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

            with mock.patch.object(run_state, "ROOT", root), \
                 mock.patch.object(run_state, "DATA", data), \
                 mock.patch.object(run_state, "LOGS", logs):
                archived = run_state.archive()
                self.assertTrue(archived.resolve().is_relative_to((data / "runs").resolve()))
                self.assertTrue((archived / "portfolio_state.json").exists())
                self.assertTrue((archived / "wallet_quality.json").exists())
                run_state.clear(force=True)
                self.assertFalse((data / "portfolio_state.json").exists())
                self.assertFalse((data / "wallet_quality.json").exists())
                self.assertTrue((archived / "portfolio_state.json").exists())

    def test_clear_without_force_refuses(self):
        with self.assertRaises(SystemExit):
            run_state.clear(force=False)


if __name__ == "__main__":
    unittest.main()
