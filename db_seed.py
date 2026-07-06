# -*- coding: utf-8 -*-
"""Semi Senti - 데이터베이스 초기화 및 기본 종목 등록.

스키마 생성 + 삼성전자·SK하이닉스 stocks 등록만 수행한다.
재무·주가 데이터는 ``python -m semi_senti.api`` 기동 시
LiveDataPipeline(DART + pykrx 폴링)이 채운다.

CLI::

    python db_seed.py
    python db_seed.py --reset-db   # DB 파일 삭제 후 재생성
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path
from typing import Sequence

_PROJECT_ROOT = Path(__file__).resolve().parent
_SRC_DIR = _PROJECT_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from semi_senti.data.default_stocks import ensure_default_stocks_registered
    from semi_senti.db import DBControl, DBControlError, init_database
    from semi_senti.db.init_db import DatabaseInitError
except ImportError as exc:
    sys.stderr.write(
        "[FAIL] semi_senti 패키지를 import 하지 못했습니다.\n"
        f"       원인: {exc}\n"
        "       해결: pip install -r requirements.txt && pip install -e . --no-deps\n"
    )
    sys.exit(3)


_LOGGER = logging.getLogger("semi_senti.db_seed")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
        datefmt="%H:%M:%S",
    )


def _step_init_db(force_reset: bool) -> Path:
    print("[STEP 1/2] DB 초기화 (init_database) ...")
    try:
        db_path = init_database(force=force_reset)
    except DatabaseInitError as exc:
        print(f"[FAIL] DB 초기화 실패: {exc}")
        raise
    print(f"[ OK ] DB 스키마 준비 완료 → {db_path}")
    return db_path


def _step_seed_stocks(db: DBControl) -> int:
    print("[STEP 2/2] stocks 테이블 등록 (삼성전자/SK하이닉스) ...")
    try:
        ensure_default_stocks_registered(db)
        rows = db.fetch_all("SELECT stock_code, name FROM stocks ORDER BY stock_code")
        for row in rows:
            print(f"        - {row['stock_code']} {row['name']}")
    except DBControlError as exc:
        print(f"[FAIL] stocks 등록 실패: {exc}")
        raise
    print(f"[ OK ] stocks 등록 완료 ({len(rows)}종)")
    return len(rows)


def _print_summary(db: DBControl) -> None:
    print("\n=========================  SEED SUMMARY  =========================")
    try:
        stocks = db.fetch_all(
            "SELECT stock_code, name, market, is_active "
            "FROM stocks ORDER BY stock_code;"
        )
        print(f"  stocks rows : {len(stocks)}")
        for s in stocks:
            print(
                f"    - {s['stock_code']} {s['name']:<10} "
                f"market={s['market']:<6} active={s['is_active']}"
            )
        print("  financials  : (실데이터) python -m semi_senti.api 기동 시 DART+yfinance 수집")
    except DBControlError as exc:
        print(f"  [warn] 요약 조회 실패: {exc}")
    print("=====================================================================\n")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="db_seed.py",
        description="Semi Senti PostgreSQL DB 초기화 + 기본 종목 등록",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="기존 DB 파일을 삭제하고 처음부터 다시 생성한다.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="DEBUG 로그까지 출력한다.",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    _setup_logging(args.verbose)

    print("=====================================================================")
    print("  Semi Senti - Database Init (db_seed.py)")
    print("=====================================================================")
    print(f"  project root : {_PROJECT_ROOT}")
    print(f"  options      : reset-db={args.reset_db}, verbose={args.verbose}")
    print()

    try:
        db_path = _step_init_db(force_reset=args.reset_db)
    except DatabaseInitError:
        return 1
    except Exception:
        print("[FAIL] DB 초기화 중 예기치 못한 오류:")
        traceback.print_exc()
        return 3

    try:
        with DBControl(db_path=db_path) as db:
            _step_seed_stocks(db)
            _print_summary(db)
    except DBControlError:
        return 2
    except Exception:
        print("[FAIL] 종목 등록 중 예기치 못한 오류:")
        traceback.print_exc()
        return 3

    print("[DONE] DB 초기화 완료. 실데이터 수집: python -m semi_senti.api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
