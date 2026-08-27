import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import simulator as simulator_module
from run_manifest import create_run_manifest, load_run_manifest
from simulator import PaperTradingSimulator


def cohort():
    return {
        "intended_domains": ["macro"],
        "wallets": [
            {"address": f"wallet-{index}", "allowed_domains": ["macro"]}
            for index in range(5)
        ],
    }


class RunManifestTests(unittest.TestCase):
    def test_manifest_mode_and_cohort_survive_restart_and_env_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            manifest = create_run_manifest(
                "paper_validation", cohort(), data_dir=data, root=ROOT
            )
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.dict(os.environ, {"POLYMARKET_EXECUTION_MODE": "observe"}):
                sim = PaperTradingSimulator()
                self.assertEqual(sim.run_id, manifest["run_id"])
                self.assertEqual(sim.execution_mode, "paper_validation")
                self.assertEqual(sim.execution_mode_source, "run_manifest")
                self.assertIn("ignorata", sim.mode_drift_warning)
                sim._save_state()
                restarted = PaperTradingSimulator()
                self.assertEqual(restarted.run_id, manifest["run_id"])
                self.assertEqual(restarted.execution_mode, "paper_validation")
                frozen = load_run_manifest(data)
                self.assertEqual(
                    [w["address"] for w in frozen["wallets"]],
                    [w["address"] for w in cohort()["wallets"]],
                )

    def test_mixed_ledger_is_not_loaded_and_opening_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            manifest = create_run_manifest("paper_validation", cohort(), data_dir=data, root=ROOT)
            (data / "portfolio_state.json").write_text(json.dumps({
                "state_version": 3, "run_id": "another-run",
                "execution_mode": "paper_validation", "cash": 10,
                "positions": {}, "closed_positions": [],
            }), encoding="utf-8")
            with mock.patch.object(simulator_module, "DATA_DIR", data):
                sim = PaperTradingSimulator()
                self.assertEqual(sim.run_id, manifest["run_id"])
                self.assertEqual(sim.portfolio.cash, 300.0)
                self.assertIn("ledger run_id", sim.run_integrity_error)
                self.assertTrue(sim._opening_halt_reason("copy").startswith("run_integrity:"))

    def test_corrupt_manifest_never_falls_back_to_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "run_manifest.json").write_text("{broken", encoding="utf-8")
            with mock.patch.object(simulator_module, "DATA_DIR", data), \
                 mock.patch.dict(
                     os.environ, {"POLYMARKET_EXECUTION_MODE": "paper_validation"}
                 ):
                sim = PaperTradingSimulator()
                self.assertIn("non valido", sim.run_integrity_error)
                self.assertTrue(sim._opening_halt_reason("copy").startswith("run_integrity:"))


if __name__ == "__main__":
    unittest.main()
