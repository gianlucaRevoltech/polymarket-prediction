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
from portfolio_sync import PolymarketPositionFetcher, PositionsFetchResult
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


def raw_position(asset):
    return {
        "asset": asset,
        "size": 1,
        "avgPrice": 0.5,
        "conditionId": f"condition-{asset}",
        "title": f"Market {asset}",
    }


class ObserveMonitoringTests(unittest.TestCase):
    def test_gamma_market_normalizes_authoritative_fee_schedule(self):
        market = PolymarketPositionFetcher._normalize_market({
            "conditionId": "condition-1",
            "question": "Example market",
            "feesEnabled": True,
            "feeSchedule": {
                "rate": "0.05", "exponent": "1", "takerOnly": True,
            },
        })

        self.assertTrue(market["fees_enabled"])
        self.assertTrue(market["fee_metadata_known"])
        self.assertEqual(market["fee_schedule"]["rate"], 0.05)
        self.assertEqual(market["fee_schedule"]["exponent"], 1.0)

    def test_gamma_fee_free_market_is_known_without_schedule(self):
        market = PolymarketPositionFetcher._normalize_market({
            "conditionId": "condition-1",
            "question": "Geopolitical market",
            "feesEnabled": False,
        })

        self.assertFalse(market["fees_enabled"])
        self.assertTrue(market["fee_metadata_known"])
        self.assertIsNone(market["fee_schedule"])

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
        self.assertEqual(
            fetcher.session.get.call_args.kwargs["params"]["limit"], 500
        )

    def test_positions_paginates_stably_up_to_500_per_page(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.session.get = mock.Mock(side_effect=[
            response([raw_position(f"asset-{i}") for i in range(500)]),
            response([raw_position("asset-500")]),
        ])

        result = fetcher.get_positions_result("0xwallet")

        self.assertTrue(result.ok)
        self.assertEqual(len(result.positions), 501)
        calls = fetcher.session.get.call_args_list
        self.assertEqual(calls[0].kwargs["params"]["limit"], 500)
        self.assertEqual(calls[0].kwargs["params"]["offset"], 0)
        self.assertEqual(calls[1].kwargs["params"]["offset"], 500)
        self.assertEqual(calls[0].kwargs["params"]["sortBy"], "TOKENS")

    def test_positions_discards_partial_pages_after_later_error(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.session.get = mock.Mock(side_effect=[
            response([raw_position(f"asset-{i}") for i in range(500)]),
            response([], 500),
        ])

        result = fetcher.get_positions_result("0xwallet")

        self.assertFalse(result.ok)
        self.assertEqual(result.positions, [])
        self.assertIn("HTTP 500", result.error)

    def test_positions_distinguishes_valid_empty_from_feed_error(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.session.get = mock.Mock(side_effect=[
            response([]),
            response([], 429),
        ])

        empty = fetcher.get_positions_result("0xempty")
        failed = fetcher.get_positions_result("0xfailed")

        self.assertTrue(empty.ok)
        self.assertEqual(empty.positions, [])
        self.assertFalse(failed.ok)
        self.assertEqual(failed.positions, [])
        self.assertIn("HTTP 429", failed.error)
        self.assertTrue(failed.transient)

    def test_recent_buy_distinguishes_not_found_from_error(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.session.get = mock.Mock(side_effect=[
            response([]),
            response([], 500),
        ])

        missing = fetcher.get_recent_buy_result("0xwallet", "asset")
        failed = fetcher.get_recent_buy_result("0xwallet", "asset")

        self.assertEqual(missing.status, "not_found")
        self.assertEqual(failed.status, "error")
        self.assertIn("HTTP 500", failed.error)

    def test_snapshot_circuit_breaker_stops_timeout_burst(self):
        fetcher = PolymarketPositionFetcher()
        fetcher.get_positions_result = mock.Mock(side_effect=[
            PositionsFetchResult("wallet-1", False, error="timeout", transient=True),
            PositionsFetchResult("wallet-2", False, error="timeout", transient=True),
            PositionsFetchResult("wallet-3", False, error="timeout", transient=True),
        ])
        wallets = [f"wallet-{i}" for i in range(1, 7)]

        snapshot = fetcher.snapshot_wallets_with_status(wallets)

        self.assertEqual(fetcher.get_positions_result.call_count, 3)
        self.assertEqual(snapshot.successful_wallets, set())
        self.assertEqual(set(snapshot.failed_wallets), set(wallets))
        self.assertIn("snapshot saltato", snapshot.failed_wallets["wallet-6"])

    def test_snapshot_circuit_breaker_counts_only_consecutive_failures(self):
        fetcher = PolymarketPositionFetcher()
        timeout = lambda wallet: PositionsFetchResult(
            wallet, False, error="timeout", transient=True
        )
        success = lambda wallet: PositionsFetchResult(wallet, True, positions=[])
        fetcher.get_positions_result = mock.Mock(side_effect=[
            timeout("wallet-1"), timeout("wallet-2"), success("wallet-3"),
            timeout("wallet-4"), timeout("wallet-5"), success("wallet-6"),
        ])
        wallets = [f"wallet-{i}" for i in range(1, 7)]

        snapshot = fetcher.snapshot_wallets_with_status(wallets)

        self.assertEqual(fetcher.get_positions_result.call_count, 6)
        self.assertEqual(snapshot.successful_wallets, {"wallet-3", "wallet-6"})
        self.assertEqual(
            set(snapshot.failed_wallets),
            {"wallet-1", "wallet-2", "wallet-4", "wallet-5"},
        )

    def test_wallet_flapping_preserves_baseline_and_avoids_false_delta(self):
        bot = main_module.PolymarketPaperTradingBot.__new__(
            main_module.PolymarketPaperTradingBot
        )
        bot.prev_holdings = None
        wallet = "0xwallet"

        aggregate = {
            "asset-old": {"holders": {wallet}, "info": {}},
        }
        delta, baseline, initialized = bot._compute_holding_deltas(
            aggregate, {wallet}
        )
        self.assertEqual(delta, set())
        self.assertEqual((baseline, initialized), (1, 1))

        delta, baseline, initialized = bot._compute_holding_deltas({}, set())
        self.assertEqual(delta, set())
        self.assertEqual(bot.prev_holdings[wallet], {"asset-old"})

        delta, _, initialized = bot._compute_holding_deltas(
            aggregate, {wallet}
        )
        self.assertEqual(delta, set())
        self.assertEqual(initialized, 0)

        recovered_with_buy = {
            **aggregate,
            "asset-new": {"holders": {wallet}, "info": {}},
        }
        delta, _, _ = bot._compute_holding_deltas(
            recovered_with_buy, {wallet}
        )
        self.assertEqual(delta, {(wallet, "asset-new")})

    def test_wallet_first_success_after_initial_failure_is_baseline_only(self):
        bot = main_module.PolymarketPaperTradingBot.__new__(
            main_module.PolymarketPaperTradingBot
        )
        bot.prev_holdings = None
        wallet = "0xlate"

        delta, _, initialized = bot._compute_holding_deltas({}, set())
        self.assertEqual((delta, initialized), (set(), 0))
        delta, baseline, initialized = bot._compute_holding_deltas(
            {"asset-old": {"holders": {wallet}, "info": {}}}, {wallet}
        )
        self.assertEqual(delta, set())
        self.assertEqual((baseline, initialized), (1, 1))

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
