import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import paper_preflight
from run_manifest import create_run_manifest
from time_utils import utc_now_iso
from paper_readiness import cohort_identity


def cohort():
    return {
        "intended_domains": ["macro"],
        "wallets": [
            {"address": f"wallet-{index}", "allowed_domains": ["macro"]}
            for index in range(5)
        ],
    }


class PaperPreflightTests(unittest.TestCase):
    @staticmethod
    def process_state(path):
        return Path(path).name != "latency_arb.pid"

    def fixture(self, root: Path):
        data = root / "data"
        logs = root / "logs"
        data.mkdir()
        logs.mkdir()
        manifest = create_run_manifest("observe", cohort(), data_dir=data, root=ROOT)
        now = utc_now_iso()
        (data / "portfolio_state.json").write_text(json.dumps({
            "state_version": 3, "initial_capital": 300.0,
            "run_id": manifest["run_id"], "execution_mode": "observe",
            "cash": 300.0, "positions": {}, "closed_positions": [], "saved_at": now,
        }), encoding="utf-8")
        (data / "runtime_status.json").write_text(json.dumps({
            "runtime_version": 2, "running_commit": manifest["deployed_commit"],
            "process_instance_id": "instance-1", "pid": 42,
            "cohort_identity": cohort_identity(manifest),
            "completed_cycles": 5, "consecutive_healthy_cycles": 5,
            "last_cycle_at": now,
            "run_id": manifest["run_id"], "runtime_mode": "observe",
            "updated_at": now, "cycle": 5, "phase": "idle", "error": "",
            "feed_health": {
                "consecutive_transient_errors": 0, "fully_failed_snapshot_cycles": 2,
                "last_snapshot_at": now, "last_snapshot_status": "complete",
                "last_snapshot_wallets_ok": [w["address"] for w in manifest["wallets"]],
                "last_snapshot_wallets_failed": [], "consecutive_complete_snapshots": 5,
                "consecutive_incomplete_snapshots": 0, "consecutive_failed_snapshots": 0,
                "backoff_remaining_seconds": 0,
            },
        }), encoding="utf-8")
        (data / "shadow_state.json").write_text(json.dumps({
            "shadow_version": 3, "initial_capital": 300.0, "cash": 300.0, "positions": {},
            "run_id": manifest["run_id"], "halt_reason": "", "closed_positions": [],
        }), encoding="utf-8")
        (logs / "bot.log").write_text("ciclo completato\n", encoding="utf-8")
        (data / "bot.pid").write_text("42")
        return data, logs, manifest

    def test_zero_candidates_is_warning_but_preflight_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, logs, _ = self.fixture(Path(tmp))
            with mock.patch.object(paper_preflight, "DATA", data), \
                 mock.patch.object(paper_preflight, "LOGS", logs), \
                 mock.patch.object(paper_preflight, "_pid_alive", side_effect=self.process_state), \
                 mock.patch.object(paper_preflight, "_run_synthetic", return_value={"passed": True}):
                report = paper_preflight.build_report()
            self.assertTrue(report["ready"])
            warning_keys = {item["key"] for item in report["warnings"]}
            self.assertIn("candidate_volume", warning_keys)
            self.assertIn("economic_edge", warning_keys)

    def test_traceback_and_shadow_run_loss_block_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, logs, manifest = self.fixture(Path(tmp))
            (logs / "bot.log").write_text("Traceback (most recent call last)\n")
            (data / "shadow_state.json").write_text(json.dumps({
                "run_id": manifest["run_id"], "halt_reason": "run_loss -6.10 USD",
                "closed_positions": [],
            }), encoding="utf-8")
            with mock.patch.object(paper_preflight, "DATA", data), \
                 mock.patch.object(paper_preflight, "LOGS", logs), \
                 mock.patch.object(paper_preflight, "_pid_alive", side_effect=self.process_state), \
                 mock.patch.object(paper_preflight, "_run_synthetic", return_value={"passed": True}):
                report = paper_preflight.build_report()
            self.assertFalse(report["ready"])
            blocker_keys = {item["key"] for item in report["blockers"]}
            self.assertIn("traceback", blocker_keys)
            self.assertIn("shadow_breaker", blocker_keys)

    def test_eligible_without_source_book_or_fee_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, logs, manifest = self.fixture(Path(tmp))
            (data / "candidate_journal.jsonl").write_text(json.dumps({
                "journal_version": 6, "run_id": manifest["run_id"],
                "signal_id": "bad", "decision": "eligible",
                "pretrade_eligible": True,
            }) + "\n", encoding="utf-8")
            with mock.patch.object(paper_preflight, "DATA", data), \
                 mock.patch.object(paper_preflight, "LOGS", logs), \
                 mock.patch.object(paper_preflight, "_pid_alive", side_effect=self.process_state), \
                 mock.patch.object(paper_preflight, "_run_synthetic", return_value={"passed": True}):
                report = paper_preflight.build_report()
            self.assertFalse(report["ready"])
            self.assertIn("journal_v6", {item["key"] for item in report["blockers"]})


if __name__ == "__main__":
    unittest.main()
