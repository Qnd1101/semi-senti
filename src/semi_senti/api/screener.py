"""스크리너 순수함수 + Pydantic 모델.

- period_return: 달력일 기준 수익률(%) 계산. DB rows 와 분리된 순수함수.
- sort_screener_items: null 은 끝으로 정렬.
- ScreenerRow: API 응답 모델.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic 응답 모델
# ---------------------------------------------------------------------------


class ScreenerRow(BaseModel):
    stock_code: str
    name: str
    price: Optional[float]
    change_pct: Optional[float]
    volume: Optional[int]
    return_1w: Optional[float]
    return_1m: Optional[float]
    return_1y: Optional[float]
    is_tracked: bool


# ---------------------------------------------------------------------------
# 순수함수: 수익률 계산
# ---------------------------------------------------------------------------

SortKey = Literal["name", "change", "price", "volume", "w1", "m1", "y1"]
OrderKey = Literal["asc", "desc"]

_DAYS_MAP: Dict[str, int] = {"w1": 7, "m1": 30, "y1": 365}


def period_return(
    close_rows: List[Dict[str, Any]],
    days: int,
    base_date: Optional[datetime.date] = None,
) -> Optional[float]:
    """달력일 기준 수익률(%) 계산.

    Args:
        close_rows: financials 에서 record_date(date), close_price(float) 를 포함하는 행 목록.
                    record_date 오름차순 또는 내림차순 모두 처리.
        days:       달력일 수 (7=1주, 30=1달, 365=1년).
        base_date:  기준일(기본=최신 record_date).

    Returns:
        수익률(%) 또는 None(데이터 부족).
    """
    if not close_rows:
        return None

    # 유효 행만(None close_price 제외)
    valid = [
        r for r in close_rows
        if r.get("close_price") is not None and r.get("record_date") is not None
    ]
    if not valid:
        return None

    def to_date(val: Any) -> datetime.date:
        if isinstance(val, datetime.date):
            return val
        return datetime.date.fromisoformat(str(val))

    sorted_rows = sorted(valid, key=lambda r: to_date(r["record_date"]))

    latest_date = base_date if base_date else to_date(sorted_rows[-1]["record_date"])
    target_date = latest_date - datetime.timedelta(days=days)

    # target_date 이하 최근 종가 (과거 가격)
    past_row = None
    for r in sorted_rows:
        if to_date(r["record_date"]) <= target_date:
            past_row = r

    current_row = sorted_rows[-1]

    if past_row is None or past_row is current_row:
        return None

    current_price: float = float(current_row["close_price"])
    past_price: float = float(past_row["close_price"])

    if past_price == 0:
        return None

    return round((current_price - past_price) / past_price * 100, 2)


# ---------------------------------------------------------------------------
# 순수함수: 정렬
# ---------------------------------------------------------------------------

_FIELD_MAP: Dict[SortKey, str] = {
    "name": "name",
    "change": "change_pct",
    "price": "price",
    "volume": "volume",
    "w1": "return_1w",
    "m1": "return_1m",
    "y1": "return_1y",
}


def sort_screener_items(
    items: List[ScreenerRow],
    sort: SortKey = "change",
    order: OrderKey = "desc",
) -> List[ScreenerRow]:
    """ScreenerRow 목록을 sort/order 기준으로 정렬.

    - 숫자/문자열(종목명) 필드를 모두 지원한다.
    - ``null`` 값은 asc/desc 와 무관하게 **항상 끝**으로 보낸다.
    """
    field = _FIELD_MAP.get(sort, "change_pct")
    reverse = order == "desc"

    present = [r for r in items if getattr(r, field, None) is not None]
    missing = [r for r in items if getattr(r, field, None) is None]

    def _val(row: ScreenerRow):
        v = getattr(row, field)
        return v.lower() if isinstance(v, str) else v

    present.sort(key=_val, reverse=reverse)
    return present + missing
