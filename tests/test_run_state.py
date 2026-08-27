import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import run_state


class RunStateTests(unittest.TestCase):
    def test_archive_precedes_explicit_clear_and_stays_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            logs = root / "logs"
            (root / "src").mkdir()
            data.mkdir()
            logs.mkdir()
            (root / "src" / "config.py").write_text("MODE='observe'\n")
            (data / "portfolio_state.json").write_text(json.dumps({
                "run_id": "../../unsafe-run",
                "cash": 297.0869,
            }))
            (data / "trades_log.json").write_text("[]")
            (data / "wallet_quality.json").write_text('{"wallet": {"pnl": -1}}')
            (data / "shadow_state.json").write_text('{"shadow_version": 1}')
            (data / "shadow_journal.jsonl").write_text('{"action": "opened"}\n')
            (data / "shadow_equity_curve.json").write_text("[]")
            (data / "run_manifest.json").write_text(json.dumps({
                "manifest_version": 1, "run_id": "unsafe-run",
                "execution_mode": "observe",
            }))
            (data / "preflight_report.json").write_text('{"ready": true}')
            (data / "wallet_validation_registry.json").write_text(json.dumps({
                "registry_version": 1,
                "wallets": {"0xbad": {"status": "quarantined"}},
            }))
            (data / "scan_results.json").write_text('{"wallets": []}')

            with mock.patch.object(run_state, "ROOT", root), \
                 mock.patch.object(run_state, "DATA", data), \
                 mock.patch.object(run_state, "LOGS", logs):
                archived = run_state.archive()
                self.assertTrue(archived.resolve().is_relative_to((data / "runs").resolve()))
                self.assertTrue((archived / "portfolio_state.json").exists())
                self.assertTrue((archived / "wallet_quality.json").exists())
                self.assertTrue((archived / "shadow_state.json").exists())
                self.assertTrue((archived / "shadow_journal.jsonl").exists())
                self.assertTrue((archived / "shadow_equity_curve.json").exists())
                self.assertTrue((archived / "run_manifest.json").exists())
                self.assertTrue((archived / "preflight_report.json").exists())
                self.assertTrue(
                    (archived / "wallet_validation_registry.json").exists()
                )
                self.assertTrue((archived / "scan_results.json").exists())
                run_state.clear(force=True)
                self.assertFalse((data / "portfolio_state.json").exists())
                self.assertFalse((data / "wallet_quality.json").exists())
                self.assertFalse((data / "shadow_state.json").exists())
                self.assertFalse((data / "shadow_journal.jsonl").exists())
                self.assertFalse((data / "shadow_equity_curve.json").exists())
                self.assertFalse((data / "run_manifest.json").exists())
                self.assertFalse((data / "preflight_report.json").exists())
                self.assertTrue(
                    (data / "wallet_validation_registry.json").exists()
                )
                self.assertTrue((data / "scan_results.json").exists())
                self.assertTrue((archived / "portfolio_state.json").exists())
                self.assertTrue((archived / "shadow_state.json").exists())

    def test_clear_without_force_refuses(self):
        with self.assertRaises(SystemExit):
            run_state.clear(force=False)


if __name__ == "__main__":
    unittest.main()
