"""Smoke manuale dell'analyzer; non effettua rete durante unittest discovery."""
import json
import sys
from pathlib import Path

from analyzer import WalletAnalyzer
from scanner import PolymarketScanner
from models import Wallet


def main() -> int:
    if sys.platform.startswith("win"):
        sys.stdout.reconfigure(encoding="utf-8")
    results = Path(__file__).resolve().parents[1] / "data" / "scan_results.json"
    if not results.exists():
        print(f"[SKIP] {results} assente")
        return 0
    data = json.loads(results.read_text(encoding="utf-8"))
    scanner = PolymarketScanner()
    analyzer = WalletAnalyzer()
    print("=" * 70)
    print("SMOKE ANALYZER - Top 5 Wallet")
    print("=" * 70)
    for w_data in data.get("wallets", [])[:5]:
        wallet = Wallet(
            address=w_data["address"],
            name=w_data.get("name", "Unknown"),
            profit=w_data.get("profit", 0),
            volume=w_data.get("volume", 0),
            rank=w_data.get("rank", 0),
        )
        activity = scanner.get_wallet_activity(wallet.address, limit=50)
        analysis = analyzer.analyze_wallet(wallet, activity)
        print(
            f"{wallet.name}: WR={analysis.win_rate:.2%}, "
            f"trades={analysis.total_trades}, ROI={analysis.roi:.2%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
