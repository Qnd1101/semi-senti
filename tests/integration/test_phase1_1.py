"""Phase 1-1 (T-001~T-004) 통합 검증 스크립트 (PostgreSQL 기준).

검증 범위
---------
1. 필수 디렉터리 존재 여부 (`/collector`, `/engine`, `/admin`, `/db`)
2. PostgreSQL 스키마(핵심 테이블 Stocks/Financials/News/Signals) 정상 빌드
3. ``DBControl`` 의 INSERT / SELECT / UPDATE / UPSERT / DELETE / TRANSACTION 동작

DB 격리
-------
개발용 ``semisenti`` DB(public 스키마)에는 시드 실데이터가 있으므로, 본
검증은 **전용 스키마**(``test_semisenti``)로 격리해 수행한다. pytest 로 실행
하면 ``tests/conftest.py`` 가 이미 격리를 세팅하고, 스크립트로 단독 실행해도
``_ensure_test_schema()`` 가 동일하게 격리를 보장한다. 따라서 STEP 2 의
``init_database(force=True)`` 가 public 데이터를 건드리지 않는다.

실행 방법
---------
(가상환경 활성화 후)

    # 1) 스크립트로 직접 실행 (권장)
    python tests/integration/test_phase1_1.py

    # 2) 옵션 사용
    python tests/integration/test_phase1_1.py --keep-schema    # 테스트 스키마 보존(기본)
    python tests/integration/test_phase1_1.py --purge-schema   # 테스트 스키마 삭제

    # 3) unittest / pytest 디스커버리도 호환
    python -m pytest tests/integration/test_phase1_1.py -v

설계 원칙
---------
- 모든 단계는 ``TestReporter`` 에 누적 기록되며, 한 단계가 실패해도 가능한
  다음 단계까지 수행하여 풀 리포트를 출력한다.
- 더미 데이터는 ``stock_code`` 의 ``TST_`` prefix 로만 사용하여, 정리 시
  ``WHERE stock_code LIKE 'TST_%'`` 일괄 삭제로 안전 정리한다.
- 에러는 ``DBControlError`` / ``OSError`` 등 가능한 모든 예외를 try/except 로
  포착하여 ``[FAIL]`` 로 보고한다.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple
from urllib.parse import quote

# ---------------------------------------------------------------------------
# 0. 경로 부트스트랩
#    `pip install -e .` 이전에도 스크립트가 단독 실행될 수 있도록 src 를
#    sys.path 에 보정한다.
# ---------------------------------------------------------------------------
# 본 파일 위치: <root>/tests/integration/test_phase1_1.py
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
SRC_PATH: Path = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# 테스트 격리 스키마 (conftest 와 동일)
TEST_SCHEMA: str = "test_semisenti"
_DEFAULT_URL: str = "postgresql://semisenti:semisenti@localhost:5432/semisenti"


def _ensure_test_schema() -> None:
    """전용 스키마를 만들고 DATABASE_URL 이 그 스키마를 바라보게 한다.

    conftest 가 이미 세팅한 경우에도 멱등하게 재적용된다. 단독 실행 시
    개발용 public 스키마를 보호하기 위한 안전장치다.
    """
    import psycopg2

    base = os.environ.get("DATABASE_URL", _DEFAULT_URL).split("?options=", 1)[0]
    conn = psycopg2.connect(base, connect_timeout=5)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{TEST_SCHEMA}"')
    conn.close()

    sep = "&" if "?" in base else "?"
    opt = quote(f"-c search_path={TEST_SCHEMA}", safe="")
    os.environ["DATABASE_URL"] = f"{base}{sep}options={opt}"


from semi_senti.db import DBControl, init_database  # noqa: E402 (sys.path 보정 후)
from semi_senti.db.control import DBControlError, _mask_dsn  # noqa: E402
from semi_senti.db.init_db import DatabaseInitError  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 상수
# ---------------------------------------------------------------------------
REQUIRED_MODULE_DIRS: Tuple[str, ...] = (
    "collector",
    "engine",
    "admin",
    "db",
)

REQUIRED_TABLES: Tuple[str, ...] = ("stocks", "financials", "news", "signals")

# 더미 데이터 식별 prefix (cleanup 안전성을 위해)
TEST_PREFIX: str = "TST_"


# ---------------------------------------------------------------------------
# 2. 컬러 출력 유틸 (Windows PowerShell 안전 폴백 포함)
# ---------------------------------------------------------------------------
class _Color:
    GREEN = ""
    RED = ""
    YELLOW = ""
    CYAN = ""
    BOLD = ""
    RESET = ""


def _try_enable_color() -> None:
    """colorama 가 있으면 ANSI 색상 활성화. 없으면 무색으로 동작."""
    try:
        import colorama  # type: ignore

        colorama.just_fix_windows_console()
        _Color.GREEN = "\033[32m"
        _Color.RED = "\033[31m"
        _Color.YELLOW = "\033[33m"
        _Color.CYAN = "\033[36m"
        _Color.BOLD = "\033[1m"
        _Color.RESET = "\033[0m"
    except Exception:  # pragma: no cover
        # 색상은 비필수 - 미설치 시 무색으로 처리.
        pass


# ---------------------------------------------------------------------------
# 3. 결과 리포터
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str = ""


@dataclass
class TestReporter:
    results: List[CheckResult] = field(default_factory=list)

    def record(self, name: str, ok: bool, message: str = "") -> bool:
        self.results.append(CheckResult(name=name, ok=ok, message=message))
        tag = (
            f"{_Color.GREEN}[SUCCESS]{_Color.RESET}"
            if ok
            else f"{_Color.RED}[FAIL]{_Color.RESET}"
        )
        line = f"  {tag} {name}"
        if message:
            line += f" - {message}"
        print(line)
        return ok

    def fail(self, name: str, exc: BaseException) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        self.record(name, ok=False, message=msg)
        # 디버깅 편의 - traceback 은 들여쓰기해서 출력
        for line in traceback.format_exc().splitlines():
            print(f"      | {line}")

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if not r.ok)

    @property
    def total(self) -> int:
        return len(self.results)

    def print_summary(self) -> None:
        bar = "=" * 60
        color = _Color.GREEN if self.failed_count == 0 else _Color.RED
        status = "PASSED" if self.failed_count == 0 else "FAILED"
        print()
        print(bar)
        print(
            f" {_Color.BOLD}RESULT:{_Color.RESET} "
            f"{color}{self.passed_count} / {self.total} {status}{_Color.RESET}"
        )
        print(bar)
        if self.failed_count:
            print()
            print(f"{_Color.RED}실패 항목:{_Color.RESET}")
            for r in self.results:
                if not r.ok:
                    print(f"  - {r.name}: {r.message}")


# ---------------------------------------------------------------------------
# 4. 검증 단계
# ---------------------------------------------------------------------------
def _print_header(step_no: int, step_total: int, title: str) -> None:
    print()
    print(f"{_Color.CYAN}[STEP {step_no}/{step_total}] {title}{_Color.RESET}")
    print("-" * 60)


def step_check_directories(reporter: TestReporter) -> None:
    """STEP 1: 필수 디렉터리 4종 + DB 저장 디렉터리 보장."""
    _print_header(1, 5, "필수 디렉터리 존재 여부 확인 (os.path.isdir)")

    # 사용자가 명시한 `/collector` 등 모듈 폴더는 src layout 의
    # `src/semi_senti/<name>/` 위치에 있다. 양쪽 후보를 모두 탐색하여
    # 둘 중 하나라도 존재하면 통과로 인정한다.
    for sub in REQUIRED_MODULE_DIRS:
        candidates = [
            PROJECT_ROOT / "src" / "semi_senti" / sub,
            PROJECT_ROOT / sub,  # 루트 직속 폴더가 별도 운용될 가능성
        ]
        found = next((p for p in candidates if os.path.isdir(str(p))), None)
        if found is not None:
            reporter.record(
                f"디렉터리 존재: /{sub}",
                ok=True,
                message=str(found.relative_to(PROJECT_ROOT)),
            )
        else:
            reporter.record(
                f"디렉터리 존재: /{sub}",
                ok=False,
                message=f"후보 경로 모두 부재 → {[str(c) for c in candidates]}",
            )

    # DB 스크립트/자산 디렉터리 (없으면 생성)
    db_dir = PROJECT_ROOT / "db"
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        reporter.record(
            "DB 저장 디렉터리 보장: /db",
            ok=os.path.isdir(str(db_dir)),
            message=str(db_dir.relative_to(PROJECT_ROOT)),
        )
    except OSError as exc:
        reporter.fail("DB 저장 디렉터리 보장: /db", exc)


def step_init_db(reporter: TestReporter) -> bool:
    """STEP 2: 테스트 스키마 초기화. 실패하면 이후 단계 의미 없으므로 False 반환."""
    _print_header(2, 5, "PostgreSQL 스키마 생성 (init_database, force=True)")

    try:
        created = init_database(force=True)
    except (DatabaseInitError, Exception) as exc:  # pragma: no cover - 연결 실패 등
        reporter.fail("init_database() 호출", exc)
        return False

    reporter.record(
        "init_database() 호출",
        ok=isinstance(created, str) and created.startswith("postgresql://"),
        message=f"force=True 로 재생성 → {created}",
    )

    # 핵심 테이블이 실제로 생성됐는지 information_schema 로 확인.
    try:
        with DBControl() as db:
            existing = set(db.list_tables())
        ok = all(t in existing for t in REQUIRED_TABLES)
        reporter.record(
            "핵심 테이블 생성 확인",
            ok=ok,
            message=f"tables={sorted(existing)}",
        )
        return ok
    except DBControlError as exc:
        reporter.fail("핵심 테이블 생성 확인", exc)
        return False


def step_verify_tables(reporter: TestReporter) -> None:
    """STEP 3: information_schema 로 스키마 검증 + CHECK 제약 동작 확인."""
    _print_header(3, 5, "4개 핵심 테이블 스키마 검증")

    try:
        with DBControl() as db:
            existing = set(db.list_tables())
    except DBControlError as exc:
        reporter.fail("테이블 목록 조회", exc)
        return

    for table in REQUIRED_TABLES:
        if table not in existing:
            reporter.record(f"테이블 존재: {table}", ok=False, message="not found")
            continue

        try:
            with DBControl() as db:
                cols = db.fetch_all(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (table,),
                )
            col_names = [c["column_name"] for c in cols]
        except DBControlError as exc:
            reporter.fail(f"테이블 컬럼 조회: {table}", exc)
            continue

        reporter.record(
            f"테이블 존재: {table}",
            ok=len(col_names) > 0,
            message=f"{len(col_names)} columns: {', '.join(col_names[:4])}...",
        )

    # 추가: signals 의 CHECK 제약(BUY/SELL/HOLD) 동작 확인
    try:
        with DBControl() as db:
            db.insert("stocks", {"stock_code": TEST_PREFIX + "CHK", "name": "SCHEMA_CHECK_DUMMY"})
            try:
                db.insert(
                    "signals",
                    {
                        "stock_code": TEST_PREFIX + "CHK",
                        "signal_type": "INVALID_TYPE",
                        "price": 1000,
                        "signaled_at": "2026-05-16T00:00:00+09:00",
                    },
                )
                # 여기 도달하면 CHECK 제약이 동작하지 않은 것.
                reporter.record(
                    "CHECK 제약: signals.signal_type",
                    ok=False,
                    message="INVALID_TYPE 이 거부되지 않음",
                )
            except DBControlError:
                reporter.record(
                    "CHECK 제약: signals.signal_type",
                    ok=True,
                    message="BUY/SELL/HOLD 외 값 거부 OK",
                )
            finally:
                db.delete("stocks", where="stock_code = ?", where_params=(TEST_PREFIX + "CHK",))
    except DBControlError as exc:
        reporter.fail("CHECK 제약: signals.signal_type", exc)


def step_crud(reporter: TestReporter) -> None:
    """STEP 4: DBControl CRUD 일관성 검증."""
    _print_header(4, 5, "DBControl CRUD 동작 검증")

    try:
        db = DBControl()
    except Exception as exc:  # pragma: no cover
        reporter.fail("DBControl 인스턴스 생성", exc)
        return

    try:
        with db:
            # ---------- INSERT (stocks) ----------
            try:
                rowid = db.insert(
                    "stocks",
                    {
                        "stock_code": TEST_PREFIX + "001",
                        "name": "테스트반도체",
                        "market": "KOSPI",
                    },
                )
                reporter.record(
                    "INSERT: stocks",
                    ok=isinstance(rowid, int),
                    message=f"row inserted (stock_code={TEST_PREFIX + '001'})",
                )
            except DBControlError as exc:
                reporter.fail("INSERT: stocks", exc)

            # ---------- INSERT MANY (stocks) ----------
            try:
                affected = db.insert_many(
                    "stocks",
                    [
                        {"stock_code": TEST_PREFIX + "002", "name": "테스트메모리"},
                        {"stock_code": TEST_PREFIX + "003", "name": "테스트파운드리"},
                    ],
                )
                reporter.record(
                    "INSERT MANY: stocks",
                    ok=affected == 2,
                    message=f"affected={affected}",
                )
            except DBControlError as exc:
                reporter.fail("INSERT MANY: stocks", exc)

            # ---------- SELECT (fetch_one) ----------
            try:
                row = db.fetch_one(
                    "SELECT stock_code, name, market, is_active "
                    "FROM stocks WHERE stock_code = ?",
                    (TEST_PREFIX + "001",),
                )
                ok = row is not None and row["name"] == "테스트반도체"
                reporter.record(
                    "SELECT: fetch_one",
                    ok=ok,
                    message=str(row),
                )
            except DBControlError as exc:
                reporter.fail("SELECT: fetch_one", exc)

            # ---------- SELECT (fetch_all) ----------
            try:
                rows = db.fetch_all(
                    "SELECT stock_code FROM stocks WHERE stock_code LIKE ? "
                    "ORDER BY stock_code",
                    (TEST_PREFIX + "%",),
                )
                reporter.record(
                    "SELECT: fetch_all",
                    ok=len(rows) >= 3,
                    message=f"count={len(rows)}",
                )
            except DBControlError as exc:
                reporter.fail("SELECT: fetch_all", exc)

            # ---------- UPDATE ----------
            try:
                changed = db.update(
                    "stocks",
                    {"name": "테스트반도체(수정)"},
                    where="stock_code = ?",
                    where_params=(TEST_PREFIX + "001",),
                )
                row = db.fetch_one(
                    "SELECT name FROM stocks WHERE stock_code = ?",
                    (TEST_PREFIX + "001",),
                )
                ok = (
                    changed == 1
                    and row is not None
                    and row["name"] == "테스트반도체(수정)"
                )
                reporter.record(
                    "UPDATE: stocks",
                    ok=ok,
                    message=f"changed={changed}, name={row and row['name']}",
                )
            except DBControlError as exc:
                reporter.fail("UPDATE: stocks", exc)

            # ---------- UPSERT ----------
            try:
                db.upsert(
                    "stocks",
                    {
                        "stock_code": TEST_PREFIX + "001",
                        "name": "테스트반도체(업서트)",
                        "market": "KOSDAQ",
                    },
                    conflict_columns=["stock_code"],
                )
                row = db.fetch_one(
                    "SELECT market FROM stocks WHERE stock_code = ?",
                    (TEST_PREFIX + "001",),
                )
                reporter.record(
                    "UPSERT: stocks (PK 충돌 시 UPDATE)",
                    ok=row is not None and row["market"] == "KOSDAQ",
                    message=f"market={row and row['market']}",
                )
            except DBControlError as exc:
                reporter.fail("UPSERT: stocks", exc)

            # ---------- 자식 테이블 INSERT (FK 확인) ----------
            try:
                db.insert(
                    "financials",
                    {
                        "stock_code": TEST_PREFIX + "001",
                        "record_date": "2026-05-16",
                        "open_price": 100.0,
                        "high_price": 110.0,
                        "low_price": 95.0,
                        "close_price": 105.0,
                        "volume": 1234567,
                        "per": 12.3,
                        "pbr": 1.4,
                        "eps": 8500.0,
                    },
                )
                db.insert(
                    "news",
                    {
                        "stock_code": TEST_PREFIX + "001",
                        "title": "테스트용 뉴스 헤드라인",
                        "summary": "통합 테스트용 더미 기사",
                        "cleaned_text": "본문 정제 후 결과",
                        "source": "integration_test",
                        "url": "https://example.test/news/1",
                        "published_at": "2026-05-16T09:00:00+09:00",
                    },
                )
                db.insert(
                    "signals",
                    {
                        "stock_code": TEST_PREFIX + "001",
                        "signal_type": "BUY",
                        "price": 105.0,
                        "band_low": 110.0,
                        "band_high": 130.0,
                        "sentiment_score": -75.0,
                        "rationale": "현재가<밴드하단 & 감성=-75",
                        "signaled_at": "2026-05-16T10:00:00+09:00",
                    },
                )
                reporter.record(
                    "INSERT: financials/news/signals (FK 정상)",
                    ok=True,
                    message="외래키 정상 적재",
                )
            except DBControlError as exc:
                reporter.fail("INSERT: financials/news/signals", exc)

            # ---------- TRANSACTION rollback ----------
            try:
                pre = db.fetch_one(
                    "SELECT name FROM stocks WHERE stock_code = ?",
                    (TEST_PREFIX + "001",),
                )
                try:
                    with db.transaction() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE stocks SET name = %s WHERE stock_code = %s",
                            ("롤백되어야_할_이름", TEST_PREFIX + "001"),
                        )
                        raise RuntimeError("의도된 예외 (rollback 검증)")
                except RuntimeError:
                    pass
                post = db.fetch_one(
                    "SELECT name FROM stocks WHERE stock_code = ?",
                    (TEST_PREFIX + "001",),
                )
                ok = (
                    pre is not None
                    and post is not None
                    and pre["name"] == post["name"]
                )
                reporter.record(
                    "TRANSACTION rollback",
                    ok=ok,
                    message=f"pre={pre and pre['name']} / post={post and post['name']}",
                )
            except DBControlError as exc:
                reporter.fail("TRANSACTION rollback", exc)
    except Exception as exc:  # pragma: no cover
        reporter.fail("DBControl 컨텍스트 블록", exc)


def _count_dummy(db: DBControl, table: str) -> int:
    row = db.fetch_one(
        f"SELECT COUNT(*) AS c FROM {table} WHERE stock_code LIKE ?",
        (TEST_PREFIX + "%",),
    )
    return int(row["c"]) if row else 0


def step_cleanup(reporter: TestReporter, *, purge_schema: bool) -> None:
    """STEP 5: 더미 데이터 정리 + (옵션) 테스트 스키마 삭제."""
    _print_header(5, 5, "더미 데이터 정리 (cleanup)")

    child_counts_before = {}
    # 1) 자식 테이블 row 개수 사전 확인 (CASCADE 검증용)
    try:
        with DBControl() as db:
            for tbl in ("financials", "news", "signals"):
                child_counts_before[tbl] = _count_dummy(db, tbl)
    except DBControlError as exc:
        reporter.fail("cleanup 사전 카운트", exc)

    # 2) DBControl 을 통한 일괄 삭제 (stocks 만 지워도 FK CASCADE 로 자식도 정리)
    try:
        with DBControl() as db:
            deleted = db.delete(
                "stocks",
                where="stock_code LIKE ?",
                where_params=(TEST_PREFIX + "%",),
            )
        reporter.record(
            "DELETE: stocks WHERE stock_code LIKE 'TST_%'",
            ok=deleted >= 0,
            message=f"deleted={deleted}",
        )
    except DBControlError as exc:
        reporter.fail("DELETE: stocks (cleanup)", exc)

    # 3) CASCADE 동작 확인 - 자식 테이블에 더미가 남았는지
    try:
        with DBControl() as db:
            remaining = {tbl: _count_dummy(db, tbl) for tbl in ("financials", "news", "signals")}
        all_clean = all(v == 0 for v in remaining.values())
        reporter.record(
            "CASCADE 정리 (자식 테이블 잔존 0)",
            ok=all_clean,
            message=(
                f"before={child_counts_before}, after={remaining}"
                if child_counts_before
                else f"after={remaining}"
            ),
        )
    except DBControlError as exc:
        reporter.fail("CASCADE 정리 확인", exc)

    # 4) (옵션) 테스트 스키마 자체 삭제
    if purge_schema:
        try:
            import psycopg2

            base = os.environ.get("DATABASE_URL", _DEFAULT_URL).split("?options=", 1)[0]
            conn = psycopg2.connect(base, connect_timeout=5)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
            conn.close()
            reporter.record(
                "테스트 스키마 삭제(--purge-schema)",
                ok=True,
                message=TEST_SCHEMA,
            )
        except Exception as exc:  # pragma: no cover
            reporter.fail("테스트 스키마 삭제(--purge-schema)", exc)


# ---------------------------------------------------------------------------
# 5. 메인 진입점 (스크립트 모드)
# ---------------------------------------------------------------------------
def _print_banner() -> None:
    bar = "=" * 60
    print(bar)
    print(f"{_Color.BOLD} Semi Senti - Phase 1-1 Integration Verification{_Color.RESET}")
    print(bar)
    print(f" Project Root : {PROJECT_ROOT}")
    print(f" Target DB    : {_mask_dsn(os.environ.get('DATABASE_URL', _DEFAULT_URL))}")
    print(f" Test schema  : {TEST_SCHEMA}")
    print(f" Python       : {sys.version.split()[0]}")
    print(bar)


def run_verification(*, purge_schema: bool = False) -> int:
    """전체 검증 실행. 종료 코드(0=성공, 1=실패)를 반환한다."""
    _try_enable_color()

    # 개발용 public 스키마를 보호하기 위해 전용 스키마로 격리.
    try:
        _ensure_test_schema()
    except Exception as exc:  # pragma: no cover - 로컬 PG 미가동 등
        print(f"  {_Color.RED}[FAIL]{_Color.RESET} 테스트 스키마 준비 실패: {exc}")
        return 1

    _print_banner()

    reporter = TestReporter()
    try:
        step_check_directories(reporter)

        db_ready = step_init_db(reporter)
        if db_ready:
            step_verify_tables(reporter)
            step_crud(reporter)
            step_cleanup(reporter, purge_schema=purge_schema)
        else:
            print(
                f"  {_Color.YELLOW}[WARN]{_Color.RESET} "
                "DB 초기화 실패로 STEP 3·4·5 를 건너뜁니다."
            )
    except KeyboardInterrupt:
        print("\n[중단] 사용자에 의해 중단되었습니다.")
        reporter.print_summary()
        return 130

    reporter.print_summary()
    return 0 if reporter.failed_count == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="test_phase1_1",
        description="Phase 1-1 (T-001~T-004) 통합 검증 스크립트",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--keep-schema",
        dest="purge_schema",
        action="store_false",
        help="검증 종료 후 테스트 스키마를 보존한다 (기본값)",
    )
    group.add_argument(
        "--purge-schema",
        dest="purge_schema",
        action="store_true",
        help="검증 종료 후 테스트 스키마(test_semisenti)를 삭제한다",
    )
    parser.set_defaults(purge_schema=False)
    return parser


# ---------------------------------------------------------------------------
# 6. unittest 호환 래퍼
#    `python -m unittest tests.integration.test_phase1_1` 형태로도 실행되도록
#    얇은 TestCase 한 개를 노출한다.
# ---------------------------------------------------------------------------
class Phase11IntegrationTest(unittest.TestCase):
    """unittest / pytest 디스커버리 호환용 래퍼."""

    def test_full_phase_1_1(self) -> None:
        exit_code = run_verification(purge_schema=False)
        self.assertEqual(exit_code, 0, "Phase 1-1 통합 검증에서 실패한 항목이 있습니다.")


if __name__ == "__main__":
    args = _build_parser().parse_args()
    sys.exit(run_verification(purge_schema=args.purge_schema))
