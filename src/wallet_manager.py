"""
Phase Z: Wallet Manager — monitoraggio frequente qualità + swap perdenti.

Problema risolto:
  - auto_rescan ogni 3h è un FULL scan pesante (minuti, 300+ API call)
  - soft-disable solo dimezza size dei wallet perdenti, NON li sostituisce
  - l'utente vuole wallet VINCENTI, swap immediato se non lo sono

Soluzione:
  - quality_refresh ogni 15min: re-fetch win_rate/ROI solo dei wallet attivi
    (10-30 call API, non 300 mercati)
  - track per-wallet P&L dai NOSTRI copy trade (se copiamo wallet X e perdiamo
    >=2 volte, quel wallet è perdente PER NOI, non solo storico Polymarket)
  - swap immediato: wallet perdente -> rimpiazza con riserva dalla reserve list
  - reserve list: scanner salva top 50 qualificati, usiamo top_active + reserve

 NON tocca la lista se un wallet ha solo 1 trade nostro in loss (varianza normale).
"""
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from config import DATA_DIR, WALLET_MONITOR, SCANNER, STRATEGY


class WalletManager:
    """Gestisce monitoraggio frequente qualità wallet + swap perdenti."""

    def __init__(self, scanner=None):
        self.scanner = scanner
        self.scan_file = DATA_DIR / "scan_results.json"
        self.quality_file = DATA_DIR / "wallet_quality.json"
        self.last_refresh: Optional[datetime] = None
        self._our_wallet_pnl: Dict[str, Dict] = {}  # addr -> {wins, losses, pnl}
        self._load_our_pnl()

    # ------------------------------------------------------------------
    # Tracking P&L dei NOSTRI copy trade per wallet sorgente
    # ------------------------------------------------------------------
    def _load_our_pnl(self):
        try:
            if self.quality_file.exists():
                with open(self.quality_file, "r") as f:
                    self._our_wallet_pnl = json.load(f)
        except Exception:
            self._our_wallet_pnl = {}

    def _save_our_pnl(self):
        try:
            with open(self.quality_file, "w") as f:
                json.dump(self._our_wallet_pnl, f, indent=2)
        except Exception:
            pass

    def record_copy_close(self, source_wallet: str, pnl: float):
        """Registra esito di un nostro copy trade chiuso, per wallet sorgente."""
        if not source_wallet:
            return
        addr = source_wallet.lower()
        d = self._our_wallet_pnl.setdefault(addr, {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0})
        d["trades"] = d.get("trades", 0) + 1
        d["pnl"] = d.get("pnl", 0.0) + pnl
        if pnl > 0:
            d["wins"] = d.get("wins", 0) + 1
        else:
            d["losses"] = d.get("losses", 0) + 1
        self._save_our_pnl()

    def our_wallet_stats(self, addr: str) -> Dict:
        return self._our_wallet_pnl.get((addr or "").lower(), {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0})

    # ------------------------------------------------------------------
    # Quality refresh: re-fetch win_rate/ROI solo wallet attivi (lightweight)
    # ------------------------------------------------------------------
    def should_refresh(self) -> bool:
        if not WALLET_MONITOR.get("enabled", True):
            return False
        if self.last_refresh is None:
            return True
        elapsed = (datetime.now() - self.last_refresh).total_seconds()
        return elapsed >= WALLET_MONITOR.get("quality_refresh_interval_sec", 900)

    def refresh_quality(self, monitored_addresses: List[str]) -> Dict[str, Dict]:
        """
        Re-fetch win_rate/ROI/decided per i wallet attualmente monitorati.
        Usa scanner._wallet_realized_performance (ricostruzione posizioni chiuse).
        Ritorna dict addr -> {win_rate, roi, pnl, decided, name, status, our_stats}
        """
        if not self.scanner:
            return {}
        qualities = {}
        for addr in monitored_addresses:
            try:
                perf = self.scanner._wallet_realized_performance(addr)
                our = self.our_wallet_stats(addr)
                # status: active / disabled (perdente)
                wr = perf.get("win_rate", 0.0)
                our_trades = our.get("trades", 0)
                our_pnl = our.get("pnl", 0.0)
                swap_wr = WALLET_MONITOR.get("swap_wr_threshold", 0.45)
                swap_min = WALLET_MONITOR.get("swap_min_our_trades", 2)
                swap_pnl_thr = WALLET_MONITOR.get("swap_our_pnl_threshold", 0.0)
                status = "active"
                if wr < swap_wr and perf.get("decided", 0) >= 5:
                    status = "disabled"
                if our_trades >= swap_min and our_pnl < swap_pnl_thr:
                    status = "disabled"
                qualities[addr.lower()] = {
                    "win_rate": wr,
                    "roi": perf.get("roi", 0.0),
                    "pnl": perf.get("pnl", 0.0),
                    "decided": perf.get("decided", 0),
                    "our_wins": our.get("wins", 0),
                    "our_losses": our.get("losses", 0),
                    "our_pnl": our_pnl,
                    "our_trades": our_trades,
                    "status": status,
                }
            except Exception as e:
                qualities[addr.lower()] = {"win_rate": 0, "roi": 0, "pnl": 0,
                                           "decided": 0, "status": "unknown",
                                           "our_trades": 0, "our_pnl": 0,
                                           "our_wins": 0, "our_losses": 0}
            time.sleep(0.3)  # rate limit gentile
        self.last_refresh = datetime.now()
        # persist qualities per dashboard + simulator soft-disable
        self._save_scan_quality(qualities)
        return qualities

    def _save_scan_quality(self, qualities: Dict[str, Dict]):
        """Merge qualities nel scan_results.json (status field) per dashboard."""
        try:
            if not self.scan_file.exists():
                return
            with open(self.scan_file, "r") as f:
                data = json.load(f)
            for w in data.get("wallets", []):
                addr = (w.get("address") or "").lower()
                q = qualities.get(addr)
                if q:
                    w["win_rate"] = q["win_rate"]
                    w["roi"] = q["roi"]
                    w["pnl"] = q["pnl"]
                    w["decided"] = q["decided"]
                    w["status"] = q["status"]
            with open(self.scan_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Swap: rimpiazza wallet perdenti con riserve
    # ------------------------------------------------------------------
    def swap_losers(self, monitored_addresses: List[str], qualities: Dict[str, Dict]) -> Tuple[List[str], List[str]]:
        """
        Rimpiazza wallet con status='disabled' con riserve dalla reserve pool.
        Returns (new_monitored_list, swapped_out_addresses)
        """
        if not WALLET_MONITOR.get("enabled", True):
            return monitored_addresses, []
        top_active = WALLET_MONITOR.get("top_active", 15)
        losers = [a for a in monitored_addresses if qualities.get((a or "").lower(), {}).get("status") == "disabled"]
        if not losers:
            return monitored_addresses, []
        # riserve: wallet in scan_results oltre i top_active, con status != disabled
        reserves = self._get_reserve_pool(exclude=set(a.lower() for a in monitored_addresses))
        if not reserves:
            print(f"[WALLET-MGR] {len(losers)} wallet perdenti ma nessuna riserva disponibile")
            return monitored_addresses, losers
        new_list = [a for a in monitored_addresses if a.lower() not in set(l.lower() for l in losers)]
        n_swap = 0
        for loser in losers:
            if not reserves:
                break
            replacement = reserves.pop(0)
            new_list.append(replacement)
            n_swap += 1
            print(f"[WALLET-MGR] SWAP: {loser[:10]}... (WR {qualities[loser.lower()]['win_rate']:.0%}, "
                  f"our P&L ${qualities[loser.lower()].get('our_pnl',0):.2f}) -> {replacement[:10]}...")
        # trim a top_active
        new_list = new_list[:top_active]
        print(f"[WALLET-MGR] {n_swap} wallet swappati, {len(new_list)} attivi")
        return new_list, losers

    def _get_reserve_pool(self, exclude: set) -> List[str]:
        """Riserve: wallet qualificati in scan_results non attivi (oltre top_active)."""
        try:
            if not self.scan_file.exists():
                return []
            with open(self.scan_file, "r") as f:
                data = json.load(f)
            wallets = data.get("wallets", [])
            reserves = []
            for w in wallets:
                addr = (w.get("address") or "").lower()
                if addr in exclude:
                    continue
                if w.get("status") == "disabled":
                    continue
                wr = w.get("win_rate", 0)
                if wr >= WALLET_MONITOR.get("swap_wr_threshold", 0.45):
                    reserves.append(w.get("address", ""))
            return [r for r in reserves if r][:WALLET_MONITOR.get("reserve_pool_size", 20)]
        except Exception:
            return []
