import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import simulator as simulator_module
from categories import categorize_market
from config import EXECUTION
from portfolio_sync import PolymarketPositionFetcher
from simulator import PaperTradingSimulator


class FakeFetcher:
    def __init__(self):
        self.books = {}
        self.markets = {}

    def get_book(self, asset):
        return self.books.get(asset)

    def get_executable_price(self, asset, side, size_shares=0):
        book = self.get_book(asset) or {}
        override = "buy_vwap" if side == "BUY" else "sell_vwap"
        if override in book:
            return book[override]
        return book.get("best_ask" if side == "BUY" else "best_bid")

    def get_market(self, condition_id):
        return self.markets.get(condition_id)

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
        self.assertEqual(rows[-1]["journal_version"], 2)
        self.assertEqual(rows[-1]["decision"], "eligible")
        self.assertEqual(rows[-1]["reason"], "passed_pretrade_checks")
        self.assertEqual(rows[-1]["best_ask"], 0.50)
        self.assertEqual(rows[-1]["executable_ask_vwap"], 0.50)
        self.assertEqual(rows[-1]["executable_bid_vwap"], 0.49)
        self.assertEqual(sim.portfolio.cash, 300.0)
        self.assertEqual(sim.recent_opens, {})

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
        drift["avg_price"] = 0.40
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
            "buy_vwap": None,
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
        info["source_trade_at"] = "2026-07-24T07:00:00+00:00"

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
