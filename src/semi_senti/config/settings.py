"""환경 변수 기반 런타임 설정.

- ``.env`` 파일이 존재하면 자동으로 로드한다(있을 때만, 없으면 무시).
- 모든 외부 의존 값(API 키·DB 경로·TTL 등)은 환경 변수로만 노출한다.
- Python 3.8+ 호환을 위해 ``dataclass`` 의 ``slots`` 옵션은 사용하지 않는다.
- 어떤 모듈도 본 모듈을 거치지 않고 ``os.environ`` 을 직접 읽지 않도록 한다.

PRD v1.2 변경사항
-----------------
- SQLite → PostgreSQL (``DATABASE_URL``)
- yfinance → pykrx
- Gemini API 연동 (``GEMINI_API_KEY``)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

try:
    # python-dotenv 는 선택 의존성으로 다룬다 (테스트 환경 등에서 미설치 가능).
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 환경에 따른 분기
    load_dotenv = None  # type: ignore[assignment]


_LOGGER = logging.getLogger(__name__)

# 프로젝트 루트 (.../semi-senti/) 를 가리킨다.
# 본 파일 위치: <root>/src/semi_senti/config/settings.py
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE: Path = PROJECT_ROOT / ".env"


def _load_env_file(env_file: Optional[Path] = None) -> None:
    """``.env`` 파일을 로드한다. 미존재 또는 라이브러리 부재 시 조용히 건너뛴다."""
    if load_dotenv is None:
        _LOGGER.debug("python-dotenv 미설치로 .env 로딩을 건너뜁니다.")
        return
    target = env_file or DEFAULT_ENV_FILE
    try:
        if target.is_file():
            load_dotenv(dotenv_path=target, override=False)
            _LOGGER.debug("환경 변수 파일 로드 완료: %s", target)
    except OSError as exc:  # pragma: no cover - 파일시스템 권한 등
        _LOGGER.warning("환경 변수 파일 로드 실패(%s): %s", target, exc)


def _env_str(key: str, default: str) -> str:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        _LOGGER.warning("환경 변수 %s 값을 int 로 변환할 수 없습니다: %r → 기본값 %d 사용", key, raw, default)
        return default


def _years_ago_yyyymmdd(years: int, *, today: Optional[date] = None) -> str:
    """오늘로부터 ``years`` 년 전 날짜를 'YYYYMMDD' 로 반환한다(2/29 방어)."""
    ref = today or date.today()
    try:
        start = ref.replace(year=ref.year - years)
    except ValueError:  # 윤년 2/29 → 2/28
        start = ref.replace(year=ref.year - years, day=28)
    return start.strftime("%Y%m%d")


def _default_pykrx_date_from() -> str:
    """주가 수집 시작일(YYYYMMDD).

    우선순위:
    1. ``PYKRX_DATE_FROM`` 이 명시되면 그 값(고정 시작일).
    2. 아니면 **현재 날짜 기준 ``DATA_COLLECTION_YEARS`` 년 전**(기본 2년).
    """
    explicit = os.getenv("PYKRX_DATE_FROM")
    if explicit not in (None, ""):
        return explicit
    return _years_ago_yyyymmdd(_env_int("DATA_COLLECTION_YEARS", 2))


@dataclass(frozen=True)
class Settings:
    """애플리케이션 전역 설정 컨테이너 (불변)."""

    # ----- 실행 환경 ---------------------------------------------------------
    app_env: str = field(default_factory=lambda: _env_str("SEMI_SENTI_ENV", "local"))
    log_level: str = field(default_factory=lambda: _env_str("LOG_LEVEL", "INFO"))

    # ----- PostgreSQL (F-1.3, PRD §4.2) -------------------------------------
    database_url: str = field(
        default_factory=lambda: _env_str(
            "DATABASE_URL", "postgresql://localhost:5432/semisenti"
        )
    )
    db_pool_min: int = field(default_factory=lambda: _env_int("DB_POOL_MIN", 1))
    db_pool_max: int = field(default_factory=lambda: _env_int("DB_POOL_MAX", 10))
    db_connect_timeout: int = field(default_factory=lambda: _env_int("DB_CONNECT_TIMEOUT", 10))

    # ----- 캐시 TTL (F-1.3.2) -----------------------------------------------
    news_cache_ttl_minutes: int = field(default_factory=lambda: _env_int("NEWS_CACHE_TTL_MINUTES", 30))
    financial_cache_ttl_hours: int = field(default_factory=lambda: _env_int("FINANCIAL_CACHE_TTL_HOURS", 24))
    price_cache_ttl_minutes: int = field(default_factory=lambda: _env_int("PRICE_CACHE_TTL_MINUTES", 15))
    price_poll_interval_seconds: int = field(
        default_factory=lambda: _env_int("PRICE_POLL_INTERVAL_SECONDS", 60)
    )
    live_data_enabled: bool = field(
        default_factory=lambda: _env_str("LIVE_DATA_ENABLED", "true").lower()
        in ("1", "true", "yes", "on")
    )

    # ----- FastAPI (HTTP API) ------------------------------------------------
    api_host: str = field(default_factory=lambda: _env_str("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: _env_int("API_PORT", 8001))

    # ----- 외부 API : DART --------------------------------------------------
    open_dart_api_key: str = field(default_factory=lambda: _env_str("OPEN_DART_API_KEY", ""))
    dart_base_url: str = field(
        default_factory=lambda: _env_str("DART_BASE_URL", "https://opendart.fss.or.kr/api")
    )
    dart_default_reprt_code: str = field(
        default_factory=lambda: _env_str("DART_DEFAULT_REPRT_CODE", "11011")
    )

    # ----- 외부 API : 네이버 뉴스 -------------------------------------------
    naver_client_id: str = field(default_factory=lambda: _env_str("NAVER_CLIENT_ID", ""))
    naver_client_secret: str = field(default_factory=lambda: _env_str("NAVER_CLIENT_SECRET", ""))
    naver_news_base_url: str = field(
        default_factory=lambda: _env_str(
            "NAVER_NEWS_BASE_URL", "https://openapi.naver.com/v1/search/news.json"
        )
    )
    naver_news_display: int = field(default_factory=lambda: _env_int("NAVER_NEWS_DISPLAY", 20))
    naver_news_sort: str = field(default_factory=lambda: _env_str("NAVER_NEWS_SORT", "date"))

    # ----- HTTP 공통 --------------------------------------------------------
    http_timeout_seconds: int = field(default_factory=lambda: _env_int("HTTP_TIMEOUT_SECONDS", 10))
    http_max_retries: int = field(default_factory=lambda: _env_int("HTTP_MAX_RETRIES", 3))

    # ----- 데이터 수집 기간 (F-1.1.2, PRD §4.2) ------------------------------
    # 수집 기간(년). 미지정 시 2년. ``pykrx_date_from`` 의 동적 기본값에 사용.
    data_collection_years: int = field(
        default_factory=lambda: _env_int("DATA_COLLECTION_YEARS", 2)
    )
    # 주가 수집 시작일. PYKRX_DATE_FROM 명시 시 그 값, 아니면 오늘-N년(동적).
    pykrx_date_from: str = field(default_factory=_default_pykrx_date_from)

    # ----- Gemini API (F-3.3) -----------------------------------------------
    gemini_api_key: str = field(default_factory=lambda: _env_str("GEMINI_API_KEY", ""))
    gemini_model: str = field(
        default_factory=lambda: _env_str("GEMINI_MODEL", "gemini-1.5-flash")
    )
    gemini_timeout_seconds: int = field(
        default_factory=lambda: _env_int("GEMINI_TIMEOUT_SECONDS", 5)
    )

    # ----- 다중 관점 시그널 가중치 (F-3.2) ------------------------------------
    signal_short_buy_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_SHORT_BUY_THRESHOLD", "25"))
    )
    signal_short_sell_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_SHORT_SELL_THRESHOLD", "-25"))
    )
    signal_mid_buy_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_MID_BUY_THRESHOLD", "25"))
    )
    signal_mid_sell_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_MID_SELL_THRESHOLD", "-25"))
    )
    signal_long_buy_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_LONG_BUY_THRESHOLD", "25"))
    )
    signal_long_sell_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_LONG_SELL_THRESHOLD", "-25"))
    )

    # ----- 텔레그램 (Phase 4) -----------------------------------------------
    telegram_bot_token: str = field(default_factory=lambda: _env_str("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: _env_str("TELEGRAM_CHAT_ID", ""))

    # ----- KoNLPy / JVM -----------------------------------------------------
    konlpy_jvm_max_heap_mb: int = field(default_factory=lambda: _env_int("KONLPY_JVM_MAX_HEAP_MB", 1024))
    konlpy_tagger: str = field(default_factory=lambda: _env_str("KONLPY_TAGGER", "Okt"))

    # ----- 분석 엔진 (Phase 2) ----------------------------------------------
    sentiment_normalization_k: int = field(
        default_factory=lambda: _env_int("SENTIMENT_NORMALIZATION_K", 10)
    )
    sentiment_top_keyword_limit: int = field(
        default_factory=lambda: _env_int("SENTIMENT_TOP_KEYWORD_LIMIT", 5)
    )

    band_lookback_days: int = field(default_factory=lambda: _env_int("BAND_LOOKBACK_DAYS", 250))
    band_margin: float = field(
        default_factory=lambda: float(_env_str("BAND_MARGIN", "0.15"))
    )

    signal_sentiment_buy_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_SENTIMENT_BUY_THRESHOLD", "-70"))
    )
    signal_sentiment_sell_threshold: float = field(
        default_factory=lambda: float(_env_str("SIGNAL_SENTIMENT_SELL_THRESHOLD", "70"))
    )

    divergence_window_days: int = field(
        default_factory=lambda: _env_int("DIVERGENCE_WINDOW_DAYS", 5)
    )
    divergence_price_threshold: float = field(
        default_factory=lambda: float(_env_str("DIVERGENCE_PRICE_THRESHOLD", "2.0"))
    )
    divergence_sentiment_threshold: float = field(
        default_factory=lambda: float(_env_str("DIVERGENCE_SENTIMENT_THRESHOLD", "10.0"))
    )

    # ----- 알림 (Phase 4-1, T-041 ~ T-043) -----------------------------------
    notify_max_retries: int = field(
        default_factory=lambda: _env_int("NOTIFY_MAX_RETRIES", 3)
    )
    notify_backoff_seconds: float = field(
        default_factory=lambda: float(_env_str("NOTIFY_BACKOFF_SECONDS", "1.0"))
    )
    sentiment_shift_threshold_pt: float = field(
        default_factory=lambda: float(_env_str("SENTIMENT_SHIFT_THRESHOLD_PT", "30.0"))
    )

    # ----- 업황 사이클 (Phase 4-2, T-044 ~ T-045) ----------------------------
    cycle_inventory_target: float = field(
        default_factory=lambda: float(_env_str("CYCLE_INVENTORY_TARGET", "4.0"))
    )
    cycle_inventory_span: float = field(
        default_factory=lambda: float(_env_str("CYCLE_INVENTORY_SPAN", "2.0"))
    )
    cycle_revenue_target: float = field(
        default_factory=lambda: float(_env_str("CYCLE_REVENUE_TARGET", "0.0"))
    )
    cycle_revenue_span: float = field(
        default_factory=lambda: float(_env_str("CYCLE_REVENUE_SPAN", "20.0"))
    )
    cycle_margin_target: float = field(
        default_factory=lambda: float(_env_str("CYCLE_MARGIN_TARGET", "10.0"))
    )
    cycle_margin_span: float = field(
        default_factory=lambda: float(_env_str("CYCLE_MARGIN_SPAN", "15.0"))
    )

    @property
    def project_root(self) -> Path:
        """프로젝트 루트 경로 (읽기 전용)."""
        return PROJECT_ROOT

    @property
    def sqlite_path(self) -> Path:
        """하위 호환 속성 — PostgreSQL 전환 후 코드에서 점진적 제거 예정.

        ``DBControl(db_path=self.sqlite_path)`` 호출 시 ``db_path`` 인자는
        ``DBControl.__init__`` 에서 무시되며 ``DATABASE_URL`` 이 사용된다.
        """
        return PROJECT_ROOT / "db" / "semisenti.db"  # dummy (DBControl 내부에서 무시됨)


# 모듈 임포트 시점에 .env 를 한 번만 로드한다.
_load_env_file()


def get_settings() -> Settings:
    """현재 환경 변수 스냅샷에 기반한 ``Settings`` 인스턴스를 반환한다.

    NOTE: 호출 시점의 ``os.environ`` 을 읽으므로, 테스트에서 환경 변수를
    바꾼 뒤 본 함수를 다시 호출하면 새로운 값으로 빌드된 객체를 얻을 수 있다.
    """
    return Settings()

