"""pytest 공용 픽스처 — 로컬 PostgreSQL 기반 테스트 격리.

전략 (전용 스키마 격리)
----------------------
코드베이스는 SQLite → PostgreSQL 로 전환됐고, ``DBControl`` / ``init_database``
는 항상 ``Settings.database_url`` 로 접속한다(``db_path`` 인자는 무시). 개발용
``semisenti`` DB(public 스키마)에는 시드된 실데이터가 들어 있으므로, 테스트가
이를 오염시키지 않도록 **같은 DB 안의 전용 스키마**(``test_semisenti``)로 격리한다.

- ``semisenti`` 롤은 CREATEDB 권한이 없으나 스키마 CREATE 권한은 있으므로,
  슈퍼유저 없이 전용 스키마를 만들 수 있다.
- 접속 DSN 에 ``options=-c search_path=<schema>`` 를 주입해 모든 연결이 테스트
  스키마를 바라보게 한다. ``get_settings()`` 는 호출 시점의 ``os.environ`` 을
  읽으므로(캐시 없음) ``DATABASE_URL`` 을 덮어쓰면 이후 생성되는 모든
  ``DBControl`` / ``init_database`` 가 자동으로 테스트 스키마를 사용한다.
- 로컬 PostgreSQL 이 없으면 DB 를 건드리는 테스트는 각자 실패하지만, 순수 로직
  테스트(형태소·감성 등)는 영향 없이 통과한다.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import pytest

_LOGGER = logging.getLogger(__name__)

TEST_SCHEMA = "test_semisenti"
_DEFAULT_URL = "postgresql://semisenti:semisenti@localhost:5432/semisenti"

# 테스트 스키마 준비 성공 여부. truncate 픽스처가 참조한다.
_DB_READY = False


def _base_url() -> str:
    """search_path 옵션을 제외한 기본 DATABASE_URL 을 돌려준다."""
    # semi_senti.config 임포트 시점에 .env 가 로드되며 DATABASE_URL 이 채워진다.
    import semi_senti.config.settings  # noqa: F401  (side effect: .env 로드)

    url = os.environ.get("DATABASE_URL") or _DEFAULT_URL
    # 이미 search_path 가 주입된 URL 이면 옵션 부분을 잘라 원본만 취한다.
    return url.split("?options=", 1)[0]


def _with_test_schema(base: str) -> str:
    """DSN 에 test 스키마 search_path 를 주입한다(공백은 %20 인코딩)."""
    sep = "&" if "?" in base else "?"
    # psycopg2 URI 는 옵션 값 내부의 공백/`=` 를 반드시 percent-encode 해야 한다
    # (공백 %20, `=` %3D). 바깥 `options=` 만 key/value 구분자로 남긴다.
    opt = quote(f"-c search_path={TEST_SCHEMA}", safe="")
    return f"{base}{sep}options={opt}"


def _all_tables() -> list[str]:
    from semi_senti.db.schema import ALL_TABLES

    return list(ALL_TABLES)


def pytest_configure(config: pytest.Config) -> None:
    """수집 전 1회: 전용 스키마 생성 + DATABASE_URL 오버라이드 + 스키마 초기화."""
    global _DB_READY

    base = _base_url()

    try:
        import psycopg2
    except ImportError:  # pragma: no cover - psycopg2 미설치 환경
        _LOGGER.warning("psycopg2 not installed; DB-backed tests will fail individually.")
        return

    try:
        admin = psycopg2.connect(base, connect_timeout=5)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"')
        admin.close()
    except psycopg2.Error as exc:
        _LOGGER.warning(
            "Local PostgreSQL not reachable (%s); pure-logic tests still run, "
            "DB-backed tests will fail individually.",
            exc,
        )
        return

    # 이후 모든 DB 접속이 테스트 스키마를 바라보도록 DATABASE_URL 을 덮어쓴다.
    os.environ["DATABASE_URL"] = _with_test_schema(base)

    # 테스트 스키마에 전체 스키마(8 테이블) 를 새로 생성한다.
    from semi_senti.db import init_database

    init_database(force=True)
    _DB_READY = True


def _truncate_all() -> None:
    """테스트 스키마의 모든 테이블을 비운다(개발 public 스키마는 무관)."""
    if not _DB_READY:
        return
    import psycopg2

    tables = ", ".join(f'"{t}"' for t in _all_tables())
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            # 앞선 테스트가 커넥션을 닫지 않아 락을 쥐고 있으면 TRUNCATE(ACCESS
            # EXCLUSIVE) 가 무한 대기한다. lock_timeout 으로 빠르게 실패시켜
            # 스위트 전체가 멈추는 것을 막는다(GC 후 다음 테스트에서 회복).
            cur.execute("SET lock_timeout = '5s'")
            cur.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        conn.close()
    except psycopg2.Error as exc:  # pragma: no cover - 방어적
        _LOGGER.warning("test-schema truncate failed: %s", exc)


@pytest.fixture(autouse=True)
def _isolate_db() -> None:
    """각 테스트 시작 전에 테스트 스키마를 초기화해 테스트 간 오염을 차단한다."""
    _truncate_all()
