"""데이터 수집 기간(pykrx 시작일) 동적 기본값 단위 테스트.

`PYKRX_DATE_FROM` 미설정 시 '현재 날짜 기준 N년 전'(기본 2년)을 계산한다.
"""

from __future__ import annotations

import os
import unittest
from datetime import date

from semi_senti.config.settings import (
    _default_pykrx_date_from,
    _years_ago_yyyymmdd,
)


class TestYearsAgo(unittest.TestCase):
    def test_basic_two_years(self) -> None:
        self.assertEqual(_years_ago_yyyymmdd(2, today=date(2026, 7, 6)), "20240706")

    def test_leap_day_guarded(self) -> None:
        # 2024-02-29 의 1년 전은 2023-02-29 가 없으므로 2023-02-28 로 보정.
        self.assertEqual(_years_ago_yyyymmdd(1, today=date(2024, 2, 29)), "20230228")


class TestDefaultPykrxDateFrom(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.get(k) for k in ("PYKRX_DATE_FROM", "DATA_COLLECTION_YEARS")}

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_explicit_value_wins(self) -> None:
        os.environ["PYKRX_DATE_FROM"] = "20200101"
        self.assertEqual(_default_pykrx_date_from(), "20200101")

    def test_dynamic_default_is_years_ago(self) -> None:
        os.environ.pop("PYKRX_DATE_FROM", None)
        os.environ["DATA_COLLECTION_YEARS"] = "3"
        expected = _years_ago_yyyymmdd(3)
        self.assertEqual(_default_pykrx_date_from(), expected)

    def test_default_two_years_when_unset(self) -> None:
        os.environ.pop("PYKRX_DATE_FROM", None)
        os.environ.pop("DATA_COLLECTION_YEARS", None)
        self.assertEqual(_default_pykrx_date_from(), _years_ago_yyyymmdd(2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
