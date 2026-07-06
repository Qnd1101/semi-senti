"""``semi_senti.db.control.DBControl`` 단위 테스트 (T-004, PostgreSQL 기준).

검증 항목
1. 컨텍스트 매니저 / connect / close 라이프사이클
2. insert / fetch_one / fetch_all 기본 CRUD
3. upsert (PRIMARY KEY 충돌 시 UPDATE) 동작
4. update / delete 의 where 강제
5. transaction 컨텍스트의 rollback
6. 잘못된 SQL 시 ``DBControlError`` 래핑

DB 격리
-------
``tests/conftest.py`` 가 전용 스키마(``test_semisenti``)로 접속을 격리하고 매
테스트 전 테이블을 비운다. ``?`` placeholder 는 ``DBControl`` 내부에서 ``%s`` 로
자동 변환되므로 그대로 사용한다.
"""

from __future__ import annotations

import unittest

from semi_senti.db import ALL_TABLES, DBControl, init_database
from semi_senti.db.control import DBControlError


class TestDBControl(unittest.TestCase):
    def setUp(self) -> None:
        init_database()

    def _db(self) -> DBControl:
        return DBControl()

    # ------------------------------------------------------------------ basic
    def test_context_manager_opens_and_closes(self) -> None:
        with self._db() as db:
            self.assertIsNotNone(db.connection)
            self.assertEqual(sorted(db.list_tables()), sorted(ALL_TABLES))
        with self.assertRaises(DBControlError):
            _ = db.connection  # close 이후 접근 → 에러

    def test_insert_and_fetch_one(self) -> None:
        with self._db() as db:
            rowid = db.insert("stocks", {"stock_code": "005930", "name": "삼성전자", "market": "KOSPI"})
            self.assertGreaterEqual(rowid, 0)
            row = db.fetch_one("SELECT * FROM stocks WHERE stock_code = ?", ("005930",))
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["name"], "삼성전자")
            self.assertEqual(row["market"], "KOSPI")
            self.assertEqual(row["is_active"], 1)

    def test_fetch_all_returns_list_of_dict(self) -> None:
        with self._db() as db:
            db.insert_many(
                "stocks",
                [
                    {"stock_code": "005930", "name": "삼성전자"},
                    {"stock_code": "000660", "name": "SK하이닉스"},
                ],
            )
            rows = db.fetch_all("SELECT stock_code, name FROM stocks ORDER BY stock_code")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["stock_code"], "000660")
            self.assertEqual(rows[1]["name"], "삼성전자")

    # ----------------------------------------------------------------- upsert
    def test_upsert_inserts_then_updates(self) -> None:
        with self._db() as db:
            db.upsert(
                "stocks",
                {"stock_code": "005930", "name": "삼성전자", "market": "KOSPI"},
                conflict_columns=["stock_code"],
            )
            db.upsert(
                "stocks",
                {"stock_code": "005930", "name": "삼성전자(우)", "market": "KOSPI"},
                conflict_columns=["stock_code"],
            )
            row = db.fetch_one("SELECT name FROM stocks WHERE stock_code = ?", ("005930",))
            assert row is not None
            self.assertEqual(row["name"], "삼성전자(우)")

    # --------------------------------------------------------------- update / delete
    def test_update_requires_where(self) -> None:
        with self._db() as db:
            with self.assertRaises(DBControlError):
                db.update("stocks", {"name": "X"}, where="")

    def test_delete_requires_where(self) -> None:
        with self._db() as db:
            with self.assertRaises(DBControlError):
                db.delete("stocks", where="")

    def test_update_and_delete_flow(self) -> None:
        with self._db() as db:
            db.insert("stocks", {"stock_code": "005930", "name": "삼성전자"})
            affected = db.update(
                "stocks",
                {"name": "Samsung Electronics"},
                where="stock_code = ?",
                where_params=("005930",),
            )
            self.assertEqual(affected, 1)

            row = db.fetch_one("SELECT name FROM stocks WHERE stock_code = ?", ("005930",))
            assert row is not None
            self.assertEqual(row["name"], "Samsung Electronics")

            deleted = db.delete("stocks", where="stock_code = ?", where_params=("005930",))
            self.assertEqual(deleted, 1)
            self.assertIsNone(
                db.fetch_one("SELECT 1 FROM stocks WHERE stock_code = ?", ("005930",))
            )

    # ----------------------------------------------------------- transaction
    def test_transaction_rollback_on_exception(self) -> None:
        with self._db() as db:
            db.insert("stocks", {"stock_code": "005930", "name": "삼성전자"})
            with self.assertRaises(RuntimeError):
                with db.transaction() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE stocks SET name = %s WHERE stock_code = %s",
                        ("CHANGED", "005930"),
                    )
                    raise RuntimeError("simulate failure")
            # rollback 으로 인해 이전 값이 유지되어야 한다.
            row = db.fetch_one("SELECT name FROM stocks WHERE stock_code = ?", ("005930",))
            assert row is not None
            self.assertEqual(row["name"], "삼성전자")

    # ----------------------------------------------------------- error wrapping
    def test_invalid_sql_raises_dbcontrolerror(self) -> None:
        with self._db() as db:
            with self.assertRaises(DBControlError):
                db.execute("SELECT * FROM nonexistent_table_xyz")


if __name__ == "__main__":
    unittest.main(verbosity=2)
