import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from models import Position
from validation import evaluate_copy_run


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


if __name__ == "__main__":
    unittest.main()
