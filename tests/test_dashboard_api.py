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


class DashboardApiTests(unittest.TestCase):
    def test_status_uses_real_wallet_manifest_and_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.object(dashboard, "DATA_DIR", data), \
                 mock.patch.dict(EXECUTION, {"mode": "observe"}):
                sim = PaperTradingSimulator()
                sim.portfolio.cash = 297.0869
                sim._save_state()
                (data / "monitored_wallets.json").write_text(json.dumps({
                    "run_id": sim.run_id,
                    "wallets": [{
                        "address": "0xactual",
                        "name": "Actual Wallet",
                        "win_rate": 0.55,
                    }],
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


if __name__ == "__main__":
    unittest.main()
