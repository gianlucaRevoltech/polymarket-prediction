import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import simulator as simulator_module
from categories import categorize_market, taker_fee_fraction
from config import EXECUTION
from portfolio_sync import PolymarketPositionFetcher
from simulator import PaperTradingSimulator
from time_utils import utc_now_iso


class FakeFetcher:
    def __init__(self):
        self.books = {}
        self.markets = {}
        self.market_calls = []

    def get_book(self, asset):
        return self.books.get(asset)

    def get_books(self, assets):
        return {asset: self.books[asset] for asset in assets if asset in self.books}

    def get_executable_price(self, asset, side, size_shares=0):
        book = self.get_book(asset) or {}
        override = "buy_vwap" if side == "BUY" else "sell_vwap"
        if override in book:
            return book[override]
        return book.get("best_ask" if side == "BUY" else "best_bid")

    def get_market(self, condition_id):
        self.market_calls.append(condition_id)
        if condition_id in self.markets:
            return self.markets[condition_id]
        return {
            "fees_enabled": False,
            "fee_schedule": None,
            "fee_metadata_known": True,
        }

    @staticmethod
    def passes_liquidity(book, side_size_min, max_spread_ticks=3):
        return bool(
            book and book.get("ask_size", 0) >= side_size_min
            and book.get("bid_size", 0) >= side_size_min
            and book.get("spread", 1) <= max_spread_ticks * 0.01
        )

    @staticmethod
    def days_to_expiry(_value):
        return 10.0


def book(bid=0.49, ask=0.50, depth=100.0):
    return {
        "best_bid": bid, "best_ask": ask,
        "bid_size": depth, "ask_size": depth,
        "spread": ask - bid, "mid": (ask + bid) / 2,
        "observed_at": utc_now_iso(),
        "bid_levels": [{"price": bid, "size": depth}],
        "ask_levels": [{"price": ask, "size": depth}],
    }


def candidate(asset="asset-1", condition="cond-1", event="fed-decision-in-july-181"):
    return {
        "asset": asset,
        "condition_id": condition,
        "title": "Will there be no change in Fed interest rates after July?",
        "slug": "fed-no-change-july",
        "event_id": "181",
        "event_slug": event,
        "outcome": "Yes",
        "avg_price": 0.50,
        "cur_price": 0.495,
        "notional_usdc": 100,
        "category": "macro",
        "redeemable": False,
        "end_date_iso": "",
        "transaction_hash": f"tx-{asset}",
        "source_trade_status": "ok",
        "source_trade_at": utc_now_iso(),
        "source_trade_price": 0.50,
        "source_trade_size": 25.0,
    }


class SimulatorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.logs = Path(self.tmp.name) / "logs"
        self.data.mkdir()
        self.logs.mkdir()
        self.patch_data = mock.patch.object(simulator_module, "DATA_DIR", self.data)
        self.patch_logs = mock.patch.object(simulator_module, "LOGS_DIR", self.logs)
        self.patch_data.start()
        self.patch_logs.start()
        self.mode = mock.patch.dict(EXECUTION, {"mode": "paper_validation"})
        self.mode.start()

    def tearDown(self):
        self.mode.stop()
        self.patch_logs.stop()
        self.patch_data.stop()
        self.tmp.cleanup()

    def _allow_high_roundtrip_cost(self):
        patcher = mock.patch.dict(
            simulator_module.STRATEGY,
            {"max_immediate_roundtrip_cost_pct": 1.0},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_observe_journals_but_never_opens(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        self.assertEqual(sim.portfolio.open_positions_count, 0)
        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["journal_version"], 6)
        self.assertEqual(rows[-1]["num_holders"], 1)
        self.assertEqual(rows[-1]["source_trade_size"], 25.0)
        self.assertEqual(rows[-1]["decision"], "eligible")
        self.assertEqual(rows[-1]["reason"], "passed_pretrade_checks")
        self.assertEqual(rows[-1]["best_ask"], 0.50)
        self.assertEqual(rows[-1]["executable_ask_vwap"], 0.50)
        self.assertAlmostEqual(rows[-1]["executable_bid_vwap"], 0.49)
        self.assertEqual(rows[-1]["source_trade_status"], "ok")
        self.assertEqual(rows[-1]["source_trade_price"], 0.50)
        self.assertEqual(rows[-1]["ask_levels_used"][0]["price"], 0.50)
        self.assertEqual(sim.portfolio.cash, 300.0)
        self.assertEqual(sim.recent_opens, {})
        self.assertEqual(len(sim.shadow_positions), 1)
        self.assertTrue((self.data / "shadow_state.json").exists())

    def test_paper_startup_guard_evaluates_but_never_opens(self):
        sim = PaperTradingSimulator()
        sim.opening_guard = lambda: "startup_verification_pending"
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        self.assertEqual(sim.portfolio.cash, 300)
        self.assertEqual(sim.recent_opens, {})
        row = json.loads(sim.candidate_journal.read_text().splitlines()[-1])
        self.assertTrue(row["pretrade_eligible"])
        self.assertEqual(row["reason"], "startup_verification_pending")

    def test_strict_save_failure_does_not_claim_fresh_ledger(self):
        sim = PaperTradingSimulator()
        before = sim.state_saved_at
        with mock.patch.object(sim, "_atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sim._save_state(strict=True)
        self.assertEqual(sim.state_saved_at, before)

    def test_corrupt_ledger_without_backup_is_never_reset(self):
        path = self.data / "portfolio_state.json"
        path.write_text("corrupt")
        sim = PaperTradingSimulator()
        self.assertTrue(sim.run_integrity_error)
        with self.assertRaises(ValueError):
            sim._save_state(strict=True)
        self.assertEqual(path.read_text(), "corrupt")

    def test_max_drawdown_survives_recovery_and_restart(self):
        sim = PaperTradingSimulator()
        sim.portfolio.cash = 297
        sim._save_state(strict=True)
        sim.portfolio.cash = 300
        sim._save_state(strict=True)
        restarted = PaperTradingSimulator()
        self.assertAlmostEqual(restarted.max_drawdown, .01)

    def test_take_profit_uses_net_bid_and_closes(self):
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        info = candidate()
        feed.books["asset-1"] = book()
        self.assertTrue(sim.open_position("wallet-a", info, fetcher=feed))
        sim.baseline_done = True
        feed.books["asset-1"] = book(bid=.70, ask=.71)
        sim.reconcile({"asset-1": {"info": info, "holders": {"wallet-a"}, "max_notional": 100}},
                      1, feed, new_holdings=set(), monitored_wallets={"wallet-a"}, failed_wallets=set())
        self.assertEqual(len(sim.portfolio.closed_positions), 1)
        self.assertEqual(sim.portfolio.closed_positions[0].close_reason, "take_profit")
        self.assertAlmostEqual(sim.portfolio.cash, 302.0)

    def test_source_notional_must_cover_fixed_paper_size(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-small"] = book()
        info = candidate(asset="asset-small", condition="cond-small")
        info["source_trade_size"] = 4.99

        self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))

        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(
            row["reason"], "source_trade_notional_below_paper_size"
        )
        self.assertFalse(row["pretrade_eligible"])
        self.assertEqual(len(sim.shadow_positions), 0)

    def test_immediate_roundtrip_cost_is_capped_before_shadow(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-costly"] = book(0.39, 0.40)
        feed.markets["cond-costly"] = {
            "category": "other",
            "fees_enabled": True,
            "fee_schedule": {
                "rate": 0.05, "exponent": 1.0, "taker_only": True,
            },
            "fee_metadata_known": True,
        }
        info = candidate(asset="asset-costly", condition="cond-costly")
        info["category"] = "other"

        self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))

        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(row["reason"], "immediate_roundtrip_cost_too_high")
        self.assertGreater(row["costs"]["immediate_roundtrip_loss_pct"], 0.025)
        self.assertEqual(len(sim.shadow_positions), 0)

    def test_consensus_and_holders_survive_journal_and_restart(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-consensus"] = book()
        info = candidate(asset="asset-consensus", condition="cond-consensus")
        info["holder_wallets"] = ["Wallet-B", "wallet-a"]

        self.assertFalse(
            sim.open_position("wallet-a", info, num_holders=2, fetcher=feed)
        )

        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(row["num_holders"], 2)
        self.assertEqual(row["holder_wallets"], ["wallet-a", "wallet-b"])
        restarted = PaperTradingSimulator()
        pos = next(iter(restarted.shadow_positions.values()))
        self.assertEqual(pos.num_holders, 2)
        self.assertEqual(pos.holder_wallets, ["wallet-a", "wallet-b"])

    def test_shadow_caps_each_wallet_to_twenty_percent_of_target_sample(self):
        EXECUTION["mode"] = "observe"
        with mock.patch.dict(EXECUTION, {"shadow_max_trades_per_wallet": 1}):
            sim = PaperTradingSimulator()
            feed = FakeFetcher()
            feed.books["asset-first"] = book()
            feed.books["asset-second"] = book()
            first = candidate(
                asset="asset-first", condition="cond-first", event="event-first"
            )
            second = candidate(
                asset="asset-second", condition="cond-second", event="event-second"
            )

            self.assertFalse(sim.open_position("wallet-a", first, fetcher=feed))
            pid = next(iter(sim.shadow_positions))
            self.assertTrue(sim._close_shadow(pid, 0.51, "exit"))
            self.assertFalse(sim.open_position("wallet-a", second, fetcher=feed))

            rows = [
                json.loads(line)
                for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
            ]
            self.assertEqual(rows[-1]["reason"], "wallet_sample_cap")
            self.assertEqual(len(sim.shadow_positions), 0)

    def test_paper_validation_uses_the_same_wallet_sample_cap(self):
        EXECUTION["mode"] = "paper_validation"
        with mock.patch.dict(EXECUTION, {"paper_max_trades_per_wallet": 1}):
            sim = PaperTradingSimulator()
            feed = FakeFetcher()
            feed.books["asset-first"] = book()
            feed.books["asset-second"] = book()
            first = candidate(
                asset="asset-first", condition="cond-first", event="event-first"
            )
            second = candidate(
                asset="asset-second", condition="cond-second", event="event-second"
            )

            self.assertTrue(sim.open_position("wallet-a", first, fetcher=feed))
            self.assertTrue(sim.close_by_asset("asset-first", 0.51, "exit"))
            self.assertFalse(sim.open_position("wallet-a", second, fetcher=feed))

            row = json.loads(
                (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
            )
            self.assertEqual(row["reason"], "wallet_sample_cap")
            self.assertEqual(sim.portfolio.open_positions_count, 0)

    def test_shadow_tracks_every_pretrade_pass_without_mutating_portfolio(self):
        EXECUTION["mode"] = "paper_validation"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        for index in range(1, 4):
            asset = f"asset-{index}"
            feed.books[asset] = book()
            self.assertEqual(
                sim.open_position(
                    "wallet-a", candidate(asset=asset, condition=f"cond-{index}",
                                            event=f"event-{index}"),
                    fetcher=feed,
                ),
                index <= 2,
            )

        self.assertEqual(sim.portfolio.open_positions_count, 2)
        self.assertEqual(len(sim.shadow_positions), 2)
        shadow_rows = [
            json.loads(line)
            for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(shadow_rows[-1]["action"], "rejected")
        summary = sim.get_shadow_summary()
        self.assertEqual(summary["rejected_candidates"], 1)
        self.assertEqual(summary["rejection_reasons"]["max_open_positions"], 1)
        self.assertEqual(shadow_rows[-1]["reason"], "max_open_positions")
        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        blocked = rows[-1]
        self.assertEqual(blocked["reason"], "max_open_positions")
        self.assertTrue(blocked["pretrade_eligible"])
        self.assertEqual(blocked["entry_price"], 0.50)
        self.assertEqual(blocked["planned_size_usdc"], 5.0)
        self.assertEqual(blocked["executable_ask_vwap"], 0.50)

    def test_sport_stop_uses_raw_ask_not_fee_loaded_entry(self):
        sim = PaperTradingSimulator()
        pos = simulator_module.Position(
            position_id="sport", market_title="Sport", market_slug="sport",
            condition_id="cond", outcome="Yes", entry_price=0.515,
            size_usdc=5.0, shares=5.0 / 0.515,
            entry_time=datetime.now(), source_wallet="wallet-a",
            asset="asset", category="sport", entry_best_ask=0.50,
        )
        self.assertEqual(
            sim._copy_sl_tp_decision(pos, 0.455, -0.08, 0.20), "hold"
        )
        self.assertEqual(
            sim._copy_sl_tp_decision(pos, 0.449, -0.08, 0.20), "stop_loss"
        )
        pos.entry_best_ask = None
        self.assertEqual(
            sim._copy_sl_tp_decision(pos, 0.455, -0.08, 0.20), "stop_loss"
        )

    def test_frozen_wallet_domains_reject_out_of_specialty_signal(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        (self.data / "monitored_wallets.json").write_text(json.dumps({
            "run_id": sim.run_id,
            "frozen": True,
            "domain_policy_version": 1,
            "intended_domains": ["macro"],
            "wallets": [{
                "address": "wallet-a", "allowed_domains": ["macro"],
            }],
        }), encoding="utf-8")
        feed = FakeFetcher()
        feed.books["asset-sport"] = book()
        info = candidate(asset="asset-sport", condition="cond-sport")
        info["category"] = "sport"
        self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))
        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(row["reason"], "wallet_domain_mismatch")
        self.assertEqual(len(sim.shadow_positions), 0)

    def test_shadow_one_position_per_event_and_reject_is_deduped(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        feed.books["asset-2"] = book()
        self.assertFalse(sim.open_position(
            "wallet-a", candidate(asset="asset-1", condition="cond-1", event="same"),
            fetcher=feed,
        ))
        second = candidate(asset="asset-2", condition="cond-2", event="same")
        self.assertFalse(sim.open_position("wallet-a", second, fetcher=feed))
        self.assertEqual(len(sim.shadow_positions), 1)
        rows = [
            json.loads(line)
            for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["reason"], "event_position_cap")
        restarted = PaperTradingSimulator()
        self.assertFalse(restarted.open_position("wallet-a", second, fetcher=feed))
        rows_after = [
            json.loads(line)
            for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows_after), len(rows))

    def test_shadow_mtm_halt_and_drawdown_persist(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        for index in range(2):
            asset = f"asset-risk-{index}"
            feed.books[asset] = book()
            self.assertFalse(sim.open_position(
                "wallet-a",
                candidate(asset=asset, condition=f"cond-risk-{index}",
                          event=f"event-risk-{index}"),
                fetcher=feed,
            ))
        for pos in sim.shadow_positions.values():
            pos.current_price = 0.19
            pos.current_price_net_of_exit_fee = True
        self.assertTrue(sim._update_shadow_risk().startswith("run_loss"))
        self.assertGreater(sim.shadow_max_drawdown, 0.02)
        sim._save_shadow_state()
        restarted = PaperTradingSimulator()
        self.assertTrue(restarted.shadow_halt_reason.startswith("run_loss"))
        self.assertGreater(restarted.shadow_max_drawdown, 0.02)

    def test_shadow_three_wallet_losses_create_cross_run_quarantine(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        for index in range(3):
            asset = f"asset-loss-{index}"
            feed.books[asset] = book()
            self.assertFalse(sim.open_position(
                "wallet-a",
                candidate(asset=asset, condition=f"cond-loss-{index}",
                          event=f"event-loss-{index}"),
                fetcher=feed,
            ))
            pid = next(iter(sim.shadow_positions))
            self.assertTrue(sim._close_shadow(pid, 0.45, "stop_loss"))
        registry = json.loads(
            (self.data / "wallet_validation_registry.json").read_text()
        )
        record = registry["wallets"]["wallet-a"]
        self.assertEqual(record["status"], "quarantined")
        self.assertEqual(record["trigger_run_id"], sim.run_id)
        self.assertEqual(sim.shadow_loss_streak, 3)
        self.assertIn("3 consecutive", sim.shadow_halt_reason)

    def test_shadow_stop_is_net_of_exit_fee_and_survives_restart(self):
        self._allow_high_roundtrip_cost()
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.markets["cond-1"] = {
            "fees_enabled": True,
            "fee_schedule": {"rate": 0.05, "exponent": 1.0},
            "fee_metadata_known": True,
            "category": "macro",
        }
        feed.books["asset-1"] = book(0.49, 0.50)
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        self.assertEqual(len(sim.shadow_positions), 1)

        restarted = PaperTradingSimulator()
        self.assertEqual(len(restarted.shadow_positions), 1)
        feed.books["asset-1"] = book(0.39, 0.40)
        aggregate = {
            "asset-1": {
                "holders": {"wallet-a"},
                "info": {**candidate(), "cur_price": 0.39},
            }
        }
        restarted.reconcile(
            aggregate, 1, feed, new_holdings=set(),
            monitored_wallets={"wallet-a"}, failed_wallets=set(),
        )

        self.assertEqual(len(restarted.shadow_positions), 0)
        self.assertEqual(len(restarted.shadow_closed_positions), 1)
        closed = restarted.shadow_closed_positions[0]
        self.assertEqual(closed.close_reason, "stop_loss")
        self.assertLess(closed.exit_price, 0.39)
        self.assertEqual(restarted.portfolio.cash, 300.0)
        self.assertEqual(restarted.portfolio.open_positions_count, 0)

    def test_shadow_batch_outage_preserves_mark_without_gamma_fanout(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        previous_mark = next(iter(sim.shadow_positions.values())).current_price
        calls_after_open = list(feed.market_calls)

        feed.books.clear()
        sim._manage_shadow_positions(
            {}, feed, monitored_wallets={"wallet-a"}, failed_wallets=set()
        )

        self.assertEqual(len(sim.shadow_positions), 1)
        self.assertEqual(
            next(iter(sim.shadow_positions.values())).current_price, previous_mark
        )
        self.assertEqual(feed.market_calls, calls_after_open)

        sim._manage_shadow_positions(
            {
                "asset-1": {
                    "holders": {"wallet-a"},
                    "info": {**candidate(), "redeemable": True, "cur_price": 1.0},
                }
            },
            feed,
            monitored_wallets={"wallet-a"},
            failed_wallets=set(),
        )
        self.assertEqual(len(sim.shadow_positions), 0)
        self.assertEqual(sim.shadow_closed_positions[0].close_reason, "resolved")
        self.assertEqual(feed.market_calls, calls_after_open)

    def test_shadow_resolution_uses_matching_token_outcome_price(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))

        feed.books["asset-1"] = {
            **book(),
            "bid_levels": [],
            "best_bid": None,
        }
        feed.markets["cond-1"] = {
            "closed": True,
            "tokens": ["asset-other", "asset-1"],
            "outcome_prices": ["0", "1"],
        }
        sim._manage_shadow_positions(
            {}, feed, monitored_wallets=set(), failed_wallets=set()
        )

        self.assertEqual(len(sim.shadow_positions), 0)
        self.assertEqual(len(sim.shadow_closed_positions), 1)
        closed = sim.shadow_closed_positions[0]
        self.assertEqual(closed.close_reason, "resolved")
        self.assertEqual(closed.exit_price, 1.0)

    def test_shadow_signal_is_not_duplicated_after_restart(self):
        EXECUTION["mode"] = "observe"
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        sim = PaperTradingSimulator()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        restarted = PaperTradingSimulator()
        self.assertFalse(
            restarted.open_position("wallet-a", candidate(), fetcher=feed)
        )
        self.assertEqual(len(restarted.shadow_positions), 1)
        lifecycle = [
            json.loads(line)
            for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(sum(row["action"] == "opened" for row in lifecycle), 1)

    def test_legacy_shadow_v1_is_preserved_but_cannot_accept_new_entries(self):
        EXECUTION["mode"] = "observe"
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        sim = PaperTradingSimulator()
        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))
        state_path = self.data / "shadow_state.json"
        state = json.loads(state_path.read_text())
        state["shadow_version"] = 1
        for key in (
            "initial_capital", "cash", "run_start_equity",
            "daily_start_equity", "daily_start_date", "peak_equity",
            "max_drawdown", "halt_reason", "loss_streak",
            "wallet_loss_streaks", "blocked_conditions",
            "legacy_unconstrained", "intended_domains",
        ):
            state.pop(key, None)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        restarted = PaperTradingSimulator()
        self.assertTrue(restarted.shadow_legacy_unconstrained)
        self.assertEqual(len(restarted.shadow_positions), 1)
        self.assertAlmostEqual(restarted.shadow_cash, 295.0)
        feed.books["asset-2"] = book()
        self.assertFalse(restarted.open_position(
            "wallet-a",
            candidate(asset="asset-2", condition="cond-2", event="event-2"),
            fetcher=feed,
        ))
        rows = [
            json.loads(line)
            for line in (self.data / "shadow_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            rows[-1]["reason"], "legacy_unconstrained_shadow_requires_new_run"
        )

    def test_observe_records_specific_pretrade_rejection_reasons(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()

        no_book = candidate(asset="asset-no-book", condition="cond-no-book")
        self.assertFalse(sim.open_position("wallet-a", no_book, fetcher=feed))

        feed.books["asset-band"] = book(0.89, 0.90)
        out_of_band = candidate(asset="asset-band", condition="cond-band")
        self.assertFalse(
            sim.open_position("wallet-a", out_of_band, num_holders=1, fetcher=feed)
        )

        feed.books["asset-drift"] = book(0.59, 0.60)
        drift = candidate(asset="asset-drift", condition="cond-drift")
        drift["source_trade_price"] = 0.40
        self.assertFalse(sim.open_position("wallet-a", drift, fetcher=feed))

        feed.books["asset-spread"] = book(0.40, 0.50)
        wide = candidate(asset="asset-spread", condition="cond-spread")
        self.assertFalse(sim.open_position("wallet-a", wide, fetcher=feed))

        feed.books["asset-depth"] = book(depth=10)
        shallow = candidate(asset="asset-depth", condition="cond-depth")
        self.assertFalse(sim.open_position("wallet-a", shallow, fetcher=feed))

        feed.books["asset-expiry"] = book()
        expires = candidate(asset="asset-expiry", condition="cond-expiry")
        expires["end_date_iso"] = "future"
        feed.days_to_expiry = lambda _value: 100.0
        self.assertFalse(sim.open_position("wallet-a", expires, fetcher=feed))

        feed.books["asset-vwap"] = {
            **book(),
            "ask_levels": [{"price": 0.50, "size": 1.0}],
        }
        no_full_fill = candidate(asset="asset-vwap", condition="cond-vwap")
        self.assertFalse(sim.open_position("wallet-a", no_full_fill, fetcher=feed))

        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [row["reason"] for row in rows],
            [
                "no_executable_two_sided_book",
                "entry_price_out_of_band",
                "entry_drift_too_high",
                "spread_too_wide",
                "insufficient_top_level_depth",
                "expiry_too_far",
                "insufficient_ask_depth_for_full_fill",
            ],
        )

    def test_signal_id_deduplicates_after_restart(self):
        EXECUTION["mode"] = "observe"
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        info = candidate()
        info["transaction_hash"] = "0xsource-trade"
        info["source_trade_at"] = utc_now_iso()

        sim = PaperTradingSimulator()
        self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))
        sim._save_state()
        journal = self.data / "candidate_journal.jsonl"
        self.assertEqual(len(journal.read_text().splitlines()), 1)

        restarted = PaperTradingSimulator()
        self.assertFalse(restarted.open_position("wallet-a", info, fetcher=feed))
        self.assertEqual(len(journal.read_text().splitlines()), 1)

    def test_duplicate_condition_and_event_survive_restart_and_cooldown(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        feed.books["asset-2"] = book(0.48, 0.50)
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        old = datetime.now() - timedelta(minutes=61)
        sim.recent_opens = {"asset-1": old, "cond-1": old}
        sim._save_recent_opens()
        sim._save_state()

        restarted = PaperTradingSimulator()
        same_condition = candidate(asset="asset-2", condition="cond-1")
        self.assertFalse(
            restarted.open_position("wallet-b", same_condition, fetcher=feed)
        )
        other_fed_market = candidate(asset="asset-2", condition="cond-2")
        self.assertFalse(
            restarted.open_position("wallet-b", other_fed_market, fetcher=feed)
        )
        self.assertEqual(restarted.portfolio.open_positions_count, 1)

    def test_event_cap_rejects_projected_size_above_three_percent(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        sim = PaperTradingSimulator(initial_capital=100.0)
        self.assertFalse(
            sim.open_position("wallet-a", candidate(), fetcher=feed)
        )
        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["reason"], "event_exposure_limit")

    def test_stop_loss_condition_block_persists(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        self.assertTrue(sim.close_by_asset("asset-1", 0.45, "stop_loss"))
        sim._save_state()

        restarted = PaperTradingSimulator()
        feed.books["asset-2"] = book()
        self.assertIn("cond-1", restarted.blocked_conditions)
        self.assertFalse(
            restarted.open_position(
                "wallet-a", candidate(asset="asset-2"), fetcher=feed
            )
        )

    def test_ask_entry_bid_mark_and_exit(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.49, 0.50)
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        pos = next(iter(sim.portfolio.positions.values()))
        self.assertEqual(pos.size_usdc, 5.0)
        self.assertAlmostEqual(pos.entry_price, 0.50, places=6)
        self.assertAlmostEqual(pos.current_price, 0.49, places=6)
        self.assertAlmostEqual(sim.portfolio.total_value, 299.90, places=6)
        sim.close_by_asset("asset-1", 0.49, "exit")
        self.assertAlmostEqual(sim.portfolio.closed_positions[-1].pnl, -0.10, places=6)

    def test_market_fee_schedule_applies_to_entry_journal_and_exit(self):
        fee_gate = mock.patch.dict(
            simulator_module.STRATEGY,
            {"max_immediate_roundtrip_cost_pct": 1.0},
        )
        fee_gate.start()
        self.addCleanup(fee_gate.stop)
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.39, 0.40)
        feed.markets["cond-1"] = {
            "category": "other",
            "fees_enabled": True,
            "fee_schedule": {
                "rate": 0.05, "exponent": 1.0, "taker_only": True,
            },
            "fee_metadata_known": True,
        }
        info = candidate()
        info["category"] = "other"
        sim = PaperTradingSimulator()

        self.assertTrue(sim.open_position("wallet-a", info, fetcher=feed))

        pos = next(iter(sim.portfolio.positions.values()))
        # fee/share = 0.05 * 0.40 * 0.60 = 0.012
        self.assertAlmostEqual(pos.entry_price, 0.412, places=9)
        # Mark liquidabile: bid 0.39 - fee/share 0.05*0.39*0.61.
        self.assertAlmostEqual(pos.current_price, 0.378105, places=9)
        self.assertTrue(pos.current_price_net_of_exit_fee)
        self.assertEqual(pos.fee_rate, 0.05)
        self.assertEqual(pos.fee_source, "market_fee_schedule")
        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["journal_version"], 6)
        self.assertEqual(rows[-1]["fee_schedule"]["rate"], 0.05)
        self.assertGreater(rows[-1]["costs"]["fee_usdc"], 0)

        sim._save_state()
        restarted = PaperTradingSimulator()
        restored = next(iter(restarted.portfolio.positions.values()))
        self.assertTrue(restored.fees_enabled)
        self.assertEqual(restored.fee_rate, 0.05)
        self.assertEqual(restored.fee_exponent, 1.0)
        self.assertAlmostEqual(restored.current_price, 0.378105, places=9)
        self.assertTrue(restored.current_price_net_of_exit_fee)

        self.assertTrue(sim.close_by_asset("asset-1", 0.50, "exit"))
        closed = sim.portfolio.closed_positions[-1]
        # exit fee/share = 0.05 * 0.50 * 0.50 = 0.0125
        self.assertAlmostEqual(closed.exit_price, 0.4875, places=9)
        self.assertAlmostEqual(closed.current_price, 0.4875, places=9)
        self.assertAlmostEqual(
            sim.portfolio.cash,
            sim.portfolio.initial_capital + closed.pnl,
            places=9,
        )
        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual(rows[-1]["decision"], "closed")
        self.assertEqual(rows[-1]["fee_schedule"]["rate"], 0.05)
        self.assertGreater(rows[-1]["costs"]["exit_fee_usdc"], 0)

    def test_state_v2_gross_bid_is_migrated_once_without_reset(self):
        self._allow_high_roundtrip_cost()
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.45, 0.46)
        feed.markets["cond-1"] = {
            "category": "other",
            "fees_enabled": True,
            "fee_schedule": {
                "rate": 0.05, "exponent": 1.0, "taker_only": True,
            },
            "fee_metadata_known": True,
        }
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        run_id = sim.run_id
        sim._save_state()

        state_path = self.data / "portfolio_state.json"
        state = json.loads(state_path.read_text())
        state["state_version"] = 2
        stored = next(iter(state["positions"].values()))
        stored["current_price"] = 0.45
        stored.pop("current_price_net_of_exit_fee", None)
        state_path.write_text(json.dumps(state))

        migrated = PaperTradingSimulator()
        pos = next(iter(migrated.portfolio.positions.values()))
        self.assertEqual(migrated.run_id, run_id)
        self.assertAlmostEqual(pos.current_price, 0.437625, places=9)
        self.assertTrue(pos.current_price_net_of_exit_fee)
        self.assertAlmostEqual(
            migrated.portfolio.total_value,
            295.0 + pos.shares * 0.437625,
            places=9,
        )

        migrated._save_state()
        reloaded = PaperTradingSimulator()
        pos_again = next(iter(reloaded.portfolio.positions.values()))
        self.assertAlmostEqual(pos_again.current_price, 0.437625, places=9)

    def test_state_v2_gross_close_cash_is_rebuilt_from_net_exits(self):
        self._allow_high_roundtrip_cost()
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.39, 0.40)
        feed.markets["cond-1"] = {
            "category": "other",
            "fees_enabled": True,
            "fee_schedule": {
                "rate": 0.05, "exponent": 1.0, "taker_only": True,
            },
            "fee_metadata_known": True,
        }
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        self.assertTrue(sim.close_by_asset("asset-1", 0.50, "exit"))
        sim._save_state()

        state_path = self.data / "portfolio_state.json"
        state = json.loads(state_path.read_text())
        state["state_version"] = 2
        stored = state["closed_positions"][0]
        stored["current_price"] = 0.50
        stored.pop("current_price_net_of_exit_fee", None)
        state["cash"] = 295.0 + stored["shares"] * 0.50
        state_path.write_text(json.dumps(state))

        migrated = PaperTradingSimulator()
        closed = migrated.portfolio.closed_positions[0]
        expected_cash = 300.0 + closed.pnl
        self.assertAlmostEqual(migrated.portfolio.cash, expected_cash, places=9)
        self.assertAlmostEqual(migrated.portfolio.total_value, expected_cash, places=9)

    def test_fee_schedule_unknown_rejects_candidate_fail_closed(self):
        EXECUTION["mode"] = "observe"
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        feed.markets["cond-1"] = None
        sim = PaperTradingSimulator()

        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))

        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(row["reason"], "fee_schedule_unavailable")

    def test_fee_formula_matches_official_usdc_curve(self):
        schedule = {"rate": 0.05, "exponent": 1.0, "taker_only": True}
        # 100 shares @ 0.30: fee = 100 * .05 * .30 * .70 = $1.05.
        fraction = taker_fee_fraction(
            "other", 0.30, fee_schedule=schedule, fees_enabled=True
        )
        self.assertAlmostEqual(100 * 0.30 * fraction, 1.05, places=9)

    def test_observe_still_manages_preexisting_positions(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.49, 0.50)
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))
        sim.execution_mode = "observe"
        feed.books["asset-1"] = book(0.45, 0.46)
        aggregate = {
            "asset-1": {
                "info": candidate(),
                "holders": {"wallet-a"},
                "max_notional": 100,
            }
        }
        sim.reconcile(
            aggregate, 1, feed, new_holdings=set(),
            monitored_wallets={"wallet-a"},
        )
        self.assertEqual(sim.portfolio.open_positions_count, 0)
        self.assertEqual(sim.portfolio.closed_positions[-1].close_reason, "stop_loss")

    def test_run_equity_halt_persists(self):
        sim = PaperTradingSimulator()
        sim._save_state()
        sim.portfolio.cash = 294.0
        self.assertTrue(sim._evaluate_equity_halts().startswith("run_loss"))
        sim._save_state()
        restarted = PaperTradingSimulator()
        self.assertTrue(restarted._opening_halt_reason("copy").startswith("run_loss"))

    def test_daily_equity_halt_persists_across_restart(self):
        sim = PaperTradingSimulator()
        sim._save_state()
        sim.portfolio.cash = 297.0
        self.assertTrue(sim._evaluate_equity_halts().startswith("daily_loss"))
        sim._save_state()
        restarted = PaperTradingSimulator()
        self.assertTrue(
            restarted._opening_halt_reason("copy").startswith("daily_loss")
        )

    def test_three_losses_quarantine_until_manual_reactivation(self):
        feed = FakeFetcher()
        sim = PaperTradingSimulator()
        for index in range(3):
            asset = f"asset-{index}"
            condition = f"cond-{index}"
            feed.books[asset] = book()
            info = candidate(
                asset=asset, condition=condition, event=f"event-{index}"
            )
            self.assertTrue(
                sim.open_position(f"wallet-{index}", info, fetcher=feed)
            )
            self.assertTrue(sim.close_by_asset(asset, 0.45, "stop_loss"))
        self.assertIn("copy", sim.quarantined_strategies)
        sim._save_state()

        restarted = PaperTradingSimulator()
        feed.books["asset-4"] = book()
        self.assertIn("copy", restarted.quarantined_strategies)
        self.assertFalse(
            restarted.open_position(
                "wallet-4",
                candidate(asset="asset-4", condition="cond-4", event="event-4"),
                fetcher=feed,
            )
        )
        restarted.reactivate_strategy("copy")
        feed.books["asset-5"] = book()
        self.assertTrue(
            restarted.open_position(
                "wallet-4",
                candidate(asset="asset-5", condition="cond-5", event="event-5"),
                fetcher=feed,
            )
        )

    def test_legacy_snapshot_migrates_and_peak_is_initial_capital(self):
        source = ROOT / "logs_current_2026-07-23" / "portfolio_state.json"
        shutil.copy2(source, self.data / "portfolio_state.json")
        sim = PaperTradingSimulator()
        self.assertAlmostEqual(sim.portfolio.cash, 297.0869, places=4)
        self.assertEqual(sim.portfolio.open_positions_count, 0)
        self.assertEqual(len(sim.portfolio.closed_positions), 5)
        self.assertTrue(all(p.event_slug == "" for p in sim.portfolio.closed_positions))
        summary = sim.get_portfolio_summary()
        self.assertEqual(summary["peak_equity"], 300.0)
        self.assertAlmostEqual(summary["drawdown_pct"], (300 - 297.0869) / 300, places=6)

    def test_macro_and_geopolitics_classification(self):
        self.assertEqual(
            categorize_market(
                "Will the Fed increase interest rates by 25 bps?",
                tags=[{"label": "Federal Reserve"}],
            ),
            "macro",
        )
        self.assertEqual(
            categorize_market("Will Israel and Iran agree to a ceasefire?"),
            "geopolitics",
        )

    def test_source_trade_is_required_recent_and_verified(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        reasons = []

        cases = [
            ("asset-missing", {"source_trade_status": "not_found"},
             "source_trade_unavailable"),
            ("asset-error", {"source_trade_status": "error"},
             "source_trade_lookup_error"),
            ("asset-no-hash", {"transaction_hash": ""},
             "source_trade_missing_transaction_hash"),
            (
                "asset-stale",
                {"source_trade_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=61)
                ).isoformat()},
                "source_trade_stale",
            ),
        ]
        for asset, overrides, expected in cases:
            feed.books[asset] = book()
            info = candidate(asset=asset, condition=f"cond-{asset}")
            info.update(overrides)
            self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))
            reasons.append(expected)

        rows = [
            json.loads(line)
            for line in (self.data / "candidate_journal.jsonl").read_text().splitlines()
        ]
        self.assertEqual([row["reason"] for row in rows], reasons)

    def test_drift_uses_source_trade_price_not_wallet_average(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book(0.52, 0.53)
        info = candidate()
        info["avg_price"] = 0.10
        info["source_trade_price"] = 0.50

        self.assertFalse(sim.open_position("wallet-a", info, fetcher=feed))
        row = json.loads(
            (self.data / "candidate_journal.jsonl").read_text().splitlines()[-1]
        )
        self.assertEqual(row["decision"], "eligible")

    def test_pretrade_vwap_uses_exactly_one_book_snapshot(self):
        EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator()
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        original_get_book = feed.get_book
        feed.get_book = mock.Mock(wraps=original_get_book)
        feed.get_executable_price = mock.Mock(
            wraps=feed.get_executable_price
        )

        self.assertFalse(sim.open_position("wallet-a", candidate(), fetcher=feed))

        self.assertEqual(feed.get_book.call_count, 1)
        feed.get_executable_price.assert_not_called()

    def test_failed_source_wallet_does_not_trigger_false_exit(self):
        feed = FakeFetcher()
        feed.books["asset-1"] = book()
        sim = PaperTradingSimulator()
        self.assertTrue(sim.open_position("wallet-a", candidate(), fetcher=feed))

        # Il wallet sorgente detiene ancora l'asset: la perdita del consenso
        # non equivale a una vendita.
        sim.reconcile(
            {
                "asset-1": {
                    "info": candidate(),
                    "holders": {"wallet-a"},
                    "max_notional": 100,
                }
            },
            2, feed, new_holdings=set(),
            monitored_wallets={"wallet-a"}, failed_wallets=set(),
        )
        self.assertEqual(sim.portfolio.open_positions_count, 1)

        sim.reconcile(
            {}, 1, feed, new_holdings=set(),
            monitored_wallets={"wallet-a"}, failed_wallets={"wallet-a"},
        )
        self.assertEqual(sim.portfolio.open_positions_count, 1)

        sim.reconcile(
            {}, 1, feed, new_holdings=set(),
            monitored_wallets={"wallet-a"}, failed_wallets=set(),
        )
        self.assertEqual(sim.portfolio.open_positions_count, 0)
        self.assertEqual(sim.portfolio.closed_positions[-1].close_reason, "exit")

    def test_executable_price_walks_depth_as_vwap(self):
        fetcher = PolymarketPositionFetcher()
        depth_book = {
            "best_bid": 0.49,
            "best_ask": 0.50,
            "bid_levels": [
                {"price": 0.49, "size": 4},
                {"price": 0.47, "size": 6},
            ],
            "ask_levels": [
                {"price": 0.50, "size": 5},
                {"price": 0.52, "size": 5},
            ],
        }
        with mock.patch.object(fetcher, "get_book", return_value=depth_book):
            self.assertAlmostEqual(
                fetcher.get_executable_price("asset", "BUY", 10), 0.51
            )
            self.assertAlmostEqual(
                fetcher.get_executable_price("asset", "SELL", 10), 0.478
            )
            self.assertIsNone(
                fetcher.get_executable_price("asset", "BUY", 11)
            )


if __name__ == "__main__":
    unittest.main()
