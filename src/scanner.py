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
from categories import categorize_market
from config import POLYMARKET_API, SCANNER, CATEGORIES, DATA_DIR


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
        Mercati piu attivi/popolari (gamma, ordinati per volume), categorizzati.

        Returns:
            Lista di {condition_id, question, slug, volume, category}
        """
        try:
            url = f"{self.gamma_api}/markets"
            params = {
                "closed": "false", "active": "true",
                "order": "volumeNum", "ascending": "false",
                "limit": n_markets,
            }
            r = requests.get(url, params=params, timeout=25,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            out = []
            for m in r.json():
                cond = m.get("conditionId", "")
                if not cond:
                    continue
                events = m.get("events") or []
                event_ticker = events[0].get("ticker", "") if events else ""
                event_slug = events[0].get("slug", "") if events else ""
                category = categorize_market(
                    question=m.get("question", ""),
                    event_ticker=event_ticker,
                    event_slug=event_slug,
                    fee_type=m.get("feeType", ""),
                )
                out.append({
                    "condition_id": cond,
                    "question": m.get("question", ""),
                    "slug": m.get("slug", ""),
                    "volume": float(m.get("volumeNum", 0) or 0),
                    "category": category,
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

    # ------------------------------------------------------------------
    #  Helper riusabili: overlap holder -> candidati -> qualificazione ROI
    # ------------------------------------------------------------------
    def _collect_overlap(self, markets: List[Dict], holders_per_market: int):
        """Mappa wallet -> set(mercati co-detenuti) + metadati, sui mercati dati."""
        wallet_markets: Dict[str, set] = defaultdict(set)
        wallet_meta: Dict[str, Dict] = {}
        for i, m in enumerate(markets, 1):
            for h in self.get_market_holders(m["condition_id"], limit=holders_per_market):
                if not self._looks_like_retail(h["name"], h["pseudonym"]):
                    continue
                addr = h["address"]
                wallet_markets[addr].add(m["condition_id"])
                if addr not in wallet_meta:
                    wallet_meta[addr] = {"name": h["name"] or h["pseudonym"],
                                         "pseudonym": h["pseudonym"]}
            if i % 10 == 0:
                print(f"    {i}/{len(markets)} mercati ({len(wallet_markets)} wallet)...")
            time.sleep(0.2)
        return wallet_markets, wallet_meta

    def _qualify_wallets(self, wallet_markets: Dict[str, set], wallet_meta: Dict[str, Dict],
                         min_overlap: int, min_realized_roi: float, min_decided: int,
                         min_win_rate: float = 0.55,
                         candidate_cap: int = 60) -> List[Dict]:
        """Filtra per overlap, poi qualifica per ROI realizzato storico e win rate."""
        candidates = [(a, len(ms)) for a, ms in wallet_markets.items() if len(ms) >= min_overlap]
        candidates.sort(key=lambda x: x[1], reverse=True)
        candidates = candidates[:candidate_cap]
        print(f"    {len(candidates)} candidati con overlap >= {min_overlap}")

        qualified: List[Dict] = []
        for i, (addr, overlap) in enumerate(candidates, 1):
            perf = self._wallet_realized_performance(addr)
            if (perf["decided"] >= min_decided
                    and perf["roi"] >= min_realized_roi
                    and perf["win_rate"] >= min_win_rate):
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
                print(f"    verificati {i}/{len(candidates)} ({len(qualified)} ok)...")
            time.sleep(0.3)
        qualified.sort(key=lambda x: (x["roi"], x["win_rate"], x["overlap"]), reverse=True)
        return qualified

    def scan_categories(self, top_n: int = 20) -> List[Wallet]:
        """
        Seleziona wallet SPECIALISTI per categoria di mercato.

        Per ogni categoria attiva (sport/crypto/politics/weather) trova gli holder
        che co-detengono i mercati popolari di QUELLA categoria e li qualifica per
        ROI realizzato storico. Il risultato e' un mix bilanciato tra categorie,
        cosi seguiamo i wallet giusti per ciascun tipo di mercato.
        """
        cfg = CATEGORIES
        print(f"\n{'*'*64}")
        print(f"  SCANNER PER CATEGORIA - specialisti per tipo di mercato")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'*'*64}\n")

        print(f"[1/3] Mercati popolari (top {cfg['markets_to_scan']}) e categorizzazione...")
        markets = self.get_popular_markets(n_markets=cfg["markets_to_scan"])
        by_cat: Dict[str, List[Dict]] = defaultdict(list)
        for m in markets:
            by_cat[m["category"]].append(m)
        for cat in cfg["active"]:
            print(f"  {cat:9}: {len(by_cat.get(cat, []))} mercati")
        # Phase CJ: diagnostica — stampa esempi "other" per identificare keyword mancanti
        other_markets = by_cat.get("other", [])
        if other_markets:
            print(f"  {'other':9}: {len(other_markets)} mercati (primi 15 esempi per debug):")
            for m in other_markets[:15]:
                print(f"    vol={m.get('volume',0):>12.0f}  {m.get('question','')[:75]}")

        per_cat = cfg["specialists_per_category"]
        results_by_cat: Dict[str, List[Dict]] = {}

        for cat in cfg["active"]:
            cat_markets = by_cat.get(cat, [])
            if not cat_markets:
                print(f"\n[{cat}] nessun mercato in questa categoria, salto.")
                continue
            print(f"\n[2/3] Categoria '{cat}' - holder e qualificazione...")
            wallet_markets, wallet_meta = self._collect_overlap(
                cat_markets, cfg["holders_per_market"])
            qualified = self._qualify_wallets(
                wallet_markets, wallet_meta,
                min_overlap=cfg["min_overlap"],
                min_realized_roi=cfg["min_realized_roi"],
                min_decided=cfg["min_decided"],
                min_win_rate=cfg.get("min_win_rate", 0.55))
            results_by_cat[cat] = qualified[:per_cat]
            print(f"  -> {len(results_by_cat[cat])} specialisti '{cat}'")

        # Interleaving bilanciato tra categorie (round-robin) fino a top_n,
        # deduplicando per indirizzo (un wallet puo' essere specialista in piu
        # categorie: lo teniamo una sola volta, nella prima in cui emerge).
        print(f"\n[3/3] Composizione mix bilanciato (max {top_n})...")
        interleaved: List[Dict] = []
        seen_addr = set()
        idx = 0
        max_len = max((len(v) for v in results_by_cat.values()), default=0)
        while len(interleaved) < top_n and idx < max_len:
            for cat in cfg["active"]:
                lst = results_by_cat.get(cat, [])
                if idx < len(lst):
                    w = dict(lst[idx])
                    if w["address"] in seen_addr:
                        continue
                    w["category"] = cat
                    seen_addr.add(w["address"])
                    interleaved.append(w)
                    if len(interleaved) >= top_n:
                        break
            idx += 1

        print(f"\n{'='*70}")
        print(f"  WALLET SELEZIONATI: {len(interleaved)}")
        print(f"{'='*70}")
        print(f"  {'wallet':22} | {'cat':9} | {'mkt':>3} | {'ROI st.':>7} | {'WR':>4} | {'pos':>3}")
        print(f"  {'-'*22}-+-{'-'*9}-+-{'-'*3}-+-{'-'*7}-+-{'-'*4}-+-{'-'*3}")
        for w in interleaved:
            print(f"  {w['name'][:22]:22} | {w['category']:9} | {w['overlap']:3} | "
                  f"{w['roi']:6.1%} | {w['win_rate']:4.0%} | {w['decided']:3}")
        print(f"{'='*70}")

        result = []
        for rank, w in enumerate(interleaved, 1):
            wallet = Wallet(
                address=w["address"], name=w["name"],
                profit=w["pnl"], volume=w["invested"],
                rank=rank, pseudonym=w["pseudonym"],
            )
            wallet.roi = w["roi"] * 100
            wallet.num_trades = w["decided"]
            wallet.win_rate = w["win_rate"]
            result.append(wallet)

        extra = {w["address"]: w for w in interleaved}
        self._save_scan_results(result, extra=extra)
        return result

    def scan_consensus(self, top_n: int = 20,
                       n_markets: int = 60, holders_per_market: int = 25,
                       min_overlap: int = 3, candidate_cap: int = 80,
                       min_realized_roi: float = 0.20, min_decided: int = 10,
                       min_win_rate: float = 0.55) -> List[Wallet]:
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
              f"min {min_decided} posizioni, WR >= {min_win_rate:.0%})...")
        qualified: List[Dict] = []
        for i, (addr, overlap) in enumerate(candidates, 1):
            perf = self._wallet_realized_performance(addr)
            if (perf["decided"] >= min_decided
                    and perf["roi"] >= min_realized_roi
                    and perf["win_rate"] >= min_win_rate):
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

        # Ranking: per qualita reale (ROI storico, win rate), poi overlap come tie-break
        qualified.sort(key=lambda x: (x["roi"], x["win_rate"], x["overlap"]), reverse=True)
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

        # Phase B: enforce qualita (win-rate + posizioni decise) anche sul path
        # legacy. Ricostruisce le metriche realizzate storiche per wallet e
        # scarta quelli che non superano le soglie di CATEGORIES/SCANNER.
        # Senza questo passo scan_all salva wallet con WR<0.55 (bug P5).
        min_wr = SCANNER.get("min_win_rate", CATEGORIES["min_win_rate"])
        min_decided = SCANNER.get("min_decided", CATEGORIES["min_decided"])
        min_roi = CATEGORIES["min_realized_roi"]
        print(f"\n[ANALYSIS] Phase B: enforce qualita (WR>= {min_wr:.0%}, "
              f"decided>= {min_decided}, ROI>= {min_roi:.0%})...")
        qualified_legacy: List[Wallet] = []
        for i, w in enumerate(active_wallets, 1):
            perf = self._wallet_realized_performance(w.address)
            w.num_trades = perf["decided"]
            w.win_rate = perf["win_rate"]
            w.roi = perf["roi"] * 100  # sovrascrive ROI leaderboard con ROI realizzato
            if (perf["decided"] >= min_decided
                    and perf["win_rate"] >= min_wr
                    and perf["roi"] >= min_roi):
                qualified_legacy.append(w)
            if i % 5 == 0:
                print(f"  verificati {i}/{len(active_wallets)} "
                      f"({len(qualified_legacy)} ok)...")
            time.sleep(0.3)
        active_wallets = qualified_legacy

        # 6. Re-number rank
        for i, w in enumerate(active_wallets, 1):
            w.rank = i

        print(f"\n{'*'*60}")
        print(f"  RISULTATO FINALE: {len(active_wallets)} wallet attivi e qualificati")
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
                "category": e.get("category", ""),
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
    parser.add_argument("--mode", choices=["categories", "consensus", "legacy"], default="categories",
                        help="categories: specialisti per categoria; consensus: stessi mercati; legacy: leaderboard")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--markets", type=int, default=60)
    parser.add_argument("--min-overlap", type=int, default=3)
    parser.add_argument("--min-roi", type=float, default=0.10, help="ROI realizzato minimo (es. 0.10 = 10%)")
    parser.add_argument("--min-decided", type=int, default=3, help="minimo posizioni decise")
    args = parser.parse_args()

    scanner = PolymarketScanner()
    if args.mode == "categories":
        scanner.scan_categories(top_n=args.top)
    elif args.mode == "consensus":
        scanner.scan_consensus(top_n=args.top, n_markets=args.markets,
                               min_overlap=args.min_overlap,
                               min_realized_roi=args.min_roi,
                               min_decided=args.min_decided)
    else:
        scanner.scan_all(top_n=args.top)
