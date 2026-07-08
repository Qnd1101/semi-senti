"""FastAPI 어댑터 — 메인 앱 + 라우터 (PRD v1.2).

PRD v1.2 변경사항:
- GET /api/snapshot/{code}: 종목 전체 분석 스냅샷 (F-4.1~F-4.3)
- 다중 관점 시그널 (SHORT/MID/LONG) 지원
- Gemini Reasoning 연동
- PostgreSQL 기반 DB 상태 표시
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..admin import StockAdmin, StockAdminError, SystemMonitor
from ..collector import CollectorError, PriceCollector, fetch_chart_candles, list_chart_intervals
from ..config import get_settings
from ..data.default_stocks import get_default_stock
from ..data.sector_universe import SEMICONDUCTOR_UNIVERSE
from .screener import OrderKey, ScreenerRow, SortKey, period_return, sort_screener_items
from ..db import DBControl
from ..engine import ReasoningEngine, SignalLogic
from ..pipeline import get_live_pipeline, start_live_pipeline, stop_live_pipeline

_LOGGER = logging.getLogger(__name__)


# ────────────────────────── Request / Response models ──────────────────────────


class StockCreateRequest(BaseModel):
    stock_code: str = Field(..., pattern=r"^\d{6}$")
    name: str = Field(..., min_length=1)
    market: str = Field(default="KOSPI")
    validate_with_yfinance: bool = Field(default=False, alias="validate_with_pykrx")


class StockToggleRequest(BaseModel):
    stock_code: str = Field(..., pattern=r"^\d{6}$")
    is_active: bool


class RefreshRequest(BaseModel):
    stock_code: str = Field(..., pattern=r"^\d{6}$")
    market: str = Field(default="KOSPI")
    news_query: Optional[str] = None
    run_signal: bool = True
    run_sentiment: bool = True
    run_cycle: bool = True
    run_reasoning: bool = True


# ────────────────────────── Lifespan ──────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _LOGGER.info("Semi Senti FastAPI 시작 (PostgreSQL + pykrx)")
    if settings.live_data_enabled:
        start_live_pipeline(settings)
    else:
        _LOGGER.info("LIVE_DATA_ENABLED=false — 백그라운드 수집 비활성")
    yield
    stop_live_pipeline()
    _LOGGER.info("Semi Senti FastAPI 종료")


# ────────────────────────── App ──────────────────────────

app = FastAPI(
    title="Semi Senti API",
    description="반도체 감성분석 시스템 — Python 엔진 어댑터 (PRD v1.2)",
    version="0.2.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    # 외부/LAN 접속 허용: 사설 IP(10/172.16-31/192.168)의 :3000·:3001 origin 을 반영.
    # 정규식 매칭 시 미들웨어가 실제 origin 을 그대로 반영하므로 allow_credentials 와도 호환된다.
    allow_origin_regex=(
        r"http://(localhost|127\.0\.0\.1|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
        r":(3000|3001)"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────── Health ──────────────────────────


@app.get("/health")
async def health():
    s = get_settings()
    from .. import __version__ as ver  # noqa: PLC0415
    from ..db.control import _mask_dsn
    return {
        "status": "ok",
        "version": ver,
        "db": _mask_dsn(s.database_url),
        "price_source": "pykrx",
    }


# ────────────────────────── Snapshot (F-4.1 ~ F-4.3) ──────────────────────────


@app.get("/api/snapshot/{stock_code}")
async def snapshot(stock_code: str, run_reasoning: bool = False) -> Dict[str, Any]:
    """종목 전체 분석 스냅샷.

    반환 구조:
        stock: 종목 메타
        price: 현재가·밴드
        sentiment: 최신 감성 점수 + 키워드
        signals: SHORT/MID/LONG 최신 시그널
        reasonings: 관점별 근거 (Gemini 또는 폴백)
        cycle: 업황 사이클
        chart_summary: 첫/마지막 거래일
    """
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise HTTPException(status_code=400, detail="invalid stock_code")

    import json
    try:
        with DBControl() as db:
            # --- 종목 메타
            stock_row = db.fetch_one(
                "SELECT stock_code, name, market FROM stocks WHERE stock_code = %s",
                (stock_code,),
            )
            if not stock_row:
                raise HTTPException(status_code=404, detail=f"종목 없음: {stock_code}")

            # --- 최신 종가 + 재무
            latest_fin = db.fetch_one(
                "SELECT record_date, close_price, open_price, high_price, low_price, volume, "
                "revenue, operating_profit, per, pbr, eps "
                "FROM financials WHERE stock_code = %s AND close_price IS NOT NULL "
                "ORDER BY record_date DESC LIMIT 1",
                (stock_code,),
            )

            # --- 펀더멘털 밴드 (signal 테이블 최신 값)
            band_row = db.fetch_one(
                "SELECT band_low, band_high FROM signals "
                "WHERE stock_code = %s AND band_low IS NOT NULL "
                "ORDER BY signaled_at DESC LIMIT 1",
                (stock_code,),
            )

            # --- 최신 감성 점수
            sentiment_row = db.fetch_one(
                "SELECT score, raw_score, news_count, top_keywords, score_date "
                "FROM sentiment_scores WHERE stock_code = %s "
                "ORDER BY score_date DESC LIMIT 1",
                (stock_code,),
            )

            # --- 관점별 최신 시그널
            signals: Dict[str, Any] = {}
            for persp in ("SHORT", "MID", "LONG"):
                row = db.fetch_one(
                    "SELECT perspective, signal_type, score, price, band_low, band_high, "
                    "sentiment_score, rationale, signaled_at "
                    "FROM signals WHERE stock_code = %s AND perspective = %s "
                    "ORDER BY signaled_at DESC LIMIT 1",
                    (stock_code, persp),
                )
                signals[persp.lower()] = dict(row) if row else None

            # --- 관점별 최신 근거
            reasonings: Dict[str, Any] = {}
            for persp in ("SHORT", "MID", "LONG"):
                row = db.fetch_one(
                    "SELECT reasoning, is_fallback, model_version, generated_at "
                    "FROM reasonings WHERE stock_code = %s AND perspective = %s "
                    "ORDER BY generated_at DESC LIMIT 1",
                    (stock_code, persp),
                )
                reasonings[persp.lower()] = dict(row) if row else None

            # --- 업황 사이클
            cycle_row = db.fetch_one(
                "SELECT cycle_score, phase, inventory_turnover, revenue_growth_pct, "
                "op_margin_pct, score_date "
                "FROM cycle_scores WHERE stock_code = %s "
                "ORDER BY score_date DESC LIMIT 1",
                (stock_code,),
            )

            # --- 차트 요약 (첫·마지막 거래일)
            chart_summary_row = db.fetch_one(
                "SELECT MIN(record_date) AS first_date, MAX(record_date) AS last_date, "
                "COUNT(*) AS bar_count "
                "FROM financials WHERE stock_code = %s AND close_price IS NOT NULL",
                (stock_code,),
            )

        # --- 요청 시 Gemini Reasoning 즉시 생성
        if run_reasoning:
            with SignalLogic() as sl:
                try:
                    multi = sl.detect_and_store(stock_code)
                    for d in multi.decisions:
                        signals[d.perspective.lower()] = {
                            "perspective": d.perspective,
                            "signal_type": d.signal_type,
                            "score": d.score,
                            "price": d.price,
                            "band_low": d.band_low,
                            "band_high": d.band_high,
                            "sentiment_score": d.sentiment_score,
                            "rationale": d.rationale,
                            "signaled_at": d.signaled_at,
                        }
                except Exception as exc:
                    _LOGGER.warning("시그널 즉시 산출 실패: %s", exc)

            with ReasoningEngine() as re_engine:
                for persp in ("SHORT", "MID", "LONG"):
                    sig = signals.get(persp.lower())
                    if not sig:
                        continue
                    try:
                        r = re_engine.generate(
                            stock_code=stock_code,
                            perspective=persp,
                            signal_type=sig.get("signal_type", "HOLD"),
                            score=sig.get("score", 0.0),
                            price=sig.get("price"),
                            band_low=sig.get("band_low"),
                            band_high=sig.get("band_high"),
                            sentiment_score=sig.get("sentiment_score"),
                        )
                        reasonings[persp.lower()] = {
                            "reasoning": r.reasoning,
                            "is_fallback": r.is_fallback,
                            "model_version": r.model_version,
                        }
                    except Exception as exc:
                        _LOGGER.warning("Reasoning 생성 실패 (%s/%s): %s", stock_code, persp, exc)

        # --- 감성 키워드 파싱
        top_keywords = None
        if sentiment_row and sentiment_row.get("top_keywords"):
            try:
                top_keywords = json.loads(sentiment_row["top_keywords"])
            except (json.JSONDecodeError, TypeError):
                top_keywords = None

        # --- 밴드 위치 %
        band_pos_pct: Optional[float] = None
        price_val = (latest_fin or {}).get("close_price")
        bl = (band_row or {}).get("band_low") or (signals.get("short") or {}).get("band_low")
        bh = (band_row or {}).get("band_high") or (signals.get("short") or {}).get("band_high")
        if price_val and bl and bh and (bh - bl) > 0:
            band_pos_pct = round((price_val - bl) / (bh - bl) * 100, 1)

        return {
            "stock": stock_row,
            "price": {
                "close": price_val,
                "open": (latest_fin or {}).get("open_price"),
                "high": (latest_fin or {}).get("high_price"),
                "low": (latest_fin or {}).get("low_price"),
                "volume": (latest_fin or {}).get("volume"),
                "record_date": str((latest_fin or {}).get("record_date") or ""),
                "band_low": bl,
                "band_high": bh,
                "band_pos_pct": band_pos_pct,
            },
            "financials": {
                "revenue": (latest_fin or {}).get("revenue"),
                "operating_profit": (latest_fin or {}).get("operating_profit"),
                "per": (latest_fin or {}).get("per"),
                "pbr": (latest_fin or {}).get("pbr"),
                "eps": (latest_fin or {}).get("eps"),
            },
            "sentiment": {
                "score": (sentiment_row or {}).get("score"),
                "raw_score": (sentiment_row or {}).get("raw_score"),
                "news_count": (sentiment_row or {}).get("news_count"),
                "score_date": str((sentiment_row or {}).get("score_date") or ""),
                "top_keywords": top_keywords,
            },
            "signals": signals,
            "reasonings": reasonings,
            "cycle": dict(cycle_row) if cycle_row else None,
            "chart_summary": dict(chart_summary_row) if chart_summary_row else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _LOGGER.exception("스냅샷 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ────────────────────────── Stock CRUD ──────────────────────────


@app.get("/api/stocks")
async def list_stocks(include_inactive: bool = False):
    try:
        with StockAdmin() as admin:
            stocks = admin.list_stocks(include_inactive=include_inactive)
        return stocks
    except StockAdminError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/stocks", status_code=201)
async def add_stock(req: StockCreateRequest):
    try:
        with StockAdmin() as admin:
            row = admin.add_stock(
                stock_code=req.stock_code,
                name=req.name,
                market=req.market,
                validate_with_yfinance=req.validate_with_yfinance,
            )
        return row
    except StockAdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/stocks/toggle")
async def toggle_stock(req: StockToggleRequest):
    try:
        with StockAdmin() as admin:
            admin.update_stock(stock_code=req.stock_code, is_active=req.is_active)
        return {"stock_code": req.stock_code, "is_active": req.is_active}
    except StockAdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/stocks/{stock_code}")
async def get_stock(stock_code: str):
    try:
        with DBControl() as db:
            row = db.fetch_one(
                "SELECT stock_code, name, market, is_active, created_at, updated_at "
                "FROM stocks WHERE stock_code = %s",
                (stock_code,),
            )
        if not row:
            raise HTTPException(status_code=404, detail=f"종목 없음: {stock_code}")
        return row
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


class StockUpdateRequest(BaseModel):
    name: Optional[str] = None
    market: Optional[str] = None
    is_active: Optional[bool] = None


@app.patch("/api/stocks/{stock_code}")
async def update_stock(stock_code: str, req: StockUpdateRequest):
    try:
        with StockAdmin() as admin:
            admin.update_stock(
                stock_code=stock_code,
                name=req.name,
                market=req.market,
                is_active=req.is_active,
            )
        with DBControl() as db:
            row = db.fetch_one(
                "SELECT stock_code, name, market, is_active FROM stocks WHERE stock_code = %s",
                (stock_code,),
            )
        return row
    except StockAdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/stocks/{stock_code}")
async def delete_stock(stock_code: str):
    try:
        with StockAdmin() as admin:
            admin.delete_stock(stock_code)
        return {"deleted": stock_code}
    except StockAdminError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ────────────────────────── System Monitor ──────────────────────────


@app.get("/api/system/status")
async def system_status():
    try:
        with SystemMonitor() as mon:
            report = mon.status_report()
        return {
            "generated_at": report.generated_at,
            "db": report.db_path,
            "table_counts": report.table_counts,
            "failed_notifications": report.failed_notifications,
            "stocks": [
                {
                    "stock_code": s.stock_code,
                    "name": s.name,
                    "market": s.market,
                    "is_active": s.is_active,
                    "last_price_at": s.last_price_at,
                    "last_news_at": s.last_news_at,
                    "last_signal_at": s.last_signal_at,
                    "last_sentiment_date": s.last_sentiment_date,
                    "signal_count": s.signal_count,
                    "news_count": s.news_count,
                }
                for s in report.stocks
            ],
            "warnings": report.warnings,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ────────────────────────── Chart OHLCV ──────────────────────────


@app.get("/api/news/{stock_code}")
async def get_news(stock_code: str, limit: int = 100, days: int = 30) -> Dict[str, Any]:
    """종목 뉴스 기사 목록 (감성 방향 포함)."""
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise HTTPException(status_code=400, detail="invalid stock_code")
    _sel = "SELECT title, summary, url, published_at, sentiment_score FROM news WHERE stock_code = %s "
    with DBControl() as db:
        if days and days > 0:
            rows = db.fetch_all(
                _sel + "AND published_at >= (now() - make_interval(days => %s)) "
                "ORDER BY published_at DESC",
                (stock_code, days),
            )
            if not rows:  # 해당 기간 기사가 없으면 최근 20건으로 폴백 (빈 화면 방지)
                rows = db.fetch_all(
                    _sel + "ORDER BY published_at DESC LIMIT %s", (stock_code, min(limit, 20)),
                )
        else:
            rows = db.fetch_all(
                _sel + "ORDER BY published_at DESC LIMIT %s", (stock_code, limit),
            )
    items = []
    analyzed = 0
    for r in rows:
        sc = r.get("sentiment_score")
        if sc is None:
            direction = "neutral"
        else:
            analyzed += 1
            direction = "positive" if sc > 20 else ("negative" if sc < -20 else "neutral")
        pub = r.get("published_at")
        items.append({
            "title": r.get("title") or "",
            "summary": r.get("summary") or "",
            "source": "naver_news",
            "url": r.get("url") or "",
            "published_at": pub.isoformat() if hasattr(pub, "isoformat") else (str(pub) if pub else ""),
            "sentiment_direction": direction,
            "keywords": [],
        })
    return {"stock_code": stock_code, "analyzed_count": analyzed, "items": items}


@app.get("/api/chart/intervals")
async def chart_intervals():
    return {"intervals": list_chart_intervals()}


@app.get("/api/chart/{stock_code}/candles")
async def chart_candles(
    stock_code: str,
    interval: str = "1d",
    market: str = "KOSPI",
):
    """일·주·월·년봉 OHLCV (pykrx / PostgreSQL)."""
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise HTTPException(status_code=400, detail="invalid stock_code")
    meta = get_default_stock(stock_code)
    mkt = meta.market if meta else market
    try:
        db_rows = None
        with PriceCollector() as pc:
            db_rows = pc.fetch_db_candles(stock_code)
        return fetch_chart_candles(
            stock_code,
            interval=interval,
            market=mkt,
            db_rows=db_rows if db_rows else None,
        )
    except CollectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ────────────────────────── On-demand Sync ──────────────────────────


@app.post("/api/sync/defaults")
async def sync_defaults():
    try:
        pipeline = get_live_pipeline()
        pipeline.ensure_defaults()
        with StockAdmin() as admin:
            stocks = admin.list_stocks(include_inactive=False)
        return {"ok": True, "stocks": stocks}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/sync/{stock_code}")
async def sync_stock(stock_code: str, force: bool = False):
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise HTTPException(status_code=400, detail="invalid stock_code")
    try:
        pipeline = get_live_pipeline()
        result = pipeline.sync_stock(stock_code, force=force)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ──────────────────────────── Screener (반도체 유니버스) ────────────────────────────


@app.get("/api/screener/semiconductor", response_model=list[ScreenerRow])
async def screener_semiconductor(
    sort: SortKey = "change",
    order: OrderKey = "desc",
) -> list[ScreenerRow]:
    """반도체 유니버스 15종목 스크리너.

    각 종목에 대해 financials 일봉으로 다음을 계산:
    - price: 최신 종가
    - change_pct: 전일대비 수익률(%)
    - volume: 최신 거래량
    - return_1w/1m/1y: 달력일 기준 1주·1개월·1년 수익률(%)
    - is_tracked: stocks.is_active == 1

    외부 API 호출 없이 DB 만 사용.
    """
    codes = [code for code, _ in SEMICONDUCTOR_UNIVERSE]

    placeholders = ", ".join(["%s"] * len(codes))

    try:
        with DBControl() as db:
            # 종목별 is_active 조회
            active_rows = db.fetch_all(
                f"SELECT stock_code, is_active FROM stocks WHERE stock_code IN ({placeholders})",
                tuple(codes),
            )
            is_active_map: dict[str, bool] = {
                r["stock_code"]: bool(r["is_active"]) for r in active_rows
            }

            # 종목별 최신 종가·거래량
            latest_rows = db.fetch_all(
                f"""
                SELECT DISTINCT ON (stock_code)
                    stock_code, record_date, close_price, volume
                FROM financials
                WHERE stock_code IN ({placeholders}) AND close_price IS NOT NULL
                ORDER BY stock_code, record_date DESC
                """,
                tuple(codes),
            )
            latest_map: dict[str, dict] = {r["stock_code"]: r for r in latest_rows}

            # 전일 종가 (change_pct 분모)
            prev_rows = db.fetch_all(
                f"""
                SELECT f.stock_code, f.close_price AS prev_close
                FROM financials f
                INNER JOIN (
                    SELECT stock_code, MAX(record_date) AS prev_date
                    FROM financials
                    WHERE stock_code IN ({placeholders})
                      AND close_price IS NOT NULL
                    GROUP BY stock_code
                    HAVING COUNT(*) >= 2
                ) sub ON f.stock_code = sub.stock_code
                INNER JOIN (
                    SELECT stock_code, MAX(record_date) AS latest_date
                    FROM financials
                    WHERE stock_code IN ({placeholders}) AND close_price IS NOT NULL
                    GROUP BY stock_code
                ) mx ON f.stock_code = mx.stock_code
                WHERE f.record_date = (
                    SELECT MAX(f2.record_date)
                    FROM financials f2
                    WHERE f2.stock_code = f.stock_code
                      AND f2.close_price IS NOT NULL
                      AND f2.record_date < mx.latest_date
                )
                """,
                tuple(codes) + tuple(codes),
            )
            prev_map: dict[str, float] = {
                r["stock_code"]: float(r["prev_close"]) for r in prev_rows
            }

            # 수익률 계산용 전체 이력
            history_rows = db.fetch_all(
                f"""
                SELECT stock_code, record_date, close_price
                FROM financials
                WHERE stock_code IN ({placeholders}) AND close_price IS NOT NULL
                ORDER BY stock_code, record_date
                """,
                tuple(codes),
            )

        # 종목별 이력 분리
        history_by_code: dict[str, list[dict]] = {code: [] for code in codes}
        for row in history_rows:
            code = row["stock_code"]
            if code in history_by_code:
                history_by_code[code].append(row)

        items: list[ScreenerRow] = []
        for code, name in SEMICONDUCTOR_UNIVERSE:
            latest = latest_map.get(code)
            hist = history_by_code.get(code, [])

            price: float | None = float(latest["close_price"]) if latest else None
            volume: int | None = (
                int(latest["volume"])
                if latest and latest.get("volume") is not None
                else None
            )

            prev_close = prev_map.get(code)
            change_pct: float | None = None
            if price is not None and prev_close and prev_close != 0:
                change_pct = round((price - prev_close) / prev_close * 100, 2)

            items.append(
                ScreenerRow(
                    stock_code=code,
                    name=name,
                    price=price,
                    change_pct=change_pct,
                    volume=volume,
                    return_1w=period_return(hist, 7),
                    return_1m=period_return(hist, 30),
                    return_1y=period_return(hist, 365),
                    is_tracked=is_active_map.get(code, False),
                )
            )

        return sort_screener_items(items, sort=sort, order=order)

    except Exception as exc:
        _LOGGER.exception("스크리너 조회 실패: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ────────────────────────── Manual Refresh ──────────────────────────


@app.post("/api/refresh")
async def manual_refresh(req: RefreshRequest) -> Dict[str, Any]:
    """수동 갱신: 가격·뉴스·감성·시그널·사이클·Reasoning 파이프라인 실행."""
    try:
        with SystemMonitor() as mon:
            result = mon.manual_refresh(
                stock_code=req.stock_code,
                market=req.market,
                news_query=req.news_query,
                run_signal=req.run_signal,
                run_sentiment=req.run_sentiment,
                run_cycle=req.run_cycle,
            )

        if req.run_reasoning and req.run_signal:
            try:
                with SignalLogic() as sl:
                    multi = sl.detect_and_store(req.stock_code)
                with ReasoningEngine() as re_engine:
                    reasoning_results = re_engine.generate_for_signal_result(multi)
                result["reasoning"] = {
                    k: {"is_fallback": v.is_fallback} for k, v in reasoning_results.items()
                }
            except Exception as exc:
                _LOGGER.warning("Reasoning 생성 실패: %s", exc)
                result["reasoning"] = {"error": str(exc)}

        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
