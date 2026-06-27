"""
Main orchestrator per Polymarket Paper Trading Bot (mirroring di portafoglio).

Ciclo: ottieni i wallet target -> snapshot delle loro posizioni -> riconcilia il
portafoglio simulato (apri/aggiorna/chiudi) secondo la strategia (copy/consenso).
"""
import sys
import time
import json
import signal
import os
from datetime import datetime
from typing import List, Optional

# Force UTF-8 encoding for Windows console
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from scanner import PolymarketScanner
from analyzer import WalletAnalyzer
from portfolio_sync import PolymarketPositionFetcher
from simulator import PaperTradingSimulator
from models import WalletAnalysis
from config import BUDGET, STRATEGY, TRACKING, SCANNER, DATA_DIR, BASE_DIR


class PolymarketPaperTradingBot:
    """Bot principale per paper trading su Polymarket via mirroring posizioni."""

    def __init__(self):
        self.scanner = PolymarketScanner()
        self.analyzer = WalletAnalyzer()
        self.fetcher = PolymarketPositionFetcher()
        self.simulator = PaperTradingSimulator(BUDGET["initial_capital"])

        self.qualified_wallets: List[WalletAnalysis] = []
        self.monitored_addresses: List[str] = []
        self.running = False

        pid_file = BASE_DIR / "data" / "bot.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print("\n\n[BOT] Shutdown richiesto...")
        self.running = False
        pid_file = BASE_DIR / "data" / "bot.pid"
        if pid_file.exists():
            pid_file.unlink()

    # ------------------------------------------------------------------
    # Selezione wallet da monitorare
    # ------------------------------------------------------------------
    def load_monitored_from_file(self) -> List[str]:
        """Carica gli indirizzi dei top wallet da data/scan_results.json se presente."""
        results_file = DATA_DIR / "scan_results.json"
        if not results_file.exists():
            return []
        try:
            with open(results_file, "r") as f:
                data = json.load(f)
            wallets = data.get("wallets", [])[: STRATEGY["top_wallets"]]
            return [w["address"] for w in wallets if w.get("address")]
        except Exception:
            return []

    def _scan_age_sec(self) -> Optional[float]:
        """Eta' dell'ultimo scan in secondi, o None se file assente/illeggibile."""
        results_file = DATA_DIR / "scan_results.json"
        if not results_file.exists():
            return None
        try:
            with open(results_file, "r") as f:
                data = json.load(f)
            scan_time = data.get("scan_time")
            if not scan_time:
                return None
            scanned_at = datetime.fromisoformat(scan_time)
            return (datetime.now() - scanned_at).total_seconds()
        except Exception:
            return None

    def _is_scan_stale(self) -> bool:
        if not SCANNER.get("auto_rescan_enabled", True):
            return False
        age = self._scan_age_sec()
        if age is None:
            return True
        return age >= SCANNER["auto_rescan_interval_sec"]

    def _run_wallet_scan(self) -> bool:
        """Riscopre wallet specialisti per categoria e salva scan_results.json."""
        print("[BOT] Scan wallet specialisti (categorie)...")
        try:
            wallets = self.scanner.scan_categories(top_n=STRATEGY["top_wallets"])
            if wallets:
                return True
            print("[BOT] Scan completato senza wallet qualificati.")
            return False
        except Exception as e:
            print(f"[BOT] Scan fallito: {e}")
            return False

    def _reload_monitored_wallets(self) -> bool:
        new_addrs = self.load_monitored_from_file()
        if not new_addrs:
            return False
        old = set(self.monitored_addresses)
        new_set = set(new_addrs)
        added = new_set - old
        removed = old - new_set
        self.monitored_addresses = new_addrs
        if added or removed:
            print(
                f"[BOT] Lista wallet aggiornata: {len(new_addrs)} attivi "
                f"(+{len(added)} nuovi, -{len(removed)} usciti)"
            )
        return True

    def _maybe_auto_rescan(self) -> None:
        """Durante il loop, aggiorna la lista wallet se lo scan e' obsoleto."""
        if not self._is_scan_stale():
            return
        age_h = (self._scan_age_sec() or 0) / 3600
        print(f"\n[BOT] Scan obsoleto ({age_h:.1f}h) — aggiornamento automatico...")
        if self._run_wallet_scan():
            self._reload_monitored_wallets()

    def run_initial_scan(self, top_n: int = 20) -> bool:
        """Scansione leaderboard + analisi per trovare wallet profittevoli."""
        print(f"\n{'='*60}")
        print(f"POLYMARKET PAPER TRADING BOT")
        print(f"{'='*60}")
        print(f"Budget: ${BUDGET['initial_capital']} | Strategia: {STRATEGY['mode']}")
        print(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

        print("[FASE 1] Scansione wallet profittevoli...")
        wallets = self.scanner.scan_all(top_n=top_n)
        if not wallets:
            print("[ERRORE] Nessun wallet trovato. Controlla connessione internet.")
            return False

        print(f"\n[FASE 2] Analisi dettagliata {len(wallets)} wallet...")
        analyses = []
        for i, wallet in enumerate(wallets, 1):
            print(f"[{i}/{len(wallets)}] Analizzando {wallet.name}...")
            activity = self.scanner.get_wallet_activity(wallet.address, limit=100)
            if not activity:
                continue
            analyses.append(self.analyzer.analyze_wallet(wallet, activity))
            time.sleep(0.5)

        print(f"\n[FASE 3] Classificazione wallet...")
        self.qualified_wallets = self.analyzer.rank_wallets(analyses)

        print(f"\nWallet qualificati: {len(self.qualified_wallets)}")
        if not self.qualified_wallets:
            print("[WARNING] Nessun wallet qualificato. Uso i top per ROI dallo scan.")
            self.monitored_addresses = [w.address for w in wallets[: STRATEGY["top_wallets"]]]
            return bool(self.monitored_addresses)

        for i, a in enumerate(self.qualified_wallets[:10], 1):
            print(f"{i:2}. {a.wallet_name:25} | Score {a.qualification_score:5.1f} | "
                  f"ROI {a.roi:.1%} | WR {a.win_rate:.1%}")

        self.monitored_addresses = [
            w.wallet_address for w in self.qualified_wallets[: STRATEGY["top_wallets"]]
        ]
        return True

    def ensure_monitored_wallets(self) -> bool:
        """Garantisce wallet da monitorare; scan automatico se assenti o obsoleti."""
        addresses = self.load_monitored_from_file()
        if not addresses:
            print("[BOT] scan_results.json assente — scan iniziale automatico...")
            if not self._run_wallet_scan():
                print("[BOT] Fallback scan legacy leaderboard...")
                return self.run_initial_scan(top_n=STRATEGY["top_wallets"])
            addresses = self.load_monitored_from_file()

        self.monitored_addresses = addresses
        print(f"[BOT] {len(addresses)} wallet caricati da scan_results.json")

        if self._is_scan_stale():
            age_h = (self._scan_age_sec() or 0) / 3600
            print(f"[BOT] Lista datata ({age_h:.1f}h) — refresh automatico all'avvio...")
            if self._run_wallet_scan():
                self._reload_monitored_wallets()

        next_h = SCANNER["auto_rescan_interval_sec"] / 3600
        print(f"[BOT] Auto-rescan ogni {next_h:.0f}h (nessun intervento manuale richiesto)")
        return bool(self.monitored_addresses)

    # ------------------------------------------------------------------
    # Loop principale di mirroring
    # ------------------------------------------------------------------
    def _min_wallets(self) -> int:
        if STRATEGY["mode"] == "consensus":
            return max(1, STRATEGY["min_wallets_consensus"])
        return 1  # copy puro

    def run_mirror_loop(self):
        if not self.monitored_addresses:
            print("[ERRORE] Nessun wallet da monitorare.")
            return

        min_wallets = self._min_wallets()
        print(f"\n{'='*60}")
        print(f"AVVIO MIRRORING ({STRATEGY['mode']}, soglia consenso: {min_wallets})")
        print(f"Wallet monitorati: {len(self.monitored_addresses)}")
        for i, addr in enumerate(self.monitored_addresses, 1):
            print(f"  {i}. {addr}")
        print(f"Polling ogni {TRACKING['poll_interval']}s - Ctrl+C per stoppare")
        print(f"{'='*60}\n")

        self.running = True
        try:
            while self.running:
                self._maybe_auto_rescan()

                cycle_start = datetime.now().strftime('%H:%M:%S')
                print(f"\n[{cycle_start}] Snapshot posizioni...")

                aggregate = self.fetcher.snapshot_wallets(self.monitored_addresses)
                print(f"  {len(aggregate)} asset distinti rilevati tra i wallet")

                self.simulator.reconcile(aggregate, min_wallets, self.fetcher)

                summary = self.simulator.get_portfolio_summary()
                print(f"  Equity: ${summary['current_value']:.2f} "
                      f"({summary['total_pnl_pct']:+.2f}%) | "
                      f"Aperte: {summary['open_positions']} | "
                      f"Chiuse: {summary['closed_positions']} "
                      f"(WR {summary['win_rate']:.0f}%)")

                # Sleep interrompibile
                for _ in range(TRACKING["poll_interval"]):
                    if not self.running:
                        break
                    time.sleep(1)
        except KeyboardInterrupt:
            print("\n[BOT] Interrotto dall'utente")

    def run_full_cycle(self):
        if not self.ensure_monitored_wallets():
            return
        self.simulator.print_portfolio_summary()
        try:
            self.run_mirror_loop()
        finally:
            print(f"\n{'='*60}")
            print(f"SESSIONE TERMINATA")
            print(f"{'='*60}")
            self.simulator.print_portfolio_summary()


def main():
    bot = PolymarketPaperTradingBot()
    print("\n=== POLYMARKET PAPER TRADING BOT (mirroring copy/consenso) ===\n")
    bot.run_full_cycle()


if __name__ == "__main__":
    main()
