from __future__ import annotations

import argparse
import unittest
from unittest.mock import MagicMock, patch

from semi_senti.cli import _cmd_notify


class TestCliNotifySignal(unittest.TestCase):
    def test_signal_fetches_latest_signal_with_stock_param(self) -> None:
        db = MagicMock()
        db.fetch_one.return_value = {
            "signal_type": "BUY",
            "price": 1000,
            "band_low": 900,
            "band_high": 1200,
            "sentiment_score": -50,
            "signaled_at": "2026-07-06T09:00:00",
        }

        manager = MagicMock()
        manager.__enter__.return_value = manager
        manager.__exit__.return_value = None
        manager.db.return_value = db
        manager.notify_signal.return_value = MagicMock(
            success=True,
            skipped=False,
            record_id=123,
            error=None,
        )

        args = argparse.Namespace(
            kind="signal",
            stock_code="005930",
            message=None,
            threshold_pt=None,
        )

        with patch("semi_senti.notifier.NotificationManager", return_value=manager):
            rc = _cmd_notify(args)

        self.assertEqual(rc, 0)
        db.fetch_one.assert_called_once_with(
            "SELECT signal_type, price, band_low, band_high, "
            "sentiment_score, signaled_at FROM signals "
            "WHERE stock_code = %s ORDER BY signaled_at DESC LIMIT 1",
            ("005930",),
        )
        manager.notify_signal.assert_called_once()


if __name__ == "__main__":
    unittest.main()
