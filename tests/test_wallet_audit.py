import copy
import io
import json
import sys
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import dashboard
import wallet_audit
from backtester import Backtester
from scanner import PolymarketScanner
from wallet_audit_feed import FeedError, PublicResearchClient, round_robin_candidates
from wallet_history import reconstruct, window_metrics, exclusion_reasons, shortlist, wilson

NOW = 1788174000
ADDR = "0x" + "1" * 40


def trade(asset="a", side="BUY", size=10, cash=5, ts=NOW - 100, **extra):
    return {"type": "TRADE", "asset": asset, "conditionId": "c-" + asset,
            "eventSlug": "event-" + asset, "title": "Fed interest rates",
            "side": side, "size": size, "usdcSize": cash, "timestamp": ts, **extra}


def official(asset="a", shares=10, pnl=1):
    return {"asset": asset, "totalBought": shares, "realizedPnl": pnl,
            "timestamp": NOW - 50, "conditionId": "c-" + asset}


class ReconstructionTests(unittest.TestCase):
    def test_extreme_quotes_are_not_settlements(self):
        for price in (.99, .01, 1, 0):
            result = reconstruct([trade()], {"a": {"cur_price": price, "redeemable": False}})
            self.assertEqual(result["closed"], {})
            self.assertEqual(result["states"]["a"]["shares"], 10)

    def test_official_settlement_needs_exact_payout_and_is_undated(self):
        r = reconstruct([trade()], {"a": {"cur_price": 1, "redeemable": True}})
        self.assertEqual(r["closed"]["a"]["realized_pnl"], 5)
        self.assertIsNone(r["cycles"][0]["closed_at"])
        self.assertEqual(window_metrics(r, NOW - 1000, NOW)["closed_positions"], 0)

    def test_oversell_caps_proceeds_and_never_qualifies(self):
        r = reconstruct([trade(), trade(side="SELL", size=100, cash=60, ts=NOW - 50)])
        self.assertAlmostEqual(r["states"]["a"]["realized_pnl"], 1)
        self.assertEqual(r["states"]["a"]["shares"], 0)
        self.assertEqual(r["closed"], {})
        self.assertIn("incomplete_cost_basis:a", r["quality_errors"])
        bt = Backtester.__new__(Backtester)
        self.assertEqual(bt.reconstruct_positions([trade(), trade(side="SELL", size=100, cash=60)], {}), {})

    def test_no_buy_basis_never_becomes_zero_cost_profit(self):
        r = reconstruct([trade(side="SELL", cash=100)])
        self.assertTrue(r["quality_errors"])
        self.assertEqual(r["cycles"], [])

    def test_redemptions_match_asset_and_zero_payout_loser(self):
        a, b = trade(), trade(asset="b")
        a["conditionId"] = b["conditionId"] = "same-event"
        redemptions = [dict(t, type="REDEEM", side="", usdcSize=cash, timestamp=NOW - 50)
                       for t, cash in ((a, 10), (b, 0))]
        r = reconstruct([a, b] + redemptions)
        self.assertEqual(r["closed"]["a"]["realized_pnl"], 5)
        self.assertEqual(r["closed"]["b"]["realized_pnl"], -5)
        r = reconstruct([a, b, dict(redemptions[0], asset="")])
        self.assertTrue(r["quality_errors"])
        self.assertEqual(r["cycles"], [])

    def test_partial_sales_reopening_and_dedup(self):
        rows = [trade(), trade(side="SELL", size=5, cash=3, ts=NOW - 90),
                trade(side="SELL", size=5, cash=3, ts=NOW - 80),
                trade(ts=NOW - 70), trade(side="SELL", cash=4, ts=NOW - 60)]
        r = reconstruct(rows + rows, official_closed=[official(shares=20, pnl=0)], reconcile=True)
        self.assertEqual(r["quality_errors"], [])
        self.assertEqual([c["realized_pnl"] for c in r["cycles"]], [1, -1])
        self.assertEqual(len(r["activity"]), 5)

    def test_transaction_hash_not_alone_dedup_key(self):
        rows = [trade(transactionHash="same"), trade(asset="b", transactionHash="same")]
        self.assertEqual(len(reconstruct(rows)["activity"]), 2)

    def test_increments_are_not_new_entries(self):
        rows = [trade(size=5, cash=2.5), trade(size=5, cash=2.5, ts=NOW - 80),
                trade(side="SELL", cash=6, ts=NOW - 60)]
        r = reconstruct(rows, official_closed=[official()], reconcile=True)
        m = window_metrics(r, NOW - 1000, NOW)
        self.assertEqual(m["verified_new_entries"], 1)
        self.assertEqual(m["verified_increments"], 1)
        self.assertEqual(m["transactions"], 3)

    def test_official_mismatch_and_missing_basis_fail_closed(self):
        for reference in ([official(shares=100)], [official(pnl=999)], [], [official(), official(pnl=2)]):
            r = reconstruct([trade(), trade(side="SELL", cash=6, ts=NOW - 50)],
                            official_closed=reference, reconcile=True)
            self.assertTrue(r["quality_errors"])

    def test_invalid_and_non_directional_cashflows(self):
        for kind in ("SPLIT", "MERGE", "CONVERSION", "TRANSFER"):
            self.assertTrue(reconstruct([trade(type=kind)])["quality_errors"])
        for invalid in (None, "not-a-number", float("nan"), True):
            self.assertTrue(reconstruct([trade(cash=invalid)])["quality_errors"])
        r = reconstruct([trade(type="REWARD", cash=99), trade(),
                         trade(side="SELL", cash=6, ts=NOW - 50)])
        self.assertEqual(r["incentives_usdc"], 99)
        self.assertEqual(r["closed"]["a"]["realized_pnl"], 1)
        self.assertIsNone(r["copy_net_pnl"])

    def test_unexpected_current_inventory_invalidates_fully_sold_asset(self):
        reference = dict(official(), size=100, curPrice=.5)
        r = reconstruct([trade(), trade(side="SELL", cash=6, ts=NOW - 50)],
                        {"a": reference}, reconcile=True)
        self.assertIn("official_inventory_mismatch:a", r["quality_errors"])
        self.assertEqual(r["cycles"], [])

    def test_scanner_rejects_corrupt_or_truncated_history(self):
        scanner = PolymarketScanner()
        scanner.bt.fetch_activity = mock.Mock(return_value=[trade(), trade(side="SELL", size=100, cash=60)])
        scanner.bt.positions_map_result = mock.Mock(return_value={})
        self.assertEqual(scanner._wallet_realized_performance(ADDR)["status"], "unknown")
        scanner.bt.fetch_activity.return_value = [trade(), trade(side="SELL", cash=6, ts=NOW - 50)]
        scanner.bt.activity_truncated = True
        self.assertEqual(scanner._wallet_realized_performance(ADDR)["status"], "unknown")

    def test_wilson_small_sample_is_not_certainty(self):
        lower, upper = wilson(13, 13)
        self.assertLess(lower, .8)
        self.assertLessEqual(upper, 1)
        self.assertIsNone(wilson(0, 0))


class FeedTests(unittest.TestCase):
    def client(self, directory):
        return PublicResearchClient(directory, sleep=lambda _: None)

    def test_activity_splits_dense_windows_within_caps(self):
        with tempfile.TemporaryDirectory() as d:
            client = self.client(d)
            rows = [trade(asset=str(i), ts=i) for i in range(1, 6001)]
            calls = []
            def get(endpoint, params):
                calls.append(params)
                selected = [r for r in rows if params["start"] <= r["timestamp"] <= params["end"]]
                return selected[params["offset"]:params["offset"] + params["limit"]]
            client.get = get
            result = client.activity(ADDR, 1, 6000)
            self.assertEqual(result["coverage"], "complete")
            self.assertEqual(len(result["rows"]), 6000)
            self.assertTrue(all(c["limit"] <= 500 and c["offset"] <= 5000 for c in calls))
            self.assertGreater(len({(c["start"], c["end"]) for c in calls}), 1)

    def test_repeated_pages_and_limits_are_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.client(d)
            c.get = lambda *args: [trade(ts=1)] * 500
            self.assertEqual(c.activity(ADDR, 1, 1)["coverage"], "unknown")
            c.get = mock.Mock(side_effect=FeedError("HTTP429"))
            self.assertEqual(c.activity(ADDR, 1, 2)["coverage"], "unknown")
            c.get.return_value = []
            c.get.side_effect = None
            self.assertEqual(c.activity(ADDR, 1, 2)["coverage"], "complete")

    def test_pacing_backoff_cache_and_no_network_offline(self):
        with tempfile.TemporaryDirectory() as d:
            stamp, starts, sleeps = [0.0], [], []
            def sleep(value):
                sleeps.append(value)
                stamp[0] += value
            good = mock.Mock(status_code=200)
            good.json.return_value = []
            bad = mock.Mock(status_code=429, headers={"Retry-After": "3"})
            bad.raise_for_status.side_effect = requests.HTTPError("429")
            session = mock.Mock()
            responses = iter([bad, good, good])
            def get(*args, **kwargs):
                starts.append(stamp[0])
                return next(responses)
            session.get.side_effect = get
            c = PublicResearchClient(d, session=session, sleep=sleep, clock=lambda: stamp[0])
            self.assertEqual(c.get("/activity", {"user": ADDR}), [])
            self.assertEqual(c.get("/activity", {"user": ADDR}), [])
            self.assertEqual(c.get("/positions", {"user": ADDR}), [])
            self.assertIn(3, sleeps)
            self.assertTrue(all(b - a >= .5 for a, b in zip(starts, starts[1:])))
            self.assertEqual(c.cache_hits, 1)
            offline = PublicResearchClient(d, session=mock.Mock(), offline=True)
            self.assertEqual(offline.get("/activity", {"user": ADDR}), [])
            with self.assertRaises(FeedError):
                offline.get("/closed-positions", {"user": ADDR})
            offline.session.get.assert_not_called()

    def test_positions_caps_and_unknown_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.client(d)
            c.get = mock.Mock(return_value=[official()])
            self.assertEqual(c.positions(ADDR, closed=True)["coverage"], "complete")
            self.assertEqual(c.get.call_args.args[1]["limit"], 50)
            c.get.side_effect = FeedError("timeout")
            self.assertEqual(c.positions(ADDR)["coverage"], "unknown")

    def test_dense_single_second_never_silently_truncates(self):
        with tempfile.TemporaryDirectory() as d:
            c = self.client(d)
            c.get = lambda _, p: [trade(asset=str(i), ts=1) for i in range(p['offset'], p['offset'] + 500)]
            r = c.activity(ADDR, 1, 1)
            self.assertEqual(r["coverage"], "unknown")
            self.assertIn("activity_single_second_overflow", r["errors"])

    def test_request_budget_and_timeouts_are_unknown_not_empty(self):
        with tempfile.TemporaryDirectory() as d:
            session = mock.Mock()
            session.get.side_effect = requests.Timeout("test")
            c = PublicResearchClient(d, session=session, sleep=lambda _: None, max_requests=2)
            r = c.activity(ADDR, 1, 2)
            self.assertEqual(r["coverage"], "unknown")
            self.assertEqual(c.requests, 2)

    def test_tls_failure_never_disables_verification_or_retries_other_wallets(self):
        with tempfile.TemporaryDirectory() as d:
            session = mock.Mock()
            session.get.side_effect = requests.exceptions.SSLError("hostname mismatch")
            c = PublicResearchClient(d, session=session, sleep=lambda _: None)
            for addr in (ADDR, "0x" + "2" * 40):
                r = c.activity(addr, 1, 2)
                self.assertEqual(r["errors"], ["tls_verification_failed"])
            self.assertEqual(session.get.call_count, 1)
            self.assertNotEqual(session.get.call_args.kwargs.get("verify"), False)

    def test_round_robin_retains_current_and_anonymous_wallets(self):
        current = [{"address": ADDR, "name": "current"}]
        sources = {"a": [{"proxyWallet": "0x" + f"{i:040x}"} for i in range(2, 6)],
                   "b": [{"proxyWallet": "0x" + "9" * 40}, {"address": ADDR}]}
        rows = round_robin_candidates(current, sources, 3)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["address"], ADDR)
        self.assertEqual(rows[2]["address"], "0x" + "9" * 40)
        self.assertTrue(all(p.get("name") for p in rows))


class ReportTests(unittest.TestCase):
    def profile(self, wr=.59, pnl=10):
        base = {"closed_positions": 60, "distinct_events": 22, "realized_pnl": pnl,
                "buy_assets_ge_5": 15, "buy_days_ge_5": 4, "last_buy_at": NOW - 1,
                "win_rate": wr, "profit_factor": 1.5, "profit_factor_unbounded": False}
        return {"address": ADDR, "coverage": "complete", "quality_errors": [],
                "windows": {str(d): dict(base) for d in (7, 30, 90)}}

    def test_profit_and_activity_not_wr_cutoff(self):
        p = self.profile(.59)
        self.assertEqual(exclusion_reasons(p, NOW), [])
        self.assertIn("nonpositive_30_or_90_day_pnl", exclusion_reasons(self.profile(.99, -5), NOW))
        p["windows"]["90"]["closed_positions"] = 13
        self.assertIn("fewer_than_50_closures", exclusion_reasons(p, NOW))

    def test_shortlist_deterministic_max20_and_never_relaxes(self):
        profiles = [dict(self.profile(), address=f"0x{i:040x}", exclusion_reasons=[]) for i in range(30)]
        self.assertEqual(len(shortlist(profiles)), 20)
        for p in profiles:
            p["exclusion_reasons"] = ["quarantined"]
        self.assertEqual(shortlist(profiles), [])

    def make_snapshot(self, directory):
        directory.mkdir()
        wallet = {"address": ADDR, "name": "test", "allowed_domains": ["macro"]}
        files = {
            "run_manifest.json": {"manifest_version": 1, "run_id": "run-test", "wallets": [wallet]},
            "monitored_wallets.json": {"run_id": "run-test", "wallets": [wallet]},
            "portfolio_state.json": {"run_id": "run-test", "initial_capital": 300, "cash": 300,
                                     "positions": {}, "closed_positions": []},
            "wallet_validation_registry.json": {"registry_version": 1, "wallets": {}},
        }
        for name, value in files.items():
            (directory / name).write_text(json.dumps(value), encoding="utf-8")
        return directory

    def test_end_to_end_report_leaves_all_snapshot_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            source = self.make_snapshot(Path(d) / "snapshot")
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            snapshot = wallet_audit.read_snapshot(source)
            output, stamp = wallet_audit.prepare_output(Path(d) / "research", snapshot, NOW)
            client = mock.Mock(requests=0, cache_hits=0)
            client.activity.return_value = {"coverage": "complete", "errors": [],
                "rows": [trade(), trade(side="SELL", cash=6, ts=NOW - 50)]}
            client.positions.side_effect = [
                {"coverage": "complete", "errors": [], "rows": [official()]},
                {"coverage": "complete", "errors": [], "rows": []}]
            report = wallet_audit.run_audit(snapshot, output, stamp, client=client, max_new=0)
            self.assertEqual(report["profiles"][0]["windows"]["90"]["realized_pnl"], 1)
            self.assertEqual(report["profiles"][0]["paper"]["realized_net_pnl"], 0)
            self.assertEqual(report["shortlist"], [])
            self.assertFalse(report["real_money_authorized"])
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})
            self.assertTrue((output / "report.md").exists())
            strict = json.loads((output / "report.json").read_text())
            self.assertEqual(strict["current_wallet_count"], 1)
            self.assertEqual(wallet_audit.prepare_output(output, snapshot)[1], NOW)
            with self.assertRaises(ValueError):
                wallet_audit.prepare_output(output, snapshot, NOW + 1)
            with self.assertRaises(ValueError):
                wallet_audit.prepare_output(source / "output", snapshot)

    def test_archive_never_extracts_and_rejects_traversal_or_link(self):
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as f:
                member = tarfile.TarInfo("../run_manifest.json")
                member.size = 2
                f.addfile(member, io.BytesIO(b"{}"))
            with self.assertRaises(ValueError):
                wallet_audit.read_snapshot(archive)
            self.assertFalse((Path(d) / "run_manifest.json").exists())

    def test_unknown_registry_never_qualifies(self):
        p = self.profile()
        p["quality_errors"] = ["quarantine_registry_unknown"]
        self.assertIn("data_quality_unknown", exclusion_reasons(p, NOW))

    def test_missing_feed_shows_null_pnl_not_zero(self):
        client = mock.Mock()
        client.activity.return_value = {"rows": [], "coverage": "unknown", "errors": ["timeout"]}
        client.positions.return_value = {"rows": [], "coverage": "complete", "errors": []}
        result = wallet_audit.audit_wallet(client, {"address": ADDR}, NOW, set())
        self.assertIsNone(result["windows"]["90"]["realized_pnl"])
        self.assertIn("data_quality_unknown", result["exclusion_reasons"])

    def test_help_and_import_do_not_create_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, POLYMARKET_DATA_DIR=str(Path(d) / "forbidden_data"),
                       POLYMARKET_LOGS_DIR=str(Path(d) / "forbidden_logs"), PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run([sys.executable, str(ROOT / "tools/wallet_audit.py"), "--help"],
                                    env=env, cwd=d, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(d) / "forbidden_data").exists())
            self.assertFalse((Path(d) / "forbidden_logs").exists())

    def test_archive_roundtrip_reads_exactly_same_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            source = self.make_snapshot(Path(d) / "snapshot")
            archive = Path(d) / "snapshot.tar.gz"
            with tarfile.open(archive, "w:gz") as f:
                f.add(source, arcname="export/data")
            self.assertEqual(wallet_audit.read_snapshot(archive)["hashes"], wallet_audit.read_snapshot(source)["hashes"])

    def test_output_rejects_repo_runtime_and_existing_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as d:
            source = self.make_snapshot(Path(d) / "snapshot")
            snap = wallet_audit.read_snapshot(source)
            with self.assertRaises(ValueError):
                wallet_audit.prepare_output(ROOT / "data" / "audit", snap, NOW)
            folder = Path(d) / "unrelated"
            folder.mkdir()
            (folder / "precious.txt").write_text("unchanged")
            with self.assertRaises(ValueError):
                wallet_audit.prepare_output(folder, snap, NOW)
            self.assertEqual((folder / "precious.txt").read_text(), "unchanged")

    def test_dashboard_legacy_metrics_marked_unverified_without_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            source = self.make_snapshot(Path(d) / "snapshot")
            original = (source / "monitored_wallets.json").read_bytes()
            with mock.patch.object(dashboard, "DATA_DIR", source):
                rows = dashboard.get_monitored_wallets()
                html = dashboard.app.test_client().get("/")
            self.assertEqual(rows[0]["metrics_provenance"]["quality"], "legacy_unverified")
            self.assertIsNone(rows[0]["metrics_provenance"]["scan_at"])
            self.assertIsNone(rows[0]["profit"])
            self.assertIsNone(rows[0]["win_rate"])
            self.assertIn(b"Statistiche storiche dello scan", html.data)
            self.assertEqual((source / "monitored_wallets.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
