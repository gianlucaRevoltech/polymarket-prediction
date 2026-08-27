"""Smoke deterministico e isolato del lifecycle paper; non usa mai data/."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import run_manifest as run_manifest_module  # noqa: E402
import simulator as simulator_module  # noqa: E402
from run_manifest import create_run_manifest  # noqa: E402
from simulator import PaperTradingSimulator  # noqa: E402
from time_utils import utc_now_iso  # noqa: E402


class DeterministicFeed:
    def __init__(self):
        self.bid = 0.50
        self.ask = 0.50

    def get_book(self, _asset):
        return {
            "best_bid": self.bid, "best_ask": self.ask,
            "bid_size": 100.0, "ask_size": 100.0,
            "spread": self.ask - self.bid, "observed_at": utc_now_iso(),
            "bid_levels": [{"price": self.bid, "size": 100.0}],
            "ask_levels": [{"price": self.ask, "size": 100.0}],
        }

    def get_books(self, assets):
        return {asset: self.get_book(asset) for asset in assets}

    def get_executable_price(self, _asset, side, size_shares=0):
        del size_shares
        return self.ask if side == "BUY" else self.bid

    def get_market(self, _condition_id):
        return {
            "category": "macro", "fees_enabled": True,
            "fee_schedule": {"rate": 0.01, "exponent": 1.0, "taker_only": True},
            "fee_metadata_known": True, "closed": False,
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


def run_smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="polymarket-paper-smoke-") as raw:
        data_dir = Path(raw) / "data"
        logs_dir = Path(raw) / "logs"
        data_dir.mkdir()
        logs_dir.mkdir()
        simulator_module.DATA_DIR = data_dir
        simulator_module.LOGS_DIR = logs_dir
        run_manifest_module.DATA_DIR = data_dir

        wallets = [
            {"address": f"wallet-{index}", "allowed_domains": ["macro"]}
            for index in range(5)
        ]
        manifest = create_run_manifest(
            "paper_validation",
            {"wallets": wallets, "intended_domains": ["macro"]},
            data_dir=data_dir,
            root=ROOT,
        )
        feed = DeterministicFeed()
        candidate = {
            "asset": "smoke-asset", "condition_id": "smoke-condition",
            "title": "Will the smoke lifecycle reconcile?",
            "slug": "smoke-market", "event_id": "smoke-event",
            "event_slug": "smoke-event", "event_title": "Smoke Event",
            "outcome": "Yes", "avg_price": 0.50, "cur_price": 0.50,
            "notional_usdc": 100.0, "category": "macro",
            "redeemable": False, "end_date_iso": "",
            "transaction_hash": "smoke-transaction",
            "source_trade_status": "ok", "source_trade_at": utc_now_iso(),
            "source_trade_price": 0.50, "source_trade_size": 25.0,
        }

        simulator_module.EXECUTION["mode"] = "observe"
        sim = PaperTradingSimulator(initial_capital=300.0)
        if sim.execution_mode != "paper_validation":
            raise AssertionError("il manifest non prevale sulla configurazione")
        if not sim.open_position("wallet-0", candidate, fetcher=feed):
            raise AssertionError("apertura deterministica rifiutata")
        if abs(sim.portfolio.cash - 295.0) > 1e-9:
            raise AssertionError("cash dopo apertura non riconciliato")
        opened = next(iter(sim.portfolio.positions.values()))
        if opened.entry_price <= feed.ask or opened.fee_rate != 0.01:
            raise AssertionError("ingresso non include ask VWAP e fee")
        sim._save_state()

        restarted = PaperTradingSimulator(initial_capital=300.0)
        if restarted.run_id != manifest["run_id"]:
            raise AssertionError("run_id perso al restart")
        if restarted.execution_mode != "paper_validation":
            raise AssertionError("modalita paper persa al restart")
        if len(restarted.portfolio.positions) != 1:
            raise AssertionError("posizione persa al restart")

        feed.bid = 0.55
        feed.ask = 0.56
        restarted.baseline_done = True
        restarted.reconcile(
            {}, 1, feed, new_holdings=set(),
            monitored_wallets={"wallet-0"}, failed_wallets=set(),
        )
        if restarted.portfolio.positions:
            raise AssertionError("vendita sorgente non ha chiuso la posizione")
        if len(restarted.portfolio.closed_positions) != 1:
            raise AssertionError("chiusura non persistita")
        closed = restarted.portfolio.closed_positions[0]
        if closed.close_reason != "exit" or closed.exit_price >= feed.bid:
            raise AssertionError("uscita non usa il bid netto della fee")
        if abs(restarted.portfolio.cash - (300.0 + closed.pnl)) > 1e-8:
            raise AssertionError("cash e P&L non riconciliati")

        rows = [
            json.loads(line)
            for line in (data_dir / "candidate_journal.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        if [row.get("decision") for row in rows[-2:]] != ["opened", "closed"]:
            raise AssertionError("journal apertura/chiusura incompleto")
        if "smoke-transaction" not in restarted.seen_candidate_signal_ids:
            raise AssertionError("dedup signal_id perso al restart")
        return {
            "passed": True, "run_id": manifest["run_id"],
            "entry_price": opened.entry_price, "exit_price": closed.exit_price,
            "pnl": closed.pnl, "cash": restarted.portfolio.cash,
            "journal_decisions": [row.get("decision") for row in rows],
        }


if __name__ == "__main__":
    try:
        print(json.dumps(run_smoke()))
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}))
        raise SystemExit(1)
