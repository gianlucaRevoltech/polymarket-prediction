import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from models import Position
from validation import evaluate_copy_run, evaluate_shadow_run


class PromotionCriteriaTests(unittest.TestCase):
    def test_full_independent_sample_can_pass_but_never_authorizes_real_money(self):
        run_id = "run-validation"
        now = datetime.now()
        trades = []
        for index in range(100):
            pos = Position(
                position_id=str(index),
                market_title=f"Market {index}",
                market_slug=f"market-{index}",
                condition_id=f"condition-{index % 40}",
                outcome="Yes",
                entry_price=0.50,
                size_usdc=5.0,
                shares=10.0,
                entry_time=now - timedelta(days=15),
                source_wallet=f"wallet-{index}",
                asset=f"asset-{index}",
                run_id=run_id,
                signal_id=f"signal-{index}",
                event_slug=f"event-{index % 40}",
                category="macro" if index < 50 else "geopolitics",
                strategy="copy",
                current_price=0.51,
                exit_price=0.51,
                exit_time=now - timedelta(days=14) + timedelta(minutes=index),
                is_closed=True,
            )
            trades.append(pos)
        result = evaluate_copy_run(
            trades, run_id, intended_domains=["macro", "geopolitics"],
            now=now, bootstrap_iterations=1000,
        )
        self.assertTrue(result["eligible_for_paper_promotion"])
        self.assertFalse(result["real_money_authorized"])
        self.assertTrue(all(result["checks"].values()))

    def test_small_sample_is_not_eligible(self):
        result = evaluate_copy_run([], "empty", bootstrap_iterations=100)
        self.assertFalse(result["eligible_for_paper_promotion"])
        self.assertFalse(result["checks"]["closed_trades_at_least_100"])

    def test_single_wallet_sample_cannot_promote(self):
        run_id = "single-wallet"
        now = datetime.now()
        trades = []
        for index in range(100):
            trades.append(Position(
                position_id=str(index), market_title=f"Market {index}",
                market_slug=f"market-{index}", condition_id=f"cond-{index}",
                outcome="Yes", entry_price=0.50, size_usdc=5.0, shares=10.0,
                entry_time=now - timedelta(days=15), source_wallet="wallet-only",
                asset=f"asset-{index}", run_id=run_id,
                signal_id=f"signal-{index}", event_slug=f"event-{index}",
                category="macro", strategy="copy", current_price=0.51,
                exit_price=0.51, exit_time=now - timedelta(days=1),
                is_closed=True,
            ))

        result = evaluate_copy_run(
            trades, run_id, intended_domains=["macro"], now=now,
            bootstrap_iterations=500,
        )

        self.assertFalse(result["eligible_for_paper_promotion"])
        self.assertFalse(
            result["checks"]["distinct_source_wallets_at_least_5"]
        )
        self.assertFalse(
            result["checks"]["wallet_trade_concentration_at_most_20pct"]
        )
        self.assertEqual(result["metrics"]["distinct_source_wallets"], 1)
        self.assertEqual(result["metrics"]["max_wallet_trade_share"], 1.0)

    def test_bootstrap_respects_event_correlation(self):
        run_id = "clustered-run"
        now = datetime.now()
        trades = []
        index = 0
        # Il mean per trade e' positivo, ma dipende da soli 40 eventi con esiti
        # fortemente correlati al loro interno: il CI cluster deve includere zero.
        for event in range(40):
            count = 3 if event < 20 else 2
            exit_price = 0.51 if event < 20 else 0.49
            for _ in range(count):
                trades.append(Position(
                    position_id=str(index), market_title="Clustered",
                    market_slug=f"market-{index}", condition_id=f"cond-{event}",
                    outcome="Yes", entry_price=0.50, size_usdc=5.0,
                    shares=10.0, entry_time=now - timedelta(days=15),
                    source_wallet=f"wallet-{index}", asset=f"asset-{index}",
                    run_id=run_id, signal_id=f"signal-{index}",
                    event_slug=f"event-{event}", category="macro",
                    strategy="copy", current_price=exit_price,
                    exit_price=exit_price, exit_time=now - timedelta(days=1),
                    is_closed=True,
                ))
                index += 1
        result = evaluate_copy_run(
            trades, run_id, intended_domains=["macro"], now=now,
            bootstrap_iterations=3000,
        )
        self.assertGreater(result["metrics"]["ev_per_trade"], 0)
        self.assertLessEqual(result["metrics"]["bootstrap_ci95_lower_ev"], 0)
        self.assertEqual(result["metrics"]["bootstrap_unit"], "event_cluster")

    def test_shadow_can_only_promote_to_independent_paper(self):
        result = evaluate_shadow_run([], "shadow-run", bootstrap_iterations=100)
        self.assertFalse(result["eligible_for_independent_paper"])
        self.assertFalse(result["real_money_authorized"])
        self.assertEqual(result["validation_stage"], "shadow")
        self.assertNotIn("eligible_for_paper_promotion", result)
        self.assertFalse(result["checks"]["intended_domains_frozen"])

    def test_shadow_uses_mark_to_market_drawdown_override(self):
        result = evaluate_shadow_run(
            [], "shadow-run", intended_domains=["macro"],
            max_drawdown_override=0.04, bootstrap_iterations=100,
        )
        self.assertAlmostEqual(result["metrics"]["max_drawdown"], 0.04)
        self.assertFalse(result["checks"]["max_drawdown_at_most_3pct"])
        self.assertTrue(result["checks"]["intended_domains_frozen"])


if __name__ == "__main__":
    unittest.main()
