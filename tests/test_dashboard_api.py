import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dashboard
import simulator as simulator_module
from config import EXECUTION
from simulator import PaperTradingSimulator
from time_utils import utc_now_iso


class DashboardApiTests(unittest.TestCase):
    def test_status_uses_real_wallet_manifest_and_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.object(dashboard, "DATA_DIR", data), \
                 mock.patch.dict(EXECUTION, {"mode": "observe"}), \
                 mock.patch.object(dashboard, "get_bot_status", return_value="running"):
                sim = PaperTradingSimulator()
                sim.portfolio.cash = 297.0869
                sim._save_state()
                (data / "candidate_journal.jsonl").write_text(
                    json.dumps({
                        "journal_version": 6,
                        "run_id": sim.run_id,
                        "signal_id": "opened-signal",
                        "decision": "opened",
                        "reason": "paper_validation",
                        "pretrade_eligible": True,
                        "wallet": "0xactual",
                        "num_holders": 2,
                    }) + "\n",
                    encoding="utf-8",
                )
                (data / "shadow_journal.jsonl").write_text(
                    json.dumps({
                        "shadow_version": 1,
                        "run_id": sim.run_id,
                        "signal_id": "shadow-current",
                        "action": "opened",
                    }) + "\n" + json.dumps({
                        "shadow_version": 1,
                        "run_id": "other-run",
                        "signal_id": "shadow-old",
                        "action": "opened",
                    }) + "\n",
                    encoding="utf-8",
                )
                (data / "monitored_wallets.json").write_text(json.dumps({
                    "run_id": sim.run_id,
                    "frozen": True,
                    "domain_policy_version": 1,
                    "intended_domains": ["macro"],
                    "wallets": [{
                        "address": "0xactual",
                        "name": "Actual Wallet",
                        "win_rate": 0.55,
                        "allowed_domains": ["macro"],
                    }],
                }), encoding="utf-8")
                (data / "wallet_validation_registry.json").write_text(
                    json.dumps({
                        "registry_version": 1,
                        "wallets": {
                            "0xbad": {"status": "quarantined"},
                        },
                    }), encoding="utf-8",
                )
                (data / "runtime_status.json").write_text(json.dumps({
                    "run_id": sim.run_id,
                    "phase": "idle",
                    "runtime_mode": "observe",
                    "updated_at": utc_now_iso(),
                    "feed_health": {
                        "requests": 12,
                        "rate_limit_errors": 1,
                        "partial_snapshot_cycles": 2,
                    },
                }), encoding="utf-8")
                (data / "preflight_report.json").write_text(json.dumps({
                    "report_version": 1,
                    "run_id": sim.run_id,
                    "ready": True,
                    "blockers": [],
                    "warnings": [{"key": "economic_edge", "severity": "yellow"}],
                }), encoding="utf-8")

                client = dashboard.app.test_client()
                response = client.get("/api/status")
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response.headers["Cache-Control"])
                payload = response.get_json()
                self.assertEqual(payload["execution_mode"], "observe")
                self.assertEqual(payload["run_id"], sim.run_id)
                self.assertTrue(payload["state_saved_at"])
                self.assertEqual(
                    payload["monitored_wallets"][0]["address"], "0xactual"
                )
                self.assertEqual(payload["summary"]["max_open_positions"], 2)
                self.assertEqual(payload["summary"]["peak_equity"], 300.0)
                self.assertEqual(payload["candidate_summary"]["passed_pretrade"], 1)
                self.assertEqual(
                    payload["candidate_summary"]["eligible_by_wallet"]["0xactual"],
                    1,
                )
                self.assertEqual(
                    payload["candidate_summary"]["consensus_counts"]["2"], 1
                )
                self.assertEqual(payload["feed_health"]["rate_limit_errors"], 1)
                self.assertEqual(payload["cohort_health"]["wallet_count"], 1)
                self.assertEqual(payload["cohort_health"]["minimum_required"], 5)
                self.assertFalse(payload["cohort_health"]["validation_ready"])
                self.assertFalse(
                    payload["shadow_validation"]["real_money_authorized"]
                )
                self.assertTrue(
                    payload["shadow_validation"]["domain_policy_frozen"]
                )
                self.assertEqual(
                    payload["shadow_validation"]["max_open_positions"], 2
                )
                self.assertEqual(
                    payload["wallet_validation_registry"]["quarantined_count"], 1
                )
                # A stale optimistic report cannot override missing manifest,
                # insufficient cohort and incomplete runtime/journal evidence.
                self.assertFalse(payload["readiness"]["ready"])
                self.assertFalse(payload["economic_status"]["edge_demonstrated"])
                self.assertFalse(payload["economic_status"]["real_money_authorized"])
                readiness_response = client.get("/api/readiness")
                self.assertEqual(readiness_response.status_code, 200)
                self.assertFalse(readiness_response.get_json()["ready"])
                shadow_response = client.get("/api/shadow?limit=50")
                self.assertIn("no-store", shadow_response.headers["Cache-Control"])
                shadow_rows = shadow_response.get_json()
                self.assertEqual(len(shadow_rows), 1)
                self.assertEqual(shadow_rows[0]["signal_id"], "shadow-current")


if __name__ == "__main__":
    unittest.main()
