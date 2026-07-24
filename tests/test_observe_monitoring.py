import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dashboard
import main as main_module
import simulator as simulator_module
from backtester import Backtester
from config import EXECUTION
from portfolio_sync import PolymarketPositionFetcher
from simulator import PaperTradingSimulator
from time_utils import age_seconds


def response(payload, status_code=200):
    value = mock.Mock()
    value.json.return_value = payload
    value.status_code = status_code
    value.ok = status_code < 400
    if status_code >= 400:
        value.raise_for_status.side_effect = RuntimeError(f"HTTP {status_code}")
    else:
        value.raise_for_status.return_value = None
    return value


class ObserveMonitoringTests(unittest.TestCase):
    def test_activity_paginates_with_maximum_500(self):
        profiler = Backtester(activity_limit=600)
        profiler.session.get = mock.Mock(side_effect=[
            response([{"timestamp": i} for i in range(500)]),
            response([{"timestamp": i} for i in range(100)]),
        ])

        rows = profiler.fetch_activity("0x" + "1" * 40)

        self.assertEqual(len(rows), 600)
        calls = profiler.session.get.call_args_list
        self.assertEqual(calls[0].kwargs["params"]["limit"], 500)
        self.assertEqual(calls[0].kwargs["params"]["offset"], 0)
        self.assertEqual(calls[1].kwargs["params"]["limit"], 100)
        self.assertEqual(calls[1].kwargs["params"]["offset"], 500)

    def test_activity_error_is_not_an_empty_valid_history(self):
        profiler = Backtester(activity_limit=1000)
        profiler.session.get = mock.Mock(return_value=response([], 400))
        self.assertIsNone(profiler.fetch_activity("0x" + "2" * 40))

    def test_activity_never_exceeds_offset_cap(self):
        profiler = Backtester(activity_limit=10000)
        profiler.session.get = mock.Mock(
            side_effect=[
                response([{"timestamp": i} for i in range(500)])
                for _ in range(11)
            ]
        )

        rows = profiler.fetch_activity("0x" + "2" * 40)

        self.assertEqual(len(rows), 5500)
        offsets = [
            call.kwargs["params"]["offset"]
            for call in profiler.session.get.call_args_list
        ]
        self.assertEqual(max(offsets), 5000)

    def test_recent_buy_returns_source_trade_identity_and_utc(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.session.get = mock.Mock(return_value=response([
            {
                "type": "TRADE", "side": "BUY", "asset": "asset-1",
                "timestamp": 1784876400, "transactionHash": "0xtx",
                "price": 0.51, "usdcSize": 25,
            },
            {
                "type": "TRADE", "side": "BUY", "asset": "other",
                "timestamp": 1784876500, "transactionHash": "0xother",
            },
        ]))

        trade = fetcher.get_recent_buy("0x" + "3" * 40, "asset-1")

        self.assertEqual(trade["transaction_hash"], "0xtx")
        self.assertTrue(trade["source_trade_at"].endswith("+00:00"))
        self.assertEqual(trade["source_trade_price"], 0.51)
        self.assertLessEqual(
            fetcher.session.get.call_args.kwargs["params"]["limit"], 500
        )

    def test_legacy_naive_and_aware_timestamps_have_same_age(self):
        now = datetime(2026, 7, 24, 7, 30, tzinfo=timezone.utc)
        self.assertEqual(
            age_seconds("2026-07-24T07:29:30", now=now),
            age_seconds("2026-07-24T07:29:30+00:00", now=now),
        )
        self.assertEqual(age_seconds("2026-07-24T07:29:30", now=now), 30.0)

    def test_wallet_manifest_is_frozen_for_observe_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "monitored_wallets.json").write_text(json.dumps({
                "run_id": "run-current",
                "wallets": [{"address": "0xfrozen"}],
            }), encoding="utf-8")
            (data / "scan_results.json").write_text(json.dumps({
                "wallets": [{"address": "0xnew-scan"}],
            }), encoding="utf-8")
            bot = main_module.PolymarketPaperTradingBot.__new__(
                main_module.PolymarketPaperTradingBot
            )
            bot.simulator = SimpleNamespace(
                run_id="run-current", execution_mode="observe"
            )
            bot._run_wallet_scan = mock.Mock()
            with mock.patch.object(main_module, "DATA_DIR", data), \
                 mock.patch.dict(EXECUTION, {"freeze_wallets_for_run": True}):
                self.assertEqual(bot.load_monitored_from_file(), ["0xfrozen"])
                bot._maybe_auto_rescan()
                bot._run_wallet_scan.assert_not_called()

    def test_bot_health_becomes_stale_after_sixty_seconds(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
        with mock.patch.object(dashboard, "get_bot_status", return_value="running"):
            health = dashboard.get_bot_health(old)
        self.assertTrue(health["stale"])
        self.assertGreater(health["state_age_seconds"], 60)

    def test_dashboard_exposes_current_run_candidates_and_server_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.object(dashboard, "DATA_DIR", data), \
                 mock.patch.object(dashboard, "get_bot_status", return_value="running"), \
                 mock.patch.dict(EXECUTION, {"mode": "observe"}):
                sim = PaperTradingSimulator()
                sim._save_state()
                sim._journal(
                    "eligible", "passed_pretrade_checks", strategy="copy",
                    signal_id="current-signal", wallet="wallet-a",
                    info={"asset": "asset-a", "title": "Current candidate"},
                    book={
                        "best_bid": 0.49, "best_ask": 0.50,
                        "bid_size": 100, "ask_size": 100,
                    },
                    evaluation={
                        "planned_size_usdc": 5.0,
                        "entry_price": 0.50,
                        "executable_ask_vwap": 0.50,
                        "executable_bid_vwap": 0.49,
                    },
                )
                with open(data / "candidate_journal.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "journal_version": 2,
                        "run_id": "other-run",
                        "signal_id": "other-signal",
                        "decision": "eligible",
                        "reason": "passed_pretrade_checks",
                    }) + "\n")

                client = dashboard.app.test_client()
                status = client.get("/api/status").get_json()
                candidates = client.get("/api/candidates?limit=50").get_json()

                self.assertEqual(status["candidate_summary"]["total"], 1)
                self.assertEqual(status["candidate_summary"]["eligible"], 1)
                self.assertEqual(
                    status["candidate_summary"]["last_candidate"]["signal_id"],
                    "current-signal",
                )
                self.assertLess(status["state_age_seconds"], 5)
                self.assertFalse(status["bot_health"]["stale"])
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0]["signal_id"], "current-signal")
                self.assertTrue(status["state_saved_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
