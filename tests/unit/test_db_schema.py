"""``semi_senti.db.schema`` / ``init_database`` 단위 테스트 (PostgreSQL 기준).

검증 항목 (T-003):
1. 스키마의 핵심 테이블이 모두 생성된다.
2. ``init_database`` 는 멱등(idempotent) 하다 - 두 번 호출해도 에러 없음.
3. ``signals.signal_type`` 의 CHECK 제약이 동작한다.
4. 외래 키 제약이 활성화되어 동작한다.

DB 격리
-------
``tests/conftest.py`` 가 전용 스키마(``test_semisenti``)로 ``DATABASE_URL`` 을
오버라이드하고 매 테스트 전 테이블을 비운다. 따라서 본 테스트는 개발용
public 스키마를 건드리지 않는다.
"""

from __future__ import annotations

import unittest

from semi_senti.db import ALL_TABLES, DBControl, init_database
from semi_senti.db.control import DBControlError


class TestDatabaseSchema(unittest.TestCase):
    def setUp(self) -> None:
        # 테스트 스키마에 스키마를 (재)생성. conftest 가 이미 만들지만 멱등.
        init_database()

    def test_all_required_tables_created(self) -> None:
        with DBControl() as db:
            names = set(db.list_tables())
        for required in ALL_TABLES:
            self.assertIn(required, names, msg=f"필수 테이블 누락: {required}")

    def test_init_is_idempotent(self) -> None:
        try:
            init_database()
        except Exception as exc:  # pylint: disable=broad-except
            self.fail(f"init_database 2회 호출이 실패해서는 안 됩니다: {exc}")

    def test_signal_type_check_constraint(self) -> None:
        with DBControl() as db:
            db.insert("stocks", {"stock_code": "005930", "name": "삼성전자"})
            with self.assertRaises(DBControlError):
                db.insert(
                    "signals",
                    {
                        "stock_code": "005930",
                        "signal_type": "INVALID",  # CHECK 위반
                        "price": 80000,
                        "signaled_at": "2026-05-16T10:00:00+09:00",
                    },
                )

    def test_foreign_key_enforced(self) -> None:
        with DBControl() as db:
            with self.assertRaises(DBControlError):
                db.insert(
                    "news",
                    {
                        "stock_code": "NOT_EXIST",  # FK 위반 (stocks 에 없음)
                        "title": "타이틀",
                        "published_at": "2026-05-16T10:00:00+09:00",
                    },
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
