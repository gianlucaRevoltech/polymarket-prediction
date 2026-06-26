"""
Tracker per monitorare attività wallet in tempo reale
"""
import requests
import time
from datetime import datetime
from typing import List, Dict, Set
from collections import defaultdict
from models import Trade, TradeSide, Outcome
from config import POLYMARKET_API, TRACKING


class WalletTracker:
    """Monitora attività di wallet target"""
    
    def __init__(self):
        self.data_api = POLYMARKET_API["data"]
        self.seen_tx_hashes: Set[str] = set()
        self.wallet_last_check: Dict[str, int] = defaultdict(int)
    
    def get_new_trades(self, wallet_address: str, known_tx_hashes: Set[str] = None) -> List[Trade]:
        """
        Ottieni nuovi trade da un wallet
        
        Args:
            wallet_address: Indirizzo wallet da monitorare
            known_tx_hashes: Set di tx hash già visti
        
        Returns:
            Lista di nuovi Trade
        """
        if known_tx_hashes is None:
            known_tx_hashes = self.seen_tx_hashes
        
        try:
            url = f"{self.data_api}/activity"
            params = {
                "user": wallet_address,
                "limit": TRACKING["activity_limit"]
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            activities = response.json()
            
            new_trades = []
            
            # Timestamp corrente per filtrare trade vecchi
            current_time = int(time.time())
            max_age_seconds = 86400  # 24 ore (era 3600 = 1 ora) - aumentato per vedere più attività
            
            for activity in activities:
                # Filtra solo trade reali
                if activity.get("type") != "TRADE":
                    continue
                
                # BUG FIX: Ignora trade vecchi (storici)
                trade_time = activity.get("timestamp", 0)
                if trade_time < (current_time - max_age_seconds):
                    continue
                
                tx_hash = activity.get("transactionHash", "")
                
                # Skip se già visto
                if tx_hash in known_tx_hashes:
                    continue
                
                # Converte in oggetto Trade
                trade = Trade(
                    condition_id=activity.get("conditionId", ""),
                    market_title=activity.get("title", "Unknown Market"),
                    market_slug=activity.get("slug", ""),
                    side=TradeSide.BUY if activity.get("side") == "BUY" else TradeSide.SELL,
                    outcome=Outcome.YES if activity.get("outcome") == "Yes" else Outcome.NO,
                    size_usdc=activity.get("usdcSize", 0.0),
                    price=activity.get("price", 0.0),
                    timestamp=activity.get("timestamp", 0),
                    tx_hash=tx_hash,
                    event_slug=activity.get("eventSlug", ""),
                    icon=activity.get("icon", "")
                )
                
                new_trades.append(trade)
                known_tx_hashes.add(tx_hash)
            
            # Aggiorna ultimo check
            self.wallet_last_check[wallet_address] = int(datetime.now().timestamp())
            
            return new_trades
            
        except Exception as e:
            print(f"[ERRORE] Tracker {wallet_address[:10]}...: {e}")
            return []
    
    def poll_wallets(self, wallet_addresses: List[str], callback) -> None:
        """
        Polling continuo su lista wallet
        
        Args:
            wallet_addresses: Lista wallet da monitorare
            callback: Funzione da chiamare per ogni nuovo trade
                     callback(wallet_address, trade)
        """
        print(f"\n[TRACKER] Avvio monitoraggio {len(wallet_addresses)} wallet...")
        print(f"[TRACKER] Polling ogni {TRACKING['poll_interval']} secondi")
        print(f"[TRACKER] Premi Ctrl+C per stoppare\n")
        
        try:
            while True:
                for wallet_addr in wallet_addresses:
                    new_trades = self.get_new_trades(wallet_addr)
                    
                    if new_trades:
                        print(f"\n[TRACKER] {len(new_trades)} nuovi trade da {wallet_addr[:10]}...")
                        
                        for trade in new_trades:
                            print(f"  → {trade.side.value} {trade.outcome.value} | ${trade.size_usdc:.2f} | {trade.market_title[:40]}")
                            callback(wallet_addr, trade)
                    
                    # Rate limiting
                    time.sleep(1)
                
                # Attendi prima di ripollare
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Ciclo completato, attendo {TRACKING['poll_interval']}s...")
                time.sleep(TRACKING["poll_interval"])
                
        except KeyboardInterrupt:
            print("\n[TRACKER] Monitoraggio stoppato dall'utente")
    
    def get_wallet_stats(self, wallet_address: str) -> Dict:
        """
        Ottieni statistiche rapide di un wallet
        
        Args:
            wallet_address: Indirizzo wallet
        
        Returns:
            Dict con statistiche
        """
        try:
            url = f"{self.data_api}/activity"
            params = {
                "user": wallet_address,
                "limit": 100
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            activities = response.json()
            
            trades = [a for a in activities if a.get("type") == "TRADE"]
            
            # Calcola statistiche
            total_volume = sum(t.get("usdcSize", 0) for t in trades)
            buy_volume = sum(t.get("usdcSize", 0) for t in trades if t.get("side") == "BUY")
            sell_volume = sum(t.get("usdcSize", 0) for t in trades if t.get("side") == "SELL")
            
            # Mercati unici
            unique_markets = len(set(t.get("conditionId", "") for t in trades))
            
            # Ultimo trade
            last_trade_time = max((t.get("timestamp", 0) for t in trades), default=0)
            hours_ago = (datetime.now().timestamp() - last_trade_time) / 3600 if last_trade_time else None
            
            return {
                "total_trades": len(trades),
                "total_volume": total_volume,
                "buy_volume": buy_volume,
                "sell_volume": sell_volume,
                "unique_markets": unique_markets,
                "last_trade_hours_ago": hours_ago,
                "is_active": hours_ago is not None and hours_ago < 24
            }
            
        except Exception as e:
            print(f"[ERRORE] Stats {wallet_address[:10]}...: {e}")
            return {}
    
    def print_wallet_status(self, wallet_address: str, wallet_name: str):
        """Stampa stato corrente di un wallet"""
        stats = self.get_wallet_stats(wallet_address)
        
        if not stats:
            print(f"[{wallet_name}] Stato non disponibile")
            return
        
        print(f"\n{'='*60}")
        print(f"WALLET: {wallet_name}")
        print(f"{'='*60}")
        print(f"Indirizzo: {wallet_address}")
        print(f"\nATTIVITÀ:")
        print(f"  Trade Totali: {stats['total_trades']}")
        print(f"  Volume Totale: ${stats['total_volume']:,.2f}")
        print(f"  Volume BUY: ${stats['buy_volume']:,.2f}")
        print(f"  Volume SELL: ${stats['sell_volume']:,.2f}")
        print(f"  Mercati Unici: {stats['unique_markets']}")
        print(f"\nSTATO:")
        if stats['last_trade_hours_ago'] is not None:
            print(f"  Ultimo Trade: {stats['last_trade_hours_ago']:.1f} ore fa")
            print(f"  Attivo: {'✓ SÌ' if stats['is_active'] else '✗ NO'}")
        else:
            print(f"  Ultimo Trade: Mai")
            print(f"  Attivo: ✗ NO")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    # Test tracker
    tracker = WalletTracker()
    
    test_wallet = "0x96cfcb0c30942cfcd1cdf76c7d408794d66b1acb"
    tracker.print_wallet_status(test_wallet, "mintblade")
