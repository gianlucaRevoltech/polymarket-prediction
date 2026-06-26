"""Test analyzer con wallet reali"""
import json
import sys
sys.stdout.reconfigure(encoding='utf-8')

from analyzer import WalletAnalyzer
from scanner import PolymarketScanner
from models import Wallet

# Carica risultati scanner
with open('../data/scan_results.json') as f:
    data = json.load(f)

scanner = PolymarketScanner()
analyzer = WalletAnalyzer()

print("=" * 70)
print("TEST ANALYZER - Top 5 Wallet")
print("=" * 70)

for w_data in data['wallets'][:5]:
    wallet = Wallet(
        address=w_data['address'],
        name=w_data['name'],
        profit=w_data['profit'],
        volume=w_data['volume'],
        rank=w_data['rank']
    )
    
    # Fetch activity for this wallet
    activity = scanner.get_wallet_activity(wallet.address, limit=50)
    analysis = analyzer.analyze_wallet(wallet, activity)
    
    print(f"\n{wallet.name}:")
    print(f"  Win Rate: {analysis.win_rate:.2%}")
    print(f"  Total Trades: {analysis.total_trades}")
    print(f"  Markets Traded: {analysis.markets_traded}")
    print(f"  Winning Markets: {analysis.winning_trades}/{analysis.markets_traded}")
    print(f"  Losing Markets: {analysis.losing_trades}/{analysis.markets_traded}")
    print(f"  ROI: {analysis.roi:.2%}")
    print(f"  Avg Trade Size: ${analysis.avg_trade_size:.2f}")
    print(f"  Qualified: {'YES' if analysis.is_qualified else 'NO'} (Score: {analysis.qualification_score:.1f}/100)")
    if not analysis.is_qualified and analysis.disqualified_reasons:
        print(f"  Reasons: {', '.join(analysis.disqualified_reasons)}")
