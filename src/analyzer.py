"""
Analyzer per valutare qualità wallet Polymarket
"""
from typing import List, Dict
from datetime import datetime, timedelta
from collections import defaultdict
from models import Wallet, Trade, TradeSide, WalletAnalysis
from config import ANALYZER, POLYMARKET_API
import requests


class WalletAnalyzer:
    """Analizza wallet per determinare se sono degni di essere copiati"""
    
    def __init__(self):
        self.data_api = POLYMARKET_API["data"]
    
    def analyze_wallet(self, wallet: Wallet, activity: List[dict]) -> WalletAnalysis:
        """
        Analizza wallet completo
        
        Args:
            wallet: Wallet da analizzare
            activity: Lista attività raw dal API
        
        Returns:
            WalletAnalysis con tutte le metriche
        """
        # Filtra solo trade e redeem (attività rilevanti per PnL)
        trades = [a for a in activity if a.get("type") in ["TRADE", "REDEEM"]]
        
        if len(trades) == 0:
            return self._empty_analysis(wallet)
        
        # Calcola metriche base
        total_trades = len(trades)
        total_pnl = 0.0
        trade_sizes = []
        
        # Raggruppa trade per mercato
        trades_by_market = defaultdict(list)
        for trade in trades:
            market = trade.get("title", "Unknown")
            trades_by_market[market].append(trade)
        
        total_markets = len(trades_by_market)
        winning_trades = 0
        losing_trades = 0
        
        # Analizza ogni mercato
        daily_pnl = defaultdict(float)
        for market, market_trades in trades_by_market.items():
            market_pnl = self._calculate_market_pnl(market_trades)
            total_pnl += market_pnl
            
            if market_pnl > 0:
                winning_trades += 1
            elif market_pnl < 0:
                losing_trades += 1
            
            # Track daily PnL
            for trade in market_trades:
                date = datetime.fromtimestamp(trade.get("timestamp", 0)).date()
                daily_pnl[date] += market_pnl / len(market_trades)
        
        # Calcola win rate basato sui mercati (non sui trade)
        win_rate = winning_trades / total_markets if total_markets > 0 else 0.0
        
        # Usa ROI ufficiale da Polymarket (già calcolato nel Wallet)
        # Questo è più accurato perché basato su dati completi, non solo 50 attività recenti
        roi = wallet.roi / 100.0  # Converti da percentuale a decimale
        
        # Calcola volume totale e average trade size (in USDC, non in numero di share)
        # NOTA: roi proviene dal dato ufficiale del wallet; win_rate/drawdown/consistency
        # sono indicativi perche calcolati solo sulle ultime ~100 attivita.
        total_volume = sum(t.get("usdcSize", 0) for t in trades)
        avg_trade_size = total_volume / total_trades if total_trades > 0 else 0.0
        
        # Calcola consistency (% giorni profittevoli)
        profitable_days = sum(1 for pnl in daily_pnl.values() if pnl > 0)
        total_days = len(daily_pnl) if daily_pnl else 1
        consistency = profitable_days / total_days if total_days > 0 else 0.0
        
        # Calcola max drawdown
        max_drawdown = self._calculate_max_drawdown(daily_pnl)
        
        # Calcola Sharpe ratio (semplificato)
        sharpe = self._calculate_sharpe(daily_pnl)
        
        # Calcola average holding time
        avg_holding_time = self._calculate_avg_holding_time(trades_by_market)
        
        # Crea analysis
        analysis = WalletAnalysis(
            wallet_address=wallet.address,
            wallet_name=wallet.name,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_profit=total_pnl,
            total_volume=total_volume,
            roi=roi,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            profitable_days=profitable_days,
            total_days=total_days,
            consistency=consistency,
            avg_trade_size=avg_trade_size,
            avg_holding_time_hours=avg_holding_time,
            markets_traded=len(trades_by_market)
        )
        
        # Calcola qualification score
        analysis.calculate_score()
        
        # Determina se è qualificato
        analysis.is_qualified = self._check_qualification(analysis)
        
        return analysis
    
    def _calculate_market_pnl(self, trades: List[dict]) -> float:
        """Calcola PnL per un mercato specifico tracciando posizioni"""
        position_shares = 0.0
        position_cost = 0.0
        realized_pnl = 0.0
        
        # Ordina trade per timestamp
        sorted_trades = sorted(trades, key=lambda t: t.get('timestamp', 0))
        
        for trade in sorted_trades:
            trade_type = trade.get("type", "")
            usdc_size = trade.get("usdcSize", 0)
            side = trade.get("side", "")
            price = trade.get("price", 0)
            
            if trade_type == "REDEEM":
                # Redemption: l'intera posizione viene chiusa
                # usdc_size è il valore ricevuto dalla redemption
                if position_shares > 0 and position_cost > 0:
                    realized_pnl += usdc_size - position_cost
                    # Reset posizione
                    position_shares = 0.0
                    position_cost = 0.0
            elif trade_type == "TRADE" and side == "SELL":
                # Vendita parziale o totale
                if position_shares > 0 and position_cost > 0:
                    # Calcola shares vendute
                    shares_sold = usdc_size / price if price > 0 else 0
                    # Calcola costo proporzionale delle shares vendute
                    cost_per_share = position_cost / position_shares
                    cost_of_sold = cost_per_share * shares_sold
                    # PnL = ricavo - costo
                    realized_pnl += usdc_size - cost_of_sold
                    # Aggiorna posizione
                    position_shares -= shares_sold
                    position_cost -= cost_of_sold
            elif trade_type == "TRADE" and side == "BUY":
                # Ingresso: aggiungi alla posizione
                shares = usdc_size / price if price > 0 else 0
                position_shares += shares
                position_cost += usdc_size
        
        return realized_pnl
    
    def _calculate_max_drawdown(self, daily_pnl: Dict) -> float:
        """Calcola maximum drawdown dalla sequenza di PnL giornalieri"""
        if not daily_pnl:
            return 0.0
        
        # Ordina per data
        sorted_dates = sorted(daily_pnl.keys())
        
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        
        for date in sorted_dates:
            cumulative += daily_pnl[date]
            peak = max(peak, cumulative)
            drawdown = (peak - cumulative) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, drawdown)
        
        return max_dd
    
    def _calculate_sharpe(self, daily_pnl: Dict) -> float:
        """Calcola Sharpe ratio semplificato"""
        if not daily_pnl:
            return 0.0
        
        returns = list(daily_pnl.values())
        
        if len(returns) < 2:
            return 0.0
        
        avg_return = sum(returns) / len(returns)
        variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
        std_dev = variance ** 0.5
        
        if std_dev == 0:
            return 0.0
        
        # Sharpe = (Return - Risk Free) / Std Dev
        # Assumiamo risk free = 0 per semplicità
        return avg_return / std_dev
    
    def _calculate_avg_holding_time(self, trades_by_market: Dict) -> float:
        """Calcola tempo medio di holding in ore"""
        # Semplificazione: per ogni mercato, calcola tempo tra primo buy e ultima sell
        holding_times = []
        
        for market, trades in trades_by_market.items():
            timestamps = [t.get("timestamp", 0) for t in trades if t.get("timestamp")]
            if len(timestamps) >= 2:
                min_ts = min(timestamps)
                max_ts = max(timestamps)
                hours = (max_ts - min_ts) / 3600
                holding_times.append(hours)
        
        return sum(holding_times) / len(holding_times) if holding_times else 0.0
    
    def _check_qualification(self, analysis: WalletAnalysis) -> bool:
        """Verifica se wallet soddisfa criteri minimi"""
        reasons = []
        
        if analysis.roi < ANALYZER["min_roi"]:
            reasons.append(f"ROI {analysis.roi:.2%} < {ANALYZER['min_roi']:.2%}")
        
        if analysis.win_rate < ANALYZER["min_win_rate"]:
            reasons.append(f"Win Rate {analysis.win_rate:.2%} < {ANALYZER['min_win_rate']:.2%}")
        
        if analysis.max_drawdown > ANALYZER["max_drawdown"]:
            reasons.append(f"Drawdown {analysis.max_drawdown:.2%} > {ANALYZER['max_drawdown']:.2%}")
        
        # Sharpe ratio rimosso: non affidabile con dati limitati (solo 50 activities)
        # Per paper trading con budget limitato, ROI e win rate sono più importanti
        
        if analysis.consistency < ANALYZER["min_consistency"]:
            reasons.append(f"Consistency {analysis.consistency:.2%} < {ANALYZER['min_consistency']:.2%}")
        
        if analysis.avg_trade_size < ANALYZER["min_avg_trade_size"]:
            reasons.append(f"Avg Trade ${analysis.avg_trade_size:.2f} < ${ANALYZER['min_avg_trade_size']}")
        
        if analysis.avg_trade_size > ANALYZER["max_avg_trade_size"]:
            reasons.append(f"Avg Trade ${analysis.avg_trade_size:.2f} > ${ANALYZER['max_avg_trade_size']} (wallet whale)")
        
        analysis.disqualified_reasons = reasons
        
        return len(reasons) == 0
    
    def _empty_analysis(self, wallet: Wallet) -> WalletAnalysis:
        """Crea analysis vuota per wallet senza attività"""
        return WalletAnalysis(
            wallet_address=wallet.address,
            wallet_name=wallet.name,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            total_profit=0.0,
            total_volume=0.0,
            roi=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            profitable_days=0,
            total_days=0,
            consistency=0.0,
            avg_trade_size=0.0,
            avg_holding_time_hours=0.0,
            markets_traded=0,
            is_qualified=False,
            qualification_score=0.0
        )
    
    def rank_wallets(self, analyses: List[WalletAnalysis]) -> List[WalletAnalysis]:
        """
        Classifica wallet per qualità
        
        Args:
            analyses: Lista di analisi wallet
        
        Returns:
            Lista ordinata per qualification score
        """
        # Filtra solo qualificati
        qualified = [a for a in analyses if a.is_qualified]
        
        # Ordina per score
        qualified.sort(key=lambda a: a.qualification_score, reverse=True)
        
        return qualified
    
    def print_analysis(self, analysis: WalletAnalysis):
        """Stampa analisi dettagliata"""
        print(f"\n{'='*60}")
        print(f"ANALISI WALLET: {analysis.wallet_name}")
        print(f"{'='*60}")
        print(f"Indirizzo: {analysis.wallet_address}")
        print(f"\nPERFORMANCE:")
        print(f"  ROI: {analysis.roi:.2%}")
        print(f"  Profitto: ${analysis.total_profit:,.2f}")
        print(f"  Volume: ${analysis.total_volume:,.2f}")
        print(f"\nTRADING:")
        print(f"  Trade Totali: {analysis.total_trades}")
        print(f"  Win Rate: {analysis.win_rate:.2%} ({analysis.winning_trades}W / {analysis.losing_trades}L)")
        print(f"  Avg Trade: ${analysis.avg_trade_size:.2f}")
        print(f"  Avg Holding: {analysis.avg_holding_time_hours:.1f} ore")
        print(f"  Mercati: {analysis.markets_traded}")
        print(f"\nRISK:")
        print(f"  Max Drawdown: {analysis.max_drawdown:.2%}")
        print(f"  Sharpe Ratio: {analysis.sharpe_ratio:.2f}")
        print(f"  Consistency: {analysis.consistency:.2%} ({analysis.profitable_days}/{analysis.total_days} giorni)")
        print(f"\nQUALIFICATION:")
        print(f"  Score: {analysis.qualification_score:.1f}/100")
        print(f"  Qualificato: {'✓ SÌ' if analysis.is_qualified else '✗ NO'}")
        
        if analysis.disqualified_reasons:
            print(f"\n  Motivi esclusione:")
            for reason in analysis.disqualified_reasons:
                print(f"    - {reason}")
        
        print(f"{'='*60}\n")


if __name__ == "__main__":
    # Test analyzer
    analyzer = WalletAnalyzer()
    
    # Test con wallet example
    test_wallet = Wallet(
        address="0x96cfcb0c30942cfcd1cdf76c7d408794d66b1acb",
        name="mintblade",
        profit=9238344.62,
        volume=17759922.23,
        rank=1
    )
    
    print("Analyzer test - usa scanner per ottenere activity reale")
