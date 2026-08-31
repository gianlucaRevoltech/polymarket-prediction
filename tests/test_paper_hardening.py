import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_paper_preflight as fixtures
ROOT = fixtures.ROOT
import paper_preflight
import paper_control
import run_state
import dashboard
from paper_accounting import ledger_metrics, execution_fees, position_pnl, read_json
from paper_readiness import build_readiness, cohort_identity
from runtime_contract import feed_readiness, atomic_json
from run_manifest import load_run_manifest
from time_utils import utc_now_iso
from portfolio_sync import PolymarketPositionFetcher, PositionsFetchResult


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.logs, self.manifest = fixtures.PaperPreflightTests().fixture(self.root)

    def report(self):
        return build_readiness(self.data, self.logs, ROOT,
                               process_check=fixtures.PaperPreflightTests.process_state,
                               synthetic={"passed": True})

    def runtime(self, **changes):
        runtime = read_json(self.data / "runtime_status.json")
        runtime.update(changes)
        atomic_json(self.data / "runtime_status.json", runtime)
        return runtime

    def test_recovered_historical_outages_do_not_block(self):
        self.assertTrue(self.report()["ready"])
        self.assertEqual(self.report()["feed_status"], "healthy")

    def test_unknown_partial_consecutive_failures_stale_and_backoff_block(self):
        original = read_json(self.data / "runtime_status.json")["feed_health"]
        cases = [({}, "unknown"),
                 (dict(original, consecutive_failed_snapshots=2), "outage"),
                 (dict(original, consecutive_incomplete_snapshots=3), "outage"),
                 (dict(original, last_snapshot_status="partial"), "recovering"),
                 (dict(original, last_snapshot_at="2000-01-01T00:00:00Z"), "stale"),
                 (dict(original, backoff_remaining_seconds=1), "recovering")]
        for feed, expected in cases:
            with self.subTest(expected=expected, feed=feed):
                self.runtime(feed_health=feed)
                report = self.report()
                self.assertFalse(report["ready"])
                self.assertEqual(report["feed_status"], expected)

    def test_single_http_success_cannot_reset_snapshot_outage(self):
        fetcher = PolymarketPositionFetcher()
        with mock.patch.object(fetcher, "get_positions_result", return_value=PositionsFetchResult(wallet="a", ok=False, error="timeout", transient=True)):
            fetcher.snapshot_wallets_with_status(["a"])
            fetcher.snapshot_wallets_with_status(["a"])
        fetcher._data_consecutive_transient_errors = 0
        self.assertEqual(feed_readiness(fetcher.get_feed_health())[0], "outage")
        with mock.patch.object(fetcher, "get_positions_result", return_value=PositionsFetchResult(wallet="a", ok=True, positions=[])):
            fetcher.snapshot_wallets_with_status(["a"])
            self.assertEqual(feed_readiness(fetcher.get_feed_health())[0], "recovering")
            fetcher.snapshot_wallets_with_status(["a"])
            self.assertEqual(feed_readiness(fetcher.get_feed_health())[0], "healthy")
        self.assertEqual(fetcher.get_feed_health()["fully_failed_snapshot_cycles"], 2)

    def test_compatible_update_preserves_origin_but_stale_runtime_commit_blocks(self):
        manifest = dict(self.manifest, deployed_commit="old-origin")
        atomic_json(self.data / "run_manifest.json", manifest)
        self.assertTrue(self.report()["ready"])
        self.assertEqual(self.report()["run_origin_commit"], "old-origin")
        self.runtime(running_commit="old-runtime")
        self.assertFalse(self.report()["ready"])

    def test_domains_schema_and_completed_cycle_are_required(self):
        monitored = read_json(self.data / "monitored_wallets.json")
        monitored["wallets"][0]["allowed_domains"] = ["other"]
        atomic_json(self.data / "monitored_wallets.json", monitored)
        self.assertIn("cohort_identity", {x["key"] for x in self.report()["blockers"]})
        self.runtime(runtime_version=1, completed_cycles=1, last_cycle_at="2000-01-01")
        blockers = {x["key"] for x in self.report()["blockers"]}
        self.assertTrue({"runtime_schema", "two_cycles", "cycle_fresh"} <= blockers)

    def test_api_live_recovery_replaces_old_block_and_matches_cli(self):
        atomic_json(self.data / "preflight_report.json", dict(
            run_id=self.manifest["run_id"], checked_commit=self.manifest["deployed_commit"],
            ready=False, blockers=[{"key": "feed_health"}], synthetic_lifecycle={"passed": True}))
        with mock.patch.object(dashboard, "DATA_DIR", self.data), \
             mock.patch.object(dashboard, "BASE_DIR", self.root), \
             mock.patch("paper_readiness.git_commit", return_value=self.manifest["deployed_commit"]), \
             mock.patch("paper_readiness.pid_alive", side_effect=fixtures.PaperPreflightTests.process_state):
            payload = dashboard.app.test_client().get("/api/readiness").get_json()
            self.assertTrue(payload["ready"])
            self.assertEqual(payload["blockers"], self.report()["blockers"])

    def test_main_startup_and_restart_barrier(self):
        import main as main_module
        from types import SimpleNamespace
        bot = main_module.PolymarketPaperTradingBot.__new__(main_module.PolymarketPaperTradingBot)
        bot.running = True
        bot._healthy_cycles = 0
        bot.last_cycle_at = utc_now_iso()
        bot.simulator = SimpleNamespace(run_id=self.manifest["run_id"])
        feed = read_json(self.data / "runtime_status.json")["feed_health"]
        bot.fetcher = SimpleNamespace(get_feed_health=lambda: feed)
        with mock.patch.object(main_module, "DATA_DIR", self.data):
            self.assertEqual(bot._opening_guard(), "startup_verification_pending")
            bot._healthy_cycles = 2
            self.assertEqual(bot._opening_guard(), "paper_activation_pending")
            atomic_json(self.data / "paper_activation.json", dict(run_id=self.manifest["run_id"], status="active"))
            self.assertEqual(bot._opening_guard(), "")
            bot._healthy_cycles = 0  # restart never inherits process-local warmup
            self.assertEqual(bot._opening_guard(), "startup_verification_pending")

    def test_wait_handles_new_ledger_and_timeout_without_unbounded_sleep(self):
        pending = dict(ready=False, checks=[], blockers=[dict(key="ledger_schema", message="pending")])
        good = dict(ready=True, checks=[], blockers=[])
        with mock.patch.object(paper_preflight, "build_report", side_effect=[pending, good]), \
             mock.patch.object(paper_preflight.time, "monotonic", side_effect=[0, 0, 0]), \
             mock.patch.object(paper_preflight.time, "sleep") as sleep:
            self.assertTrue(paper_preflight.wait_report(post_start=True, wait_seconds=120, synthetic={"passed": True})["ready"])
            sleep.assert_called_once_with(5)
        with mock.patch.object(paper_preflight, "build_report", return_value=pending), \
             mock.patch.object(paper_preflight.time, "monotonic", side_effect=[0, 120]), \
             mock.patch.object(paper_preflight.time, "sleep") as sleep:
            self.assertFalse(paper_preflight.wait_report(post_start=True, wait_seconds=120, synthetic={"passed": True})["ready"])
            sleep.assert_not_called()

    def test_malformed_safety_is_red_not_api_crash(self):
        atomic_json(self.data / "safety_state.json", {"loss_streak": "bad"})
        self.assertFalse(self.report()["ready"])

    def test_paper_report_does_not_mix_runs_or_candidate_estimates(self):
        import paper_report
        atomic_json(self.data / "equity_curve.json", {})
        # Valid empty current-run curve; an eligible estimate is not an execution.
        (self.data / "equity_curve.json").write_text("[]")
        (self.data / "candidate_journal.jsonl").write_text(json.dumps(dict(
            run_id="other", decision="opened", costs={"fee_usdc": 90})) + "\n")
        result = paper_report.report(self.data, self.logs, ROOT)
        self.assertEqual(result["economic_status"]["fees_usdc"], 0)
        self.assertEqual(result["economic_status"]["net_pnl"], 0)
        self.assertFalse(result["real_money_authorized"])


class AccountingTests(unittest.TestCase):
    @staticmethod
    def state(pnls):
        closed = [dict(position_id=str(i), signal_id=f"s{i}", run_id="r", is_closed=True,
                       entry_price=.5, exit_price=.5+pnl/10, shares=10., size_usdc=5.,
                       source_wallet=f"w{i}", event_slug=f"e{i}", category="macro")
                  for i, pnl in enumerate(pnls)]
        return dict(run_id="r", initial_capital=300., cash=300.+sum(pnls),
                    positions={}, closed_positions=closed)

    def test_vps_three_closures_are_negative_not_zero(self):
        metrics = ledger_metrics(self.state([-.064103, 0, -.460526]), "r")
        self.assertTrue(metrics["reconciled"])
        self.assertAlmostEqual(metrics["realized_pnl"], -.524629, places=9)
        self.assertIsNone(metrics["max_positive_wallet_share"])

    def test_zero_exit_valid_and_missing_or_nonfinite_prices_not_zero(self):
        state = self.state([-5])
        self.assertEqual(position_pnl(state["closed_positions"][0]), -5)
        for bad in (None, float("nan"), "invalid"):
            state["closed_positions"][0]["exit_price"] = bad
            metrics = ledger_metrics(state, "r")
            self.assertFalse(metrics["reconciled"])
            self.assertIsNone(metrics["net_pnl"])

    def test_fees_only_executions_dedup_and_no_second_fee_subtraction(self):
        state = self.state([.3])
        rows = [dict(decision="opened", signal_id="s0", costs={"fee_usdc": .1}),
                dict(decision="closed", position_id="0", costs={"exit_fee_usdc": .2}),
                dict(decision="eligible", costs={"fee_usdc": 100}),
                dict(decision="rejected", costs={"fee_usdc": 100})]
        fees = execution_fees(rows + rows, state)
        self.assertEqual(fees["fee_quality_errors"], [])
        self.assertAlmostEqual(fees["fees_usdc"], .3)
        self.assertAlmostEqual(ledger_metrics(state, "r")["realized_pnl"], .3)
        self.assertIsNone(execution_fees([], state)["fees_usdc"])

    def test_positive_contribution_uses_wins_not_net_wallet_total(self):
        state = self.state([1, -1, 1])
        state["closed_positions"][1]["source_wallet"] = "w0"
        self.assertEqual(ledger_metrics(state, "r")["max_positive_wallet_share"], .5)


class TransactionTests(unittest.TestCase):
    setUp = HardeningTests.setUp
    runtime = HardeningTests.runtime
    def controller_context(self):
        stack = contextlib.ExitStack()
        for module in (run_state, paper_preflight):
            stack.enter_context(mock.patch.object(module, "DATA", self.data))
            stack.enter_context(mock.patch.object(module, "LOGS", self.logs))
        stack.enter_context(mock.patch.object(paper_preflight, "_run_synthetic", return_value={"passed": True}))
        stack.enter_context(mock.patch.object(paper_preflight, "_pid_alive", side_effect=fixtures.PaperPreflightTests.process_state))
        return stack

    def start_services(self, action):
        if action == "start":
            manifest = load_run_manifest(self.data)
            now = utc_now_iso()
            atomic_json(self.data / "portfolio_state.json", dict(state_version=3, run_id=manifest["run_id"],
                execution_mode="paper_validation", initial_capital=300., cash=300., positions={},
                closed_positions=[], saved_at=now))
            runtime = read_json(self.data / "runtime_status.json")
            if not runtime:
                # reuse a healthy telemetry fixture, bound to the new run/process
                with tempfile.TemporaryDirectory() as tmp:
                    fixture_data, _, _ = fixtures.PaperPreflightTests().fixture(Path(tmp))
                    runtime = read_json(fixture_data / "runtime_status.json")
            runtime.update(run_id=manifest["run_id"], runtime_mode="paper_validation",
                process_instance_id="new-process", cohort_identity=cohort_identity(manifest),
                running_commit=manifest["deployed_commit"])
            atomic_json(self.data / "runtime_status.json", runtime)
            (self.data / "bot.pid").write_text("42")

    def test_failed_initial_preflight_never_stops_archives_or_clears(self):
        before = (self.data / "portfolio_state.json").read_bytes()
        self.runtime(error="failure")
        with self.controller_context(), mock.patch.object(paper_control, "services") as services, \
             mock.patch.object(run_state, "archive") as archive, mock.patch.object(run_state, "clear") as clear:
            self.assertEqual(paper_control.paper_start(), 2)
            services.assert_not_called()
            archive.assert_not_called()
            clear.assert_not_called()
        self.assertEqual(before, (self.data / "portfolio_state.json").read_bytes())

    def test_archive_failure_does_not_clear_and_marks_failed(self):
        before = (self.data / "portfolio_state.json").read_bytes()
        with self.controller_context(), mock.patch.object(paper_control, "services"), \
             mock.patch.object(run_state, "archive", side_effect=OSError("disk full")), \
             mock.patch.object(run_state, "clear") as clear:
            self.assertEqual(paper_control.paper_start(), 2)
            clear.assert_not_called()
        self.assertEqual(before, (self.data / "portfolio_state.json").read_bytes())
        self.assertEqual(read_json(self.data / "paper_activation.json")["status"], "failed")

    def test_success_and_repeated_start_do_not_reset_paper(self):
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=self.start_services):
            self.assertEqual(paper_control.paper_start(), 0)
            paper_run = load_run_manifest(self.data)["run_id"]
            self.assertNotEqual(paper_run, self.manifest["run_id"])
            self.assertEqual(read_json(self.data / "paper_activation.json")["status"], "active")
            self.assertEqual(paper_control.paper_start(), 0)
            self.assertEqual(load_run_manifest(self.data)["run_id"], paper_run)
            self.assertTrue((self.data / "runs" / self.manifest["run_id"] / "portfolio_state.json").exists())

    def test_interrupted_post_start_preserves_pending_run_then_resumes(self):
        def interrupt(action):
            if action == "start":
                raise KeyboardInterrupt()
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=interrupt):
            self.assertEqual(paper_control.paper_start(), 2)
        paper_run = load_run_manifest(self.data)["run_id"]
        self.assertEqual(read_json(self.data / "paper_activation.json")["status"], "failed")
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=self.start_services), \
             mock.patch.object(run_state, "archive") as archive:
            self.assertEqual(paper_control.paper_start(), 0)
            archive.assert_not_called()
        self.assertEqual(load_run_manifest(self.data)["run_id"], paper_run)

    def test_creation_failure_after_archive_is_resumable(self):
        real_install = paper_control.install_transition
        def interrupted_install(data, intent):
            run_state.clear(True)
            raise OSError("disk error before manifest install")
        with self.controller_context(), mock.patch.object(paper_control, "services"), \
             mock.patch.object(paper_control, "install_transition", side_effect=interrupted_install):
            self.assertEqual(paper_control.paper_start(), 2)
        target = read_json(self.data / "paper_transition.json")["manifest"]["run_id"]
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=self.start_services), \
             mock.patch.object(run_state, "archive") as archive:
            self.assertEqual(paper_control.paper_start(), 0)
            archive.assert_not_called()
        self.assertEqual(load_run_manifest(self.data)["run_id"], target)

    def test_post_start_timeout_stops_without_deleting_new_run(self):
        initial = self.report_for_test()
        failed = dict(initial, ready=False, blockers=[dict(key="healthy_cycles", message="timeout")])
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=self.start_services) as service, \
             mock.patch.object(paper_preflight, "wait_report", side_effect=[initial, failed]):
            self.assertEqual(paper_control.paper_start(), 2)
            self.assertEqual(service.call_args.args[0], "stop")
        self.assertNotEqual(load_run_manifest(self.data)["run_id"], self.manifest["run_id"])
        self.assertEqual(read_json(self.data / "paper_activation.json")["status"], "failed")

    def test_crash_after_activation_only_finishes_commit_marker(self):
        with self.controller_context(), mock.patch.object(paper_control, "services", side_effect=self.start_services):
            self.assertEqual(paper_control.paper_start(), 0)
        intent = read_json(self.data / "paper_transition.json")
        atomic_json(self.data / "paper_transition.json", dict(intent, status="pending"))
        with self.controller_context(), mock.patch.object(paper_control, "services") as service:
            self.assertEqual(paper_control.paper_start(), 0)
            service.assert_not_called()
        self.assertEqual(read_json(self.data / "paper_transition.json")["status"], "committed")

    def report_for_test(self):
        return build_readiness(self.data, self.logs, ROOT, synthetic={"passed": True},
                               process_check=fixtures.PaperPreflightTests.process_state)


if __name__ == "__main__":
    unittest.main()
