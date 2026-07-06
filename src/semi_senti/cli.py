"""semi_senti CLI 진입점.

사용 예시::

    python -m semi_senti --version
    python -m semi_senti init-db
    python -m semi_senti init-db --force
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="semi_senti",
        description="Semi Senti project command line interface",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print package version and exit",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    init_parser = subparsers.add_parser(
        "init-db",
        help="Create all PostgreSQL schema tables (uses DATABASE_URL)",
    )
    init_parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="(legacy) ignored after PostgreSQL migration; DATABASE_URL is used",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Drop existing tables before initialization (DEV ONLY)",
    )

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="DB 시딩 + 기본 종목 등록 + 수집·분석 일괄 실행",
    )
    bootstrap_parser.add_argument(
        "--stocks",
        default=None,
        help="처리할 종목코드(쉼표 구분). 기본: 삼성전자·SK하이닉스",
    )
    bootstrap_parser.add_argument(
        "--skip-db-seed",
        action="store_true",
        help="db_seed.py(더미 재무) 생략, 스키마만 보장",
    )
    bootstrap_parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="외부 API 수집 생략 (DB 종목 등록만)",
    )
    bootstrap_parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="분석 엔진 실행 생략",
    )
    bootstrap_parser.add_argument(
        "--no-force",
        action="store_true",
        help="수집 시 TTL 캐시 존중 (기본은 force=True)",
    )

    # ---- collect 서브커맨드 ------------------------------------------------
    collect_parser = subparsers.add_parser(
        "collect",
        help="Run data collectors (price | news | dart)",
    )
    collect_parser.add_argument(
        "source",
        choices=("price", "news", "dart"),
        help="Which collector to run",
    )
    collect_parser.add_argument(
        "--stock-code", required=True,
        help="KRX 종목코드 (예: 005930)",
    )
    collect_parser.add_argument(
        "--stock-name", default=None,
        help="종목명 (stocks 테이블에 자동 upsert)",
    )
    collect_parser.add_argument(
        "--market", default="KOSPI",
        choices=("KOSPI", "KOSDAQ"),
        help="시장 구분 (price 에서 yahoo 심볼 매핑에 사용)",
    )
    collect_parser.add_argument(
        "--query", default=None,
        help="뉴스 검색 키워드 (news source 필수)",
    )
    collect_parser.add_argument(
        "--corp-code", default=None,
        help="DART corp_code (8자리, dart source 필수)",
    )
    collect_parser.add_argument(
        "--bsns-year", default=None,
        help="DART 사업연도 (dart source, 기본: 작년)",
    )
    collect_parser.add_argument(
        "--force", action="store_true",
        help="TTL 캐시 무시하고 강제 갱신",
    )

    # ---- analyze 서브커맨드 (Phase 2) -------------------------------------
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run analysis engines (sentiment | signal | divergence)",
    )
    analyze_parser.add_argument(
        "engine",
        choices=("sentiment", "signal", "divergence", "cycle"),
        help="Which engine to run",
    )
    analyze_parser.add_argument(
        "--stock-code", required=True,
        help="KRX 종목코드 (예: 005930)",
    )
    analyze_parser.add_argument(
        "--score-date", default=None,
        help="감성 분석 대상 일자 (sentiment, YYYY-MM-DD). 기본: 오늘",
    )
    analyze_parser.add_argument(
        "--window-days", type=int, default=None,
        help="다이버전스 윈도우 (divergence). 기본: Settings.divergence_window_days",
    )

    # ---- notify 서브커맨드 (Phase 4-1, T-041 ~ T-043) ----------------------
    notify_parser = subparsers.add_parser(
        "notify",
        help="Send a Telegram notification (signal | sentiment-shift | test)",
    )
    notify_parser.add_argument(
        "kind",
        choices=("signal", "sentiment-shift", "test"),
        help="알림 종류",
    )
    notify_parser.add_argument("--stock-code", default=None, help="KRX 종목코드")
    notify_parser.add_argument(
        "--message", default=None,
        help="test 모드에서 직접 전송할 메시지 본문",
    )
    notify_parser.add_argument(
        "--threshold-pt", type=float, default=None,
        help="sentiment-shift 임계값(pt). 기본: Settings.sentiment_shift_threshold_pt",
    )

    # ---- admin 서브커맨드 (Phase 4-3, T-046 ~ T-047) -----------------------
    admin_parser = subparsers.add_parser(
        "admin",
        help="Stock administration (list | add | update | delete | refresh | status)",
    )
    admin_parser.add_argument(
        "action",
        choices=("list", "add", "update", "delete", "refresh", "status"),
        help="관리 작업 종류",
    )
    admin_parser.add_argument("--stock-code", default=None, help="KRX 종목코드")
    admin_parser.add_argument("--stock-name", default=None, help="종목명 (add/update)")
    admin_parser.add_argument(
        "--market", default="KOSPI", choices=("KOSPI", "KOSDAQ"),
        help="시장 구분 (add/update/refresh)",
    )
    admin_parser.add_argument(
        "--no-validate", action="store_true",
        help="add 시 yfinance 유효성 검증을 생략",
    )
    admin_parser.add_argument(
        "--query", default=None,
        help="refresh 시 뉴스 검색 키워드 (생략 시 뉴스 수집 스킵)",
    )
    admin_parser.add_argument(
        "--include-inactive", action="store_true",
        help="list 시 비활성 종목 포함",
    )
    admin_parser.add_argument(
        "--soft", action="store_true",
        help="delete 를 비활성화(soft) 처리",
    )

    return parser


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    from .bootstrap import print_bootstrap_report, run_bootstrap

    codes = None
    if args.stocks:
        codes = [c.strip() for c in args.stocks.split(",") if c.strip()]

    report = run_bootstrap(
        stock_codes=codes,
        skip_db_seed=args.skip_db_seed,
        skip_collect=args.skip_collect,
        skip_analyze=args.skip_analyze,
        force=not args.no_force,
    )
    print_bootstrap_report(report)
    if report.skipped:
        return 2
    return 0 if report.ok else 1


def _cmd_init_db(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .db import init_database
    from .db.init_db import DatabaseInitError

    db_path = Path(args.db_path) if args.db_path else None
    try:
        created_path = init_database(db_path=db_path, force=args.force)
    except DatabaseInitError as exc:
        print(f"[ERROR] DB 초기화 실패: {exc}", file=sys.stderr)
        return 2
    print(f"[OK] DB 초기화 완료: {created_path}")
    return 0


def _cmd_collect(args: argparse.Namespace) -> int:
    """price / news / dart 수집기를 한 번 실행."""
    from .collector import (
        CollectorError,
        DartFinancialCollector,
        NaverNewsCollector,
        PriceCollector,
    )

    try:
        if args.source == "price":
            with PriceCollector() as pc:
                count = pc.collect_and_store(
                    stock_code=args.stock_code,
                    market=args.market,
                    stock_name=args.stock_name,
                    force=args.force,
                )
            print(f"[OK] price 적재 완료: {count} rows")
            return 0

        if args.source == "news":
            if not args.query:
                print("[ERROR] news source 는 --query 가 필수입니다.", file=sys.stderr)
                return 2
            with NaverNewsCollector() as nc:
                count = nc.collect_and_store(
                    stock_code=args.stock_code,
                    query=args.query,
                    stock_name=args.stock_name,
                    market=args.market,
                    force=args.force,
                )
            print(f"[OK] news 적재 완료: {count} new rows")
            return 0

        if args.source == "dart":
            corp_code = args.corp_code
            if not corp_code:
                from .collector.dart_corp import resolve_corp_code

                try:
                    corp_code = resolve_corp_code(args.stock_code)
                    print(f"[INFO] DART corp_code 자동 조회: {corp_code}")
                except CollectorError as exc:
                    print(f"[ERROR] {exc}", file=sys.stderr)
                    return 2
            with DartFinancialCollector() as dc:
                record = dc.collect_and_store(
                    stock_code=args.stock_code,
                    corp_code=corp_code,
                    bsns_year=args.bsns_year,
                    stock_name=args.stock_name,
                )
            print(f"[OK] dart 적재 완료: {record}")
            return 0

    except CollectorError as exc:
        print(f"[ERROR] 수집 실패: {exc}", file=sys.stderr)
        return 2

    print(f"[ERROR] 알 수 없는 source: {args.source}", file=sys.stderr)
    return 2


def _cmd_analyze(args: argparse.Namespace) -> int:
    """sentiment / signal / divergence 분석 엔진 실행."""
    from datetime import datetime

    from .engine import CycleAnalyzer, DivergenceDetector, SentimentEngine, SignalLogic

    try:
        if args.engine == "sentiment":
            score_date = args.score_date or datetime.utcnow().strftime("%Y-%m-%d")
            with SentimentEngine() as se:
                result = se.score_news_and_store(
                    stock_code=args.stock_code,
                    score_date=score_date,
                )
            print(
                f"[OK] sentiment 적재 완료: stock={args.stock_code} "
                f"date={score_date} score={result.score:.2f} news={result.news_count}"
            )
            return 0

        if args.engine == "signal":
            with SignalLogic() as sl:
                decision = sl.detect_and_store(stock_code=args.stock_code)
            print(
                f"[OK] signal 적재 완료: {decision.signal_type} | {decision.rationale}"
            )
            return 0

        if args.engine == "divergence":
            with DivergenceDetector() as dd:
                result = dd.detect(
                    stock_code=args.stock_code,
                    window_days=args.window_days,
                )
            print(
                f"[OK] divergence 결과: {result.divergence_type} | {result.note}"
            )
            return 0

        if args.engine == "cycle":
            with CycleAnalyzer() as ca:
                cycle = ca.analyze_and_store(stock_code=args.stock_code)
            print(
                f"[OK] cycle 적재 완료: stock={args.stock_code} "
                f"score={cycle.cycle_score:+.2f} phase={cycle.phase}"
            )
            return 0
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[ERROR] 분석 실패: {exc}", file=sys.stderr)
        return 2

    print(f"[ERROR] 알 수 없는 engine: {args.engine}", file=sys.stderr)
    return 2


def _cmd_notify(args: argparse.Namespace) -> int:
    """텔레그램 알림 발송 (signal | sentiment-shift | test)."""
    from .notifier import (
        NotificationManager,
        SentimentAlertWatcher,
        TelegramClient,
        TelegramSendError,
    )

    try:
        if args.kind == "test":
            if not args.message:
                print("[ERROR] test 모드는 --message 가 필수입니다.", file=sys.stderr)
                return 2
            client = TelegramClient()
            if not client.is_configured:
                print(
                    "[ERROR] TELEGRAM_BOT_TOKEN/CHAT_ID 가 .env 에 설정되어 있지 않습니다.",
                    file=sys.stderr,
                )
                return 2
            client.send_with_retry(args.message)
            print("[OK] 텔레그램 테스트 메시지 전송 완료")
            return 0

        if not args.stock_code:
            print(
                f"[ERROR] {args.kind} 모드는 --stock-code 가 필수입니다.",
                file=sys.stderr,
            )
            return 2

        if args.kind == "signal":
            with NotificationManager() as nm:
                # 가장 최근 signals row 를 메시지로 변환하여 발송.
                row = nm.db().fetch_one(
                    "SELECT signal_type, price, band_low, band_high, "
                    "sentiment_score, signaled_at FROM signals "
                    "WHERE stock_code = %s ORDER BY signaled_at DESC LIMIT 1",
                    (args.stock_code,),
                )
                if not row:
                    print(
                        f"[ERROR] {args.stock_code} 의 시그널이 없습니다.",
                        file=sys.stderr,
                    )
                    return 2
                result = nm.notify_signal(
                    stock_code=args.stock_code,
                    signal_type=row["signal_type"],
                    price=row.get("price"),
                    band_low=row.get("band_low"),
                    band_high=row.get("band_high"),
                    sentiment_score=row.get("sentiment_score"),
                    signaled_at=row.get("signaled_at"),
                )
            if result.success:
                print(f"[OK] signal 알림 발송 (id={result.record_id})")
                return 0
            if result.skipped:
                print(f"[SKIP] {result.skip_reason}")
                return 0
            print(f"[ERROR] 발송 실패: {result.error}", file=sys.stderr)
            return 2

        if args.kind == "sentiment-shift":
            kwargs = {"threshold_pt": args.threshold_pt} if args.threshold_pt else {}
            with SentimentAlertWatcher(**kwargs) as watcher:
                result = watcher.evaluate(args.stock_code)
            if result is None:
                print("[OK] 임계값 미초과 — 알림 없음")
                return 0
            if result.success:
                print(f"[OK] sentiment-shift 알림 발송 (id={result.record_id})")
                return 0
            print(f"[ERROR] 발송 실패: {result.error}", file=sys.stderr)
            return 2

    except TelegramSendError as exc:
        print(f"[ERROR] 텔레그램 전송 실패: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[ERROR] notify 실패: {exc}", file=sys.stderr)
        return 2

    print(f"[ERROR] 알 수 없는 kind: {args.kind}", file=sys.stderr)
    return 2


def _cmd_admin(args: argparse.Namespace) -> int:
    """종목 관리 + 시스템 상태 조회 + 수동 갱신."""
    from .admin import StockAdmin, StockAdminError, SystemMonitor

    try:
        if args.action == "list":
            with StockAdmin() as admin:
                rows = admin.list_stocks(include_inactive=args.include_inactive)
            if not rows:
                print("[INFO] 등록된 종목이 없습니다.")
                return 0
            print(f"{'CODE':<10} {'NAME':<20} {'MARKET':<8} ACTIVE  UPDATED")
            for r in rows:
                active = "Y" if r.get("is_active") else "N"
                print(
                    f"{r['stock_code']:<10} {str(r.get('name') or ''):<20} "
                    f"{(r.get('market') or '-'):<8} {active:<6} {r.get('updated_at') or '-'}"
                )
            return 0

        if args.action == "add":
            if not args.stock_code or not args.stock_name:
                print(
                    "[ERROR] add 는 --stock-code 와 --stock-name 이 모두 필요합니다.",
                    file=sys.stderr,
                )
                return 2
            with StockAdmin() as admin:
                row = admin.add_stock(
                    stock_code=args.stock_code,
                    name=args.stock_name,
                    market=args.market,
                    validate_with_yfinance=not args.no_validate,
                )
            print(f"[OK] 등록 완료: {row.get('name')} ({row.get('stock_code')})")
            return 0

        if args.action == "update":
            if not args.stock_code:
                print("[ERROR] update 는 --stock-code 가 필수입니다.", file=sys.stderr)
                return 2
            kwargs = {}
            if args.stock_name:
                kwargs["name"] = args.stock_name
            if args.market:
                kwargs["market"] = args.market
            if not kwargs:
                print(
                    "[ERROR] update 는 --stock-name 또는 --market 중 하나가 필요합니다.",
                    file=sys.stderr,
                )
                return 2
            with StockAdmin() as admin:
                admin.update_stock(stock_code=args.stock_code, **kwargs)
            print(f"[OK] 갱신 완료: {args.stock_code}")
            return 0

        if args.action == "delete":
            if not args.stock_code:
                print("[ERROR] delete 는 --stock-code 가 필수입니다.", file=sys.stderr)
                return 2
            with StockAdmin() as admin:
                if args.soft:
                    admin.deactivate_stock(args.stock_code)
                    print(f"[OK] 비활성화 완료: {args.stock_code}")
                else:
                    admin.delete_stock(args.stock_code, cascade=True)
                    print(f"[OK] 삭제 완료(CASCADE): {args.stock_code}")
            return 0

        if args.action == "status":
            with SystemMonitor() as monitor:
                report = monitor.status_report()
            print(f"[generated] {report.generated_at}")
            print(f"[db]        {report.db_path}")
            for table, count in report.table_counts.items():
                print(f"  {table:<20} {count}")
            print(f"[failed_notifications] {report.failed_notifications}")
            for s in report.stocks:
                print(
                    f"  - {s.name} ({s.stock_code}) | "
                    f"price={s.last_price_at or '-'} | "
                    f"signal={s.last_signal_at or '-'} ({s.signal_count}) | "
                    f"news={s.news_count}"
                )
            return 0

        if args.action == "refresh":
            if not args.stock_code:
                print("[ERROR] refresh 는 --stock-code 가 필수입니다.", file=sys.stderr)
                return 2
            with SystemMonitor() as monitor:
                result = monitor.manual_refresh(
                    stock_code=args.stock_code,
                    market=args.market,
                    news_query=args.query,
                )
            print(f"[{'OK' if result['ok'] else 'PARTIAL'}] refresh 완료")
            for step, payload in result["steps"].items():
                print(f"  - {step}: {payload}")
            if result["errors"]:
                print(f"  errors: {result['errors']}")
            return 0 if result["ok"] else 1

    except StockAdminError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pylint: disable=broad-except
        print(f"[ERROR] admin 실행 실패: {exc}", file=sys.stderr)
        return 2

    print(f"[ERROR] 알 수 없는 action: {args.action}", file=sys.stderr)
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    args = build_parser().parse_args(argv)

    if args.version:
        from . import __version__

        print(__version__)
        return 0

    if args.command == "bootstrap":
        return _cmd_bootstrap(args)
    if args.command == "init-db":
        return _cmd_init_db(args)
    if args.command == "collect":
        return _cmd_collect(args)
    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "notify":
        return _cmd_notify(args)
    if args.command == "admin":
        return _cmd_admin(args)

    print(
        "semi_senti CLI is ready. "
        "(try: `semi_senti bootstrap --help`, `init-db --help`, `collect --help`, "
        "`analyze --help`, `notify --help`, `admin --help`)"
    )
    return 0
