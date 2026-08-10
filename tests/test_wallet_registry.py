import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import scanner as scanner_module
from scanner import PolymarketScanner
from wallet_registry import quarantine_wallet, quarantined_wallets


class WalletRegistryTests(unittest.TestCase):
    def test_registry_is_persistent_and_scanner_excludes_wallet(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            quarantine_wallet(
                data, "0xBAD", run_id="run-failed",
                reason="shadow_consecutive_losses", loss_streak=3,
            )
            self.assertEqual(quarantined_wallets(data), {"0xbad"})

            scanner = PolymarketScanner()
            qualified = [
                {
                    "address": "0xbad", "name": "Bad", "pseudonym": "Bad",
                    "overlap": 3, "pnl": 100, "invested": 100,
                    "roi": 1.0, "decided": 20, "win_rate": 0.8,
                },
                {
                    "address": "0xgood", "name": "Good", "pseudonym": "Good",
                    "overlap": 3, "pnl": 100, "invested": 100,
                    "roi": 1.0, "decided": 20, "win_rate": 0.8,
                },
            ]
            cfg = {
                "active": ["macro"], "specialists_per_category": 5,
                "markets_to_scan": 1, "holders_per_market": 1,
                "min_overlap": 1, "min_realized_roi": 0,
                "min_decided": 1, "min_win_rate": 0,
            }
            with mock.patch.object(scanner_module, "DATA_DIR", data), \
                 mock.patch.object(scanner_module, "CATEGORIES", cfg), \
                 mock.patch.object(scanner, "get_popular_markets", return_value=[{
                     "category": "macro", "condition_id": "cond",
                     "question": "Fed?", "volume": 100,
                 }]), \
                 mock.patch.object(scanner, "_collect_overlap", return_value=({}, {})), \
                 mock.patch.object(scanner, "_qualify_wallets", return_value=qualified):
                selected = scanner.scan_categories(top_n=2)

            self.assertEqual([wallet.address for wallet in selected], ["0xgood"])
            scan = json.loads((data / "scan_results.json").read_text())
            self.assertEqual(scan["wallets"][0]["categories"], ["macro"])

    def test_corrupt_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "wallet_validation_registry.json").write_text("not-json")
            with self.assertRaises(ValueError):
                quarantined_wallets(data)

    def test_scanner_persists_all_qualified_specialist_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            scanner = PolymarketScanner()
            wallet = {
                "address": "0xmulti", "name": "Multi", "pseudonym": "Multi",
                "overlap": 3, "pnl": 100, "invested": 100,
                "roi": 1.0, "decided": 20, "win_rate": 0.8,
            }
            cfg = {
                "active": ["macro", "politics"], "specialists_per_category": 5,
                "markets_to_scan": 2, "holders_per_market": 1,
                "min_overlap": 1, "min_realized_roi": 0,
                "min_decided": 1, "min_win_rate": 0,
            }
            markets = [
                {"category": "macro", "condition_id": "m", "question": "Fed"},
                {"category": "politics", "condition_id": "p", "question": "Vote"},
            ]
            with mock.patch.object(scanner_module, "DATA_DIR", data), \
                 mock.patch.object(scanner_module, "CATEGORIES", cfg), \
                 mock.patch.object(scanner, "get_popular_markets", return_value=markets), \
                 mock.patch.object(scanner, "_collect_overlap", return_value=({}, {})), \
                 mock.patch.object(scanner, "_qualify_wallets", return_value=[wallet]):
                scanner.scan_categories(top_n=2)
            scan = json.loads((data / "scan_results.json").read_text())
            self.assertEqual(
                scan["wallets"][0]["categories"], ["macro", "politics"]
            )


if __name__ == "__main__":
    unittest.main()
