"""PostgreSQL 접근 창구 (``DBControl``).

PRD v1.2: SQLite → PostgreSQL 전환 (F-1.3, §4.2)

설계 원칙
---------
- **단일 책임**: SQL 실행/트랜잭션/연결 관리만 담당.
- **컨텍스트 매니저**: ``with DBControl() as db:`` 패턴으로 connection 누수 차단.
- **트랜잭션**: ``transaction()`` 컨텍스트로 명시적 묶음.
- **Row 반환 형태**: ``dict`` 로 통일.
- **하드코딩 금지**: 연결 URL은 ``Settings.database_url`` 에서 주입.
- **예외 일원화**: 모든 psycopg2 예외는 ``DBControlError`` 로 래핑.
- **플레이스홀더**: 내부에서 ``?`` → ``%s`` 자동 변환 (기존 쿼리 호환).
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.extensions
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "psycopg2 가 설치되지 않았습니다. `pip install psycopg2-binary` 를 실행하세요."
    ) from _e

from ..config import get_settings

_LOGGER = logging.getLogger(__name__)

ParamsType = Union[Sequence[Any], Mapping[str, Any], None]

_QMARK_RE = re.compile(r"\?")


def _to_pg_params(sql: str, params: ParamsType) -> Tuple[str, Any]:
    """``?`` 플레이스홀더를 ``%s`` 로 변환한다.

    Named dict params(``{:name}``) 는 psycopg2 ``%(name)s`` 로 변환.
    이미 ``%s`` 형식이면 그대로 통과.
    """
    if params is None:
        return sql, ()

    if isinstance(params, Mapping):
        pg_sql = re.sub(r":(\w+)", r"%(\1)s", sql)
        return pg_sql, params

    count = sql.count("?")
    if count > 0:
        pg_sql = _QMARK_RE.sub("%s", sql)
        return pg_sql, tuple(params)

    return sql, tuple(params)


class DBControlError(RuntimeError):
    """``DBControl`` 내부 SQL 오류를 일원화한 도메인 예외."""


class DBControl:
    """PostgreSQL CRUD 공통 인터페이스.

    Parameters
    ----------
    db_url:
        명시되지 않으면 ``Settings.database_url`` 을 사용한다.
    connect_timeout:
        연결 타임아웃(초). 미지정 시 ``Settings.db_connect_timeout``.
    """

    def __init__(
        self,
        db_url: Optional[str] = None,
        *,
        connect_timeout: Optional[int] = None,
        # 하위 호환: SQLite 경로 인자 무시
        db_path: Any = None,
        timeout: Any = None,
        enable_foreign_keys: bool = True,
    ) -> None:
        settings = get_settings()
        self._db_url: str = db_url or settings.database_url
        self._connect_timeout: int = (
            connect_timeout if connect_timeout is not None else settings.db_connect_timeout
        )
        self._conn: Optional[psycopg2.extensions.connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @property
    def db_url(self) -> str:
        return self._db_url

    @property
    def connection(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            raise DBControlError(
                "DB 연결이 열려 있지 않습니다. 'with DBControl() as db:' 형태로 사용하거나 connect() 를 호출하세요."
            )
        return self._conn

    def connect(self) -> psycopg2.extensions.connection:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            dsn = self._db_url
            if "connect_timeout" not in dsn:
                conn = psycopg2.connect(
                    dsn, connect_timeout=self._connect_timeout,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
            else:
                conn = psycopg2.connect(
                    dsn,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
            conn.autocommit = False
            self._conn = conn
            _LOGGER.debug("PostgreSQL 연결 생성: %s", _mask_dsn(self._db_url))
            return conn
        except psycopg2.Error as exc:
            raise DBControlError(f"PostgreSQL 연결 실패: {exc}") from exc

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            try:
                self._conn.close()
            except psycopg2.Error as exc:  # pragma: no cover
                _LOGGER.warning("PostgreSQL 연결 종료 중 경고: %s", exc)
            finally:
                self._conn = None

    def __enter__(self) -> "DBControl":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Transaction
    # ------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[psycopg2.extensions.connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except psycopg2.Error as rollback_exc:  # pragma: no cover
                _LOGGER.error("rollback 실패: %s", rollback_exc)
            raise

    # ------------------------------------------------------------------
    # Low-level execute
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: ParamsType = None) -> psycopg2.extensions.cursor:
        conn = self.connect()
        pg_sql, pg_params = _to_pg_params(sql, params)
        try:
            cur = conn.cursor()
            cur.execute(pg_sql, pg_params if pg_params else None)
            conn.commit()
            return cur
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(
                f"SQL 실행 실패: {sql.strip().splitlines()[0]} ({exc})"
            ) from exc

    def executemany(self, sql: str, seq_of_params: Iterable[ParamsType]) -> psycopg2.extensions.cursor:
        conn = self.connect()
        rows = list(seq_of_params)
        if not rows:
            cur = conn.cursor()
            return cur
        pg_sql, _ = _to_pg_params(sql, rows[0])
        pg_rows = [_to_pg_params(sql, r)[1] for r in rows]
        try:
            cur = conn.cursor()
            psycopg2.extras.execute_batch(cur, pg_sql, pg_rows)
            conn.commit()
            return cur
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(f"executemany 실패: {sql.strip().splitlines()[0]} ({exc})") from exc

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def fetch_one(self, sql: str, params: ParamsType = None) -> Optional[Dict[str, Any]]:
        conn = self.connect()
        pg_sql, pg_params = _to_pg_params(sql, params)
        try:
            cur = conn.cursor()
            cur.execute(pg_sql, pg_params if pg_params else None)
            row = cur.fetchone()
            return dict(row) if row is not None else None
        except psycopg2.Error as exc:
            raise DBControlError(f"fetch_one 실패: {exc}") from exc

    def fetch_all(self, sql: str, params: ParamsType = None) -> List[Dict[str, Any]]:
        conn = self.connect()
        pg_sql, pg_params = _to_pg_params(sql, params)
        try:
            cur = conn.cursor()
            cur.execute(pg_sql, pg_params if pg_params else None)
            return [dict(row) for row in cur.fetchall()]
        except psycopg2.Error as exc:
            raise DBControlError(f"fetch_all 실패: {exc}") from exc

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------
    def insert(self, table: str, data: Mapping[str, Any]) -> int:
        if not data:
            raise DBControlError("insert 데이터가 비어 있습니다.")
        cols = list(data.keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        ph_sql = ", ".join("%s" for _ in cols)
        values = tuple(data[c] for c in cols)
        base_sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph_sql})'
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(base_sql + " RETURNING id", values)
            row = cur.fetchone()
            conn.commit()
            return int(row["id"]) if row and "id" in row else 0
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            # id 컬럼이 없는 테이블(예: stocks, PK=stock_code)은 RETURNING id 가
            # UndefinedColumn(42703) 을 낸다. 이 경우 RETURNING 없이 재실행한다.
            if getattr(exc, "pgcode", None) == "42703":
                try:
                    cur = conn.cursor()
                    cur.execute(base_sql, values)
                    conn.commit()
                    return 0
                except psycopg2.Error as exc2:
                    try:
                        conn.rollback()
                    except psycopg2.Error:  # pragma: no cover
                        pass
                    raise DBControlError(f"insert 실패: {exc2}") from exc2
            raise DBControlError(f"insert 실패: {exc}") from exc

    def insert_many(self, table: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        first = rows[0]
        cols = list(first.keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        ph_sql = ", ".join("%s" for _ in cols)
        sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph_sql})'
        pg_rows = [tuple(r[c] for c in cols) for r in rows]
        conn = self.connect()
        try:
            cur = conn.cursor()
            psycopg2.extras.execute_batch(cur, sql, pg_rows)
            conn.commit()
            # execute_batch 는 여러 문장으로 나눠 실행하므로 cur.rowcount 가
            # 마지막 배치 수만 담아 신뢰할 수 없다. 성공 시 전체 행이 삽입되므로
            # 실제 삽입 시도 행 수를 그대로 돌려준다.
            return len(pg_rows)
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(f"insert_many 실패: {exc}") from exc

    def upsert(
        self,
        table: str,
        data: Mapping[str, Any],
        conflict_columns: Sequence[str],
        update_columns: Optional[Sequence[str]] = None,
    ) -> int:
        if not data:
            raise DBControlError("upsert 데이터가 비어 있습니다.")
        if not conflict_columns:
            raise DBControlError("upsert 의 conflict_columns 가 비어 있습니다.")

        cols = list(data.keys())
        col_sql = ", ".join(f'"{c}"' for c in cols)
        ph_sql = ", ".join("%s" for _ in cols)
        values = tuple(data[c] for c in cols)

        upd_cols = list(update_columns) if update_columns else [
            c for c in cols if c not in conflict_columns
        ]

        sql = f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph_sql})'
        if upd_cols:
            conflict_part = ", ".join(f'"{c}"' for c in conflict_columns)
            update_part = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in upd_cols)
            sql += f" ON CONFLICT ({conflict_part}) DO UPDATE SET {update_part}"
        else:
            conflict_part = ", ".join(f'"{c}"' for c in conflict_columns)
            sql += f" ON CONFLICT ({conflict_part}) DO NOTHING"

        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, values)
            conn.commit()
            return cur.rowcount or 0
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(f"upsert 실패: {exc}") from exc

    def update(
        self,
        table: str,
        data: Mapping[str, Any],
        where: str,
        where_params: ParamsType = None,
    ) -> int:
        if not data:
            raise DBControlError("update 데이터가 비어 있습니다.")
        if not where.strip():
            raise DBControlError("update 시 where 절은 필수입니다.")

        cols = list(data.keys())
        set_clause = ", ".join(f'"{c}" = %s' for c in cols)
        values: Tuple[Any, ...] = tuple(data[c] for c in cols)

        where_pg, where_vals = _to_pg_params(where, where_params)
        sql = f'UPDATE "{table}" SET {set_clause} WHERE {where_pg}'

        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, values + (where_vals if isinstance(where_vals, tuple) else tuple(where_vals)))
            conn.commit()
            return cur.rowcount or 0
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(f"update 실패: {exc}") from exc

    def delete(self, table: str, where: str, where_params: ParamsType = None) -> int:
        if not where.strip():
            raise DBControlError("delete 시 where 절은 필수입니다.")
        where_pg, where_vals = _to_pg_params(where, where_params)
        sql = f'DELETE FROM "{table}" WHERE {where_pg}'
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, where_vals if where_vals else None)
            conn.commit()
            return cur.rowcount or 0
        except psycopg2.Error as exc:
            try:
                conn.rollback()
            except psycopg2.Error:  # pragma: no cover
                pass
            raise DBControlError(f"delete 실패: {exc}") from exc

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------
    def table_exists(self, table: str) -> bool:
        row = self.fetch_one(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return row is not None

    def list_tables(self) -> List[str]:
        rows = self.fetch_all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() ORDER BY table_name"
        )
        return [r["table_name"] for r in rows]

    # ------------------------------------------------------------------
    # 하위 호환 — SQLite 시절 속성
    # ------------------------------------------------------------------
    @property
    def db_path(self):  # pragma: no cover
        """SQLite 시절 호환 속성. database_url 반환."""
        return self._db_url


# ------------------------------------------------------------------
# Internal utilities
# ------------------------------------------------------------------
def _mask_dsn(dsn: str) -> str:
    """DSN 내 패스워드를 마스킹해 로그에 노출하지 않는다."""
    return re.sub(r"(:)[^:@]+(@)", r"\1***\2", dsn)
