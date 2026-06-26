"""
Scanner per trovare wallet profittevoli su Polymarket.

Due modalita:
  - scan_all (legacy): leaderboard profit + volume. Trova wallet bravi ma che
    tradano mercati diversi -> il consenso quasi non si attiva.
  - scan_consensus (nuova): parte dai MERCATI POPOLARI (gamma, alto volume),
    ne estrae gli holder (data-api /holders) e qualifica i wallet che
    CO-DETENGONO gli stessi mercati (overlap) e sono profittevoli. Cosi il
    consenso ha basi statistiche reali (piu wallet sugli stessi asset).
"""
import requests
import re
import time
import json
import os
from collections import defaultdict
from typing import List, Dict, Optional
from datetime import datetime
from models import Wallet
from portfolio_sync import PolymarketPositionFetcher
from backtester import Backtester
from config import POLYMARKET_API, SCANNER, DATA_DIR


class PolymarketScanner:
    """Scansiona Polymarket per trovare wallet profittevoli"""
    
    def __init__(self):
        self.data_api = POLYMARKET_API["data"]
        self.gamma_api = POLYMARKET_API["gamma"]
        self.rsc_headers = {
            "RSC": "1",
            "Next-Router-State-Tree": "%5B%22%22%2C%7B%7D%5D",
            "Next-Url": "/leaderboard",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        self.session = requests.Session()
        self.session.headers.update(self.rsc_headers)
        self.fetcher = PolymarketPositionFetcher()
        self.bt = Backtester(activity_limit=1000)
    
    def _fetch_leaderboard_page(self) -> str:
        """Scarica la pagina leaderboard con header RSC"""
        url = "https://polymarket.com/leaderboard"
        
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                return response.text
            except requests.RequestException as e:
                print(f"  [RETRY {attempt+1}/3] Errore fetch leaderboard: {e}")
                time.sleep(2 * (attempt + 1))
        
        return ""
    
    def _parse_wallets_from_rsc(self, text: str, data_type: str = "profit") -> List[Dict]:
        """
        Parsa wallet dalla risposta RSC di Polymarket
        
        Args:
            text: Testo RSC response
            data_type: "profit" o "volume"
        
        Returns:
            Lista dict con dati wallet
        """
        pattern = (
            r'"proxyWallet":"(0x[a-fA-F0-9]{40})",'
            r'"name":"([^"]*)",'
            r'"pseudonym":"([^"]*)",'
            r'"amount":([0-9.eE+-]+),'
            r'"pnl":(-?[0-9.eE+-]+),'
            r'"volume":([0-9.eE+-]+)'
        )
        
        matches = re.findall(pattern, text)
        
        wallets = []
        seen = set()
        
        for wallet_addr, name, pseudonym, amount, pnl, volume in matches:
            if wallet_addr in seen:
                continue
            seen.add(wallet_addr)
            
            try:
                wallet_data = {
                    "address": wallet_addr,
                    "name": name if name else pseudonym,
                    "pseudonym": pseudonym,
                    "amount": float(amount),
                    "pnl": float(pnl),
                    "volume": float(volume),
                    "roi": 0.0
                }
                
                if wallet_data["volume"] > 0:
                    wallet_data["roi"] = (wallet_data["pnl"] / wallet_data["volume"]) * 100
                
                wallets.append(wallet_data)
            except (ValueError, TypeError):
                continue
        
        return wallets
    
    def scan_profit_leaderboard(self, top_n: int = 50) -> List[Wallet]:
        """
        Scansione leaderboard per PROFITTO
        
        Cerca wallet con:
        - ROI elevato (> 5%)
        - Profitto positivo
        - Volume ragionevole (non necessariamente whale)
        
        Args:
            top_n: Numero wallet da ritornare
        
        Returns:
            Lista Wallet ordinati per ROI (discendente)
        """
        print(f"\n{'='*60}")
        print(f"SCANSIONE LEADERBOARD PROFIT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        # Fetch pagina
        print("[1/3] Scaricamento leaderboard...")
        text = self._fetch_leaderboard_page()
        
        if not text:
            print("  [ERRORE] Impossibile scaricare leaderboard")
            return []
        
        # Parse wallet
        print("[2/3] Parsing dati wallet...")
        raw_wallets = self._parse_wallets_from_rsc(text, "profit")
        print(f"  Trovati {len(raw_wallets)} wallet unici")
        
        if not raw_wallets:
            print("  [ERRORE] Nessun wallet trovato nella risposta")
            return []
        
        # Filtra e ordina
        print("[3/3] Filtro e classificazione...")
        
        # Criteria di selezione per budget piccolo
        qualified = []
        for w in raw_wallets:
            # Solo wallet profittevoli
            if w["pnl"] <= 0:
                continue
            
            # ROI minimo 10% (alto per budget piccolo)
            if w["roi"] < 10.0:
                continue
            
            # Profitto massimo ragionevole (non whale)
            if w["pnl"] > 100000:  # Max $100K profit
                continue
            
            qualified.append(w)
        
        # Ordina per ROI (discendente)
        qualified.sort(key=lambda x: x["roi"], reverse=True)
        
        # Top N
        top = qualified[:top_n]
        
        # Converti in oggetti Wallet
        result = []
        for rank, w in enumerate(top, 1):
            wallet = Wallet(
                address=w["address"],
                name=w["name"],
                profit=w["pnl"],
                volume=w["volume"],
                rank=rank,
                pseudonym=w["pseudonym"]
            )
            result.append(wallet)
        
        # Print results
        print(f"\n{'='*60}")
        print(f"TOP {len(result)} WALLET PER ROI (Profit Leaderboard)")
        print(f"{'='*60}")
        
        for i, w in enumerate(result[:20], 1):
            print(f"{i:3}. {w.name:30} | PnL: ${w.profit:>10,.0f} | Vol: ${w.volume:>12,.0f} | ROI: {w.roi:>5.1f}%")
        
        if len(result) > 20:
            print(f"     ... e altri {len(result) - 20} wallet")
        
        print(f"{'='*60}")
        
        return result
    
    def scan_volume_leaderboard(self, top_n: int = 50) -> List[Wallet]:
        """
        Scansione leaderboard per VOLUME (per diversificazione)
        """
        print(f"\n[SCAN] Leaderboard Volume...")
        
        # Usa stesso page ma con parametri diversi
        url = "https://polymarket.com/leaderboard"
        self.session.headers["Next-Url"] = "/leaderboard"
        
        text = self._fetch_leaderboard_page()
        if not text:
            return []
        
        raw_wallets = self._parse_wallets_from_rsc(text, "volume")
        
        # Per volume, prendi wallet profittevoli con alto volume
        qualified = [w for w in raw_wallets if w["pnl"] > 0 and w["roi"] > 0.5]
        qualified.sort(key=lambda x: x["volume"], reverse=True)
        
        result = []
        for rank, w in enumerate(qualified[:top_n], 1):
            wallet = Wallet(
                address=w["address"],
                name=w["name"],
                profit=w["pnl"],
                volume=w["volume"],
                rank=rank,
                pseudonym=w["pseudonym"]
            )
            result.append(wallet)
        
        print(f"  Trovati {len(result)} wallet profittevoli (volume)")
        return result
    
    def get_wallet_activity(self, address: str, limit: int = 100) -> List[Dict]:
        """
        Ottieni activity recente di un wallet
        
        Args:
            address: Indirizzo wallet
            limit: Numero di activity da recuperare
        
        Returns:
            Lista di activity
        """
        for attempt in range(3):
            try:
                url = f"{self.data_api}/activity"
                params = {
                    "user": address,
                    "limit": limit
                }
                
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                return response.json()
                
            except requests.RequestException as e:
                if attempt < 2:
                    print(f"  [RETRY {attempt+1}/3] Activity fetch error: {e}")
                    time.sleep(2)
                else:
                    print(f"  [ERRORE] Activity fetch fallito per {address[:10]}...")
        
        return []
    
    def count_trades(self, activity: List[Dict]) -> int:
        """Conta trade reali nell'activity"""
        return sum(1 for a in activity if a.get("type") == "TRADE")
    
    # ==================================================================
    #  NUOVO: scan orientato al CONSENSO (wallet che co-detengono mercati)
    # ==================================================================
    def get_popular_markets(self, n_markets: int = 60) -> List[Dict]:
        """
        Mercati piu attivi/popolari (gamma, ordinati per volume).

        Returns:
            Lista di {condition_id, question, volume}
        """
        try:
            url = f"{self.gamma_api}/markets"
            params = {
                "closed": "false", "active": "true",
                "order": "volumeNum", "ascending": "false",
                "limit": n_markets,
            }
            r = requests.get(url, params=params, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            out = []
            for m in r.json():
                cond = m.get("conditionId", "")
                if not cond:
                    continue
                out.append({
                    "condition_id": cond,
                    "question": m.get("question", ""),
                    "volume": float(m.get("volumeNum", 0) or 0),
                })
            return out
        except requests.RequestException as e:
            print(f"  [ERRORE] gamma markets: {e}")
            return []

    def get_market_holders(self, condition_id: str, limit: int = 25) -> List[Dict]:
        """
        Top holder di un mercato (entrambi gli outcome) via data-api /holders.

        Returns:
            Lista di {address, name, pseudonym, amount, outcome_index}
        """
        try:
            url = f"{self.data_api}/holders"
            r = self.session.get(url, params={"market": condition_id, "limit": limit}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException:
            return []

        holders = []
        for token in data:
            for h in token.get("holders", []):
                addr = h.get("proxyWallet", "")
                if not addr:
                    continue
                holders.append({
                    "address": addr,
                    "name": h.get("name", ""),
                    "pseudonym": h.get("pseudonym", ""),
                    "amount": float(h.get("amount", 0) or 0),
                    "outcome_index": h.get("outcomeIndex", -1),
                })
        return holders

    @staticmethod
    def _looks_like_retail(name: str, pseudonym: str) -> bool:
        """Scarta wallet probabilmente LP/contratti (senza name ne pseudonym)."""
        return bool(name.strip() or pseudonym.strip())

    def _wallet_realized_performance(self, address: str) -> Dict:
        """
        ROI REALIZZATO storico (non snapshot): ricostruisce le posizioni chiuse
        dal feed /activity e le valuta fino a risoluzione (motore Backtester).

        Returns:
            {roi, pnl, bought, decided, win_rate}
        """
        activity = self.bt.fetch_activity(address)
        posmap = self.bt.positions_map(address)
        closed = self.bt.reconstruct_positions(activity, posmap)

        pnl = sum(p["realized_pnl"] for p in closed.values())
        bought = sum(p["bought"] for p in closed.values())
        n = len(closed)
        wins = sum(1 for p in closed.values() if p["realized_pnl"] > 0)
        roi = (pnl / bought) if bought > 0 else 0.0
        return {"roi": roi, "pnl": pnl, "bought": bought,
                "decided": n, "win_rate": (wins / n if n else 0.0)}

    def scan_consensus(self, top_n: int = 20,
                       n_markets: int = 60, holders_per_market: int = 25,
                       min_overlap: int = 3, candidate_cap: int = 80,
                       min_realized_roi: float = 0.10, min_decided: int = 3) -> List[Wallet]:
        """
        Qualifica wallet che co-detengono gli stessi mercati popolari e sono
        profittevoli, per dare basi reali alla strategia di consenso.
        """
        print(f"\n{'*'*64}")
        print(f"  SCANNER CONSENSO - wallet sugli stessi mercati popolari")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'*'*64}\n")

        print(f"[1/4] Mercati popolari (top {n_markets} per volume)...")
        markets = self.get_popular_markets(n_markets=n_markets)
        print(f"  Trovati {len(markets)} mercati attivi")
        if not markets:
            return []

        print(f"[2/4] Estrazione holder per mercato...")
        wallet_markets: Dict[str, set] = defaultdict(set)
        wallet_meta: Dict[str, Dict] = {}
        for i, m in enumerate(markets, 1):
            for h in self.get_market_holders(m["condition_id"], limit=holders_per_market):
                if not self._looks_like_retail(h["name"], h["pseudonym"]):
                    continue
                addr = h["address"]
                wallet_markets[addr].add(m["condition_id"])
                if addr not in wallet_meta:
                    wallet_meta[addr] = {"name": h["name"] or h["pseudonym"], "pseudonym": h["pseudonym"]}
            if i % 10 == 0:
                print(f"  Processati {i}/{len(markets)} mercati ({len(wallet_markets)} wallet unici)...")
            time.sleep(0.25)

        # Candidati con overlap sufficiente
        candidates = [(a, len(ms)) for a, ms in wallet_markets.items() if len(ms) >= min_overlap]
        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:candidate_cap]
        print(f"  {len(candidates)} candidati con overlap >= {min_overlap} mercati")

        print(f"[3/4] ROI realizzato storico dei candidati (soglia >= {min_realized_roi:.0%}, "
              f"min {min_decided} posizioni)...")
        qualified: List[Dict] = []
        for i, (addr, overlap) in enumerate(candidates, 1):
            perf = self._wallet_realized_performance(addr)
            if perf["decided"] >= min_decided and perf["roi"] >= min_realized_roi:
                qualified.append({
                    "address": addr,
                    "name": wallet_meta[addr]["name"],
                    "pseudonym": wallet_meta[addr]["pseudonym"],
                    "overlap": overlap,
                    "pnl": perf["pnl"],
                    "invested": perf["bought"],
                    "roi": perf["roi"],
                    "decided": perf["decided"],
                    "win_rate": perf["win_rate"],
                })
            if i % 10 == 0:
                print(f"  Verificati {i}/{len(candidates)} ({len(qualified)} qualificati)...")
            time.sleep(0.3)

        # Ranking: per qualita reale (ROI storico), poi overlap come tie-break
        qualified.sort(key=lambda x: (x["roi"], x["overlap"]), reverse=True)
        top = qualified[:top_n]

        print(f"\n{'='*70}")
        print(f"  TOP {len(top)} WALLET (ROI realizzato storico + overlap mercati)")
        print(f"{'='*70}")
        print(f"  {'wallet':22} | {'mkt':>3} | {'ROI st.':>7} | {'WR':>4} | {'pos':>3} | {'PnL':>12}")
        print(f"  {'-'*22}-+-{'-'*3}-+-{'-'*7}-+-{'-'*4}-+-{'-'*3}-+-{'-'*12}")
        for w in top:
            print(f"  {w['name'][:22]:22} | {w['overlap']:3} | {w['roi']:6.1%} | "
                  f"{w['win_rate']:4.0%} | {w['decided']:3} | ${w['pnl']:>11,.0f}")
        print(f"{'='*70}")
        if not top:
            print("  Nessun wallet supera la soglia. Prova ad abbassare --min-roi o --min-overlap.")

        # Converti in Wallet e salva
        result = []
        for rank, w in enumerate(top, 1):
            wallet = Wallet(
                address=w["address"],
                name=w["name"],
                profit=w["pnl"],
                volume=w["invested"],
                rank=rank,
                pseudonym=w["pseudonym"],
            )
            wallet.roi = w["roi"] * 100      # ROI realizzato storico (%)
            wallet.num_trades = w["decided"]  # posizioni decise reali
            wallet.win_rate = w["win_rate"]
            result.append(wallet)

        self._save_scan_results(result, extra={a["address"]: a for a in top})
        return result

    def scan_all(self, top_n: int = 30) -> List[Wallet]:
        """
        Scansione completa: combina profit + volume leaderboard
        
        Args:
            top_n: Numero totale wallet da trovare
        
        Returns:
            Lista Wallet combinata e deduplicata, ordinati per ROI
        """
        print(f"\n{'*'*60}")
        print(f"  POLYMARKET WALLET SCANNER - FULL SCAN")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'*'*60}\n")
        
        # 1. Scan profit leaderboard
        profit_wallets = self.scan_profit_leaderboard(top_n=top_n)
        
        # 2. Scan volume leaderboard (per diversificazione)
        volume_wallets = self.scan_volume_leaderboard(top_n=20)
        
        # 3. Combina e deduplica
        seen = set()
        combined = []
        
        # Prima aggiungi profit wallets (piu interessanti)
        for w in profit_wallets:
            if w.address not in seen:
                seen.add(w.address)
                combined.append(w)
        
        # Poi aggiungi volume wallets se non gia presenti
        for w in volume_wallets:
            if w.address not in seen:
                seen.add(w.address)
                combined.append(w)
        
        # 4. Per ogni wallet, conta trade reali
        print(f"\n[ANALYSIS] Verifica activity per {len(combined)} wallet...")
        
        active_wallets = []
        for i, wallet in enumerate(combined, 1):
            activity = self.get_wallet_activity(wallet.address, limit=50)
            wallet.num_trades = self.count_trades(activity)
            
            if wallet.num_trades >= SCANNER["min_trades"]:
                active_wallets.append(wallet)
            
            if i % 5 == 0:
                print(f"  Analizzati {i}/{len(combined)}...")
            
            time.sleep(0.5)  # Rate limiting
        
        # 5. Ordina per ROI
        active_wallets.sort(key=lambda w: w.roi, reverse=True)
        
        # 6. Re-number rank
        for i, w in enumerate(active_wallets, 1):
            w.rank = i
        
        print(f"\n{'*'*60}")
        print(f"  RISULTATO FINALE: {len(active_wallets)} wallet attivi e profittevoli")
        print(f"{'*'*60}")
        
        # Salva risultati su file
        self._save_scan_results(active_wallets)
        
        return active_wallets[:top_n]
    
    def _save_scan_results(self, wallets: List[Wallet], extra: Optional[Dict] = None):
        """Salva risultati scansione su file"""
        results_file = DATA_DIR / "scan_results.json"
        extra = extra or {}

        wallets_out = []
        for w in wallets:
            e = extra.get(w.address, {})
            wallets_out.append({
                "address": w.address,
                "name": w.name,
                "profit": w.profit,
                "volume": w.volume,
                "roi": w.roi,                 # ROI realizzato storico (%)
                "num_trades": w.num_trades,   # posizioni decise reali
                "rank": w.rank,
                # Metriche specifiche del consenso (se disponibili)
                "markets_overlap": e.get("overlap", w.num_trades),
                "win_rate": e.get("win_rate", w.win_rate),
                "decided_positions": e.get("decided", w.num_trades),
            })

        data = {
            "scan_time": datetime.now().isoformat(),
            "total_wallets": len(wallets),
            "wallets": wallets_out,
        }

        with open(results_file, "w") as f:
            json.dump(data, f, indent=2)

        print(f"\n  [SAVE] Risultati salvati in {results_file}")


if __name__ == "__main__":
    import sys
    if sys.platform.startswith('win'):
        sys.stdout.reconfigure(encoding='utf-8')

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["consensus", "legacy"], default="consensus",
                        help="consensus: wallet sugli stessi mercati; legacy: leaderboard")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--markets", type=int, default=60)
    parser.add_argument("--min-overlap", type=int, default=3)
    parser.add_argument("--min-roi", type=float, default=0.10, help="ROI realizzato minimo (es. 0.10 = 10%)")
    parser.add_argument("--min-decided", type=int, default=3, help="minimo posizioni decise")
    args = parser.parse_args()

    scanner = PolymarketScanner()
    if args.mode == "consensus":
        scanner.scan_consensus(top_n=args.top, n_markets=args.markets,
                               min_overlap=args.min_overlap,
                               min_realized_roi=args.min_roi,
                               min_decided=args.min_decided)
    else:
        scanner.scan_all(top_n=args.top)
