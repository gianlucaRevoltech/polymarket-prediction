import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dashboard
import main as main_module
import scanner as scanner_module
import simulator as simulator_module
from config import EXECUTION
from scanner import PolymarketScanner
from simulator import PaperTradingSimulator


def _response(payload):
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


class ScanContractTests(unittest.TestCase):
    def test_gamma_markets_are_paginated_and_deduplicated(self):
        first = [
            {
                "conditionId": f"cond-{index}",
                "question": f"Market {index}",
                "volumeNum": 1000 - index,
                "events": [],
            }
            for index in range(100)
        ]
        second = [
            {
                "conditionId": f"cond-{index}",
                "question": f"Market {index}",
                "volumeNum": 1000 - index,
                "events": [],
            }
            for index in range(100, 200)
        ]
        third = [
            {
                "conditionId": "cond-200",
                "question": "Market 200",
                "volumeNum": 1,
                "events": [],
            }
        ]
        scanner = PolymarketScanner()
        with mock.patch.object(
            scanner_module.requests,
            "get",
            side_effect=[_response(first), _response(second), _response(third)],
        ) as get:
            markets = scanner.get_popular_markets(201)

        self.assertEqual(len(markets), 201)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], 0)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["limit"], 100)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 100)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["limit"], 100)
        self.assertEqual(get.call_args_list[2].kwargs["params"]["offset"], 200)
        self.assertEqual(get.call_args_list[2].kwargs["params"]["limit"], 1)

    def test_holder_limit_is_clamped_and_errors_are_observable(self):
        scanner = PolymarketScanner()
        scanner.session.get = mock.Mock(return_value=_response([]))
        self.assertEqual(scanner.get_market_holders("condition", limit=25), [])
        self.assertEqual(
            scanner.session.get.call_args.kwargs["params"]["limit"], 20
        )
        self.assertEqual(scanner.scan_health["holder_requests"], 1)
        self.assertEqual(scanner.scan_health["holder_successes"], 1)
        self.assertEqual(scanner.scan_health["holder_limit_clamped"], 1)

        invalid = _response({"unexpected": True})
        scanner.session.get = mock.Mock(return_value=invalid)
        self.assertEqual(scanner.get_market_holders("condition-2"), [])
        self.assertEqual(scanner.scan_health["holder_errors"], 1)
        self.assertTrue(scanner.scan_health["last_holder_error"])

    def test_bot_refuses_an_insufficient_frozen_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            wallets = [{"address": f"0x{index}"} for index in range(3)]
            (data / "scan_results.json").write_text(
                json.dumps({"wallets": wallets}), encoding="utf-8"
            )
            bot = main_module.PolymarketPaperTradingBot.__new__(
                main_module.PolymarketPaperTradingBot
            )
            bot.simulator = SimpleNamespace(
                run_id="run-current", execution_mode="observe"
            )
            bot._run_wallet_scan = mock.Mock()
            with mock.patch.object(main_module, "DATA_DIR", data), \
                 mock.patch.dict(EXECUTION, {"minimum_monitored_wallets": 5}):
                self.assertFalse(bot.ensure_monitored_wallets())
            self.assertFalse((data / "monitored_wallets.json").exists())

    def test_direct_main_does_not_fallback_to_legacy_scanner(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            bot = main_module.PolymarketPaperTradingBot.__new__(
                main_module.PolymarketPaperTradingBot
            )
            bot.simulator = SimpleNamespace(
                run_id="run-current", execution_mode="observe"
            )
            bot._run_wallet_scan = mock.Mock(return_value=False)
            bot.run_initial_scan = mock.Mock(return_value=True)
            with mock.patch.object(main_module, "DATA_DIR", data):
                self.assertFalse(bot.ensure_monitored_wallets())
            bot.run_initial_scan.assert_not_called()

    def test_dashboard_exposes_cohort_health_for_current_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.object(dashboard, "DATA_DIR", data), \
                 mock.patch.dict(
                     EXECUTION,
                     {"mode": "observe", "minimum_monitored_wallets": 5},
                 ):
                sim = PaperTradingSimulator()
                sim._save_state()
                (data / "monitored_wallets.json").write_text(
                    json.dumps({
                        "run_id": sim.run_id,
                        "wallets": [
                            {"address": f"0x{index}"} for index in range(3)
                        ],
                    }),
                    encoding="utf-8",
                )
                payload = dashboard.app.test_client().get(
                    "/api/status"
                ).get_json()

            cohort = payload["cohort_health"]
            self.assertEqual(cohort["wallet_count"], 3)
            self.assertEqual(cohort["minimum_required"], 5)
            self.assertFalse(cohort["validation_ready"])
            self.assertIn("3/5", cohort["reason"])


class AtomicArtifactTests(unittest.TestCase):
    def test_equity_curve_remains_valid_json_without_tick_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.dict(EXECUTION, {"mode": "observe"}):
                sim = PaperTradingSimulator()
                sim.record_equity()
                sim.record_equity()
                curve = json.loads(sim.equity_file.read_text(encoding="utf-8"))

            self.assertEqual(len(curve), 2)
            self.assertFalse(Path(str(sim.equity_file) + ".bak").exists())


if __name__ == "__main__":
    unittest.main()
