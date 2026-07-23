"""
Wallet history profiler per strategie copy / consenso su Polymarket.

Ricostruisce dal feed /activity dei wallet monitorati le posizioni e calcola
quanto avrebbe reso copiarle, valutando ogni posizione fino alla sua RISOLUZIONE.

PUNTI CHIAVE DI METODO (per evitare risultati falsati):
  - PnL direzionale reale: eventi TRADE (BUY/SELL) e REDEEM. Esclude REWARD e
    MAKER_REBATE (market making non replicabile da un copiatore retail).
  - Anti survivorship-bias: le posizioni PERDENTI non generano un REDEEM, quindi
    restano "aperte" nel feed. Le valutiamo allo snapshot /positions (curPrice):
    una perdente risolta vale 0 -> viene contata come perdita. Senza questo, si
    vedrebbero solo i vincitori (win rate ~100%, ROI assurdo).
  - Anti inflazione da finestra: sui REDEEM accreditiamo solo le shares che abbiamo
    effettivamente tracciato nella finestra (cap), non l'intero payout del whale.
  - Conta solo posizioni DECISE (vendute, o risolte a 0/1). Le posizioni ancora
    aperte (prezzo 0<p<1) sono escluse dal ROI realizzato.

ATTENZIONE: seleziona e valuta i wallet sulla stessa storia e usa il loro prezzo
medio, non il best ask CLOB disponibile quando il bot rileva il segnale. Quindi
NON è un backtest out-of-sample e non dimostra un edge eseguibile.
"""
import sys
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

import requests

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from config import POLYMARKET_API, BUDGET, STRATEGY, SIMULATOR, DATA_DIR
from portfolio_sync import PolymarketPositionFetcher
from categories import categorize_market, taker_fee_fraction

RESOLVED_LOW = 0.02
RESOLVED_HIGH = 0.98


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


class Backtester:
    def __init__(self, activity_limit: int = 1000):
        self.data_api = POLYMARKET_API["data"]
        self.activity_limit = activity_limit
        self.fetcher = PolymarketPositionFetcher()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    # ------------------------------------------------------------------
    def fetch_activity(self, wallet: str) -> List[Dict]:
        try:
            url = f"{self.data_api}/activity"
            r = self.session.get(url, params={"user": wallet, "limit": self.activity_limit}, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[BT] Errore activity {wallet[:10]}...: {e}")
            return []

    def positions_map(self, wallet: str) -> Dict[str, Dict]:
        """asset -> {cur_price, redeemable} dallo snapshot corrente."""
        out = {}
        for p in self.fetcher.get_positions(wallet):
            out[p["asset"]] = {"cur_price": p["cur_price"], "redeemable": p["redeemable"]}
        return out

    # ------------------------------------------------------------------
    def reconstruct_positions(self, activity: List[Dict], posmap: Dict[str, Dict]) -> Dict[str, Dict]:
        """
        Ricostruisce per-asset il PnL realizzato fino alla risoluzione.

        Returns asset -> {title, outcome, condition_id, bought, realized_pnl, roi, decided}
        Solo gli asset DECISI (venduti o risolti a 0/1) hanno decided=True.
        """
        state: Dict[str, Dict] = {}
        cond_assets = defaultdict(set)

        def ensure(asset, ev):
            if asset not in state:
                state[asset] = {
                    "title": ev.get("title", ""),
                    "outcome": ev.get("outcome", ""),
                    "condition_id": ev.get("conditionId", ""),
                    "bought": 0.0,
                    "shares_bought": 0.0,  # shares totali comprate (per prezzo medio ingresso)
                    "realized_pnl": 0.0,
                    "shares": 0.0,
                    "cost": 0.0,
                    "first_buy_ts": None,  # timestamp del primo BUY (ordine consenso)
                }
            return state[asset]

        for ev in sorted(activity, key=lambda e: e.get("timestamp", 0)):
            etype = ev.get("type", "")
            if etype == "TRADE":
                asset = ev.get("asset", "")
                if not asset:
                    continue
                cond = ev.get("conditionId", "")
                s = ensure(asset, ev)
                cond_assets[cond].add(asset)
                side = ev.get("side", "")
                shares = float(ev.get("size", 0) or 0)
                usdc = float(ev.get("usdcSize", 0) or 0)
                if side == "BUY":
                    s["shares"] += shares
                    s["cost"] += usdc
                    s["bought"] += usdc
                    s["shares_bought"] += shares
                    if s["first_buy_ts"] is None:
                        s["first_buy_ts"] = ev.get("timestamp", 0)
                elif side == "SELL" and s["shares"] > 1e-9:
                    frac = min(1.0, shares / s["shares"])
                    cost_sold = s["cost"] * frac
                    s["realized_pnl"] += usdc - cost_sold
                    s["shares"] -= shares
                    s["cost"] -= cost_sold
            elif etype == "REDEEM":
                cond = ev.get("conditionId", "")
                payout = float(ev.get("usdcSize", 0) or 0)
                size = float(ev.get("size", 0) or 0)
                res_per_share = (payout / size) if size > 1e-9 else 1.0
                remaining = size
                # Accredita solo le shares tracciate nella finestra (cap), non tutto il payout
                for a in list(cond_assets.get(cond, set())):
                    s = state[a]
                    if s["shares"] <= 1e-9 or remaining <= 1e-9:
                        continue
                    redeem_shares = min(s["shares"], remaining)
                    frac = redeem_shares / s["shares"]
                    cost_part = s["cost"] * frac
                    s["realized_pnl"] += redeem_shares * res_per_share - cost_part
                    s["shares"] -= redeem_shares
                    s["cost"] -= cost_part
                    remaining -= redeem_shares
            # REWARD / MAKER_REBATE ignorati

        # Finalizza: valuta le shares residue allo snapshot (cattura le perdenti a 0)
        result = {}
        for asset, s in state.items():
            if s["bought"] <= 0:
                continue
            decided = False
            if s["shares"] <= 1e-6:
                decided = True  # interamente venduta/riscattata
            else:
                info = posmap.get(asset)
                if info is not None:
                    cur = info["cur_price"]
                    if info["redeemable"] or cur <= RESOLVED_LOW or cur >= RESOLVED_HIGH:
                        res_price = 1.0 if cur >= 0.5 else 0.0
                        s["realized_pnl"] += s["shares"] * res_price - s["cost"]
                        s["shares"] = 0.0
                        s["cost"] = 0.0
                        decided = True
                    # altrimenti posizione ancora aperta (prezzo vivo) -> non decisa
            if decided:
                s["roi"] = s["realized_pnl"] / s["bought"] if s["bought"] > 0 else 0.0
                s["entry_price"] = (s["bought"] / s["shares_bought"]) if s["shares_bought"] > 0 else 0.0
                result[asset] = s
        return result

    @staticmethod
    def rewards_total(activity: List[Dict]) -> float:
        return sum(
            float(e.get("usdcSize", 0) or 0)
            for e in activity
            if e.get("type") in ("REWARD", "MAKER_REBATE")
        )

    # ------------------------------------------------------------------
    # Modello "entrata tardiva": un copiatore retail NON entra al prezzo del
    # wallet, ma piu tardi (e con slippage + fee). Stimiamo:
    #   - valore a risoluzione per share: res = entry_price * (1 + roi_del_wallet)
    #   - prezzo che PAGHEREMMO noi: prezzo di mercato tardivo * (1+slippage) * (1+fee)
    #   - ROI realistico = res / prezzo_pagato - 1
    # ------------------------------------------------------------------
    @staticmethod
    def _effective_buy(market_price: float, category: str) -> float:
        slip = SIMULATOR.get("entry_slippage", 0.0)
        eff = market_price * (1 + slip)
        fee = taker_fee_fraction(category, eff)
        return eff * (1 + fee)

    def _late_copy_position(self, p: Dict) -> Dict:
        """Trasforma una posizione copy nel suo equivalente a entrata tardiva."""
        entry = p.get("entry_price", 0.0)
        if entry <= 0:
            return p
        category = categorize_market(p.get("title", ""))
        res = entry * (1 + p["roi"])                 # valore a risoluzione per share
        paid = self._effective_buy(entry, category)  # noi paghiamo entry + slip + fee
        roi_late = (res / paid - 1) if paid > 0 else 0.0
        q = dict(p)
        q["roi"] = roi_late
        q["entry_price"] = entry          # banda sul prezzo di mercato
        q["realized_pnl"] = (res - paid) * (p["bought"] / entry if entry > 0 else 0.0)
        q["category"] = category
        return q

    def _build_consensus_position(self, plist: List[Dict], late_entry: bool) -> Dict:
        title = plist[0]["title"]
        category = categorize_market(title)
        if not late_entry:
            avg_roi = sum(p["roi"] for p in plist) / len(plist)
            return {
                "roi": avg_roi,
                "realized_pnl": sum(p["realized_pnl"] for p in plist) / len(plist),
                "bought": sum(p["bought"] for p in plist) / len(plist),
                "entry_price": sum(p.get("entry_price", 0.0) for p in plist) / len(plist),
                "title": title, "outcome": plist[0]["outcome"],
                "holders": len(plist), "category": category,
            }
        # Entrata tardiva: il consenso si forma quando il K-esimo wallet entra.
        # Ordiniamo per timestamp del primo acquisto e prendiamo il prezzo di
        # mercato a quel punto = entry_price del wallet che completa il consenso.
        ordered = sorted(plist, key=lambda p: (p.get("first_buy_ts") or 0))
        k_idx = min(len(ordered) - 1, max(0, STRATEGY.get("min_wallets_consensus", 2) - 1))
        late_market_price = ordered[k_idx].get("entry_price", 0.0)
        res = _median([p.get("entry_price", 0.0) * (1 + p["roi"]) for p in plist])
        paid = self._effective_buy(late_market_price, category)
        roi_late = (res / paid - 1) if paid > 0 else 0.0
        avg_bought = sum(p["bought"] for p in plist) / len(plist)
        return {
            "roi": roi_late,
            "realized_pnl": (res - paid) * (avg_bought / late_market_price if late_market_price > 0 else 0.0),
            "bought": avg_bought,
            "entry_price": late_market_price,
            "title": title, "outcome": plist[0]["outcome"],
            "holders": len(plist), "category": category,
        }

    # ------------------------------------------------------------------
    def run(self, wallets: List[Dict], min_consensus: int = 2,
            min_price: float = 0.0, max_price: float = 1.0,
            late_entry: bool = False) -> Dict:
        print(f"\n{'='*70}")
        print(f"  WALLET HISTORY PROFILER - dati in-sample, NON prova di edge")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | finestra ~{self.activity_limit} attivita/wallet")
        print(f"{'='*70}\n")
        print(f"  {'wallet':22} | {'pos':>3} | {'ROI':>8} | {'WR':>4} | {'PnL':>12}")
        print(f"  {'-'*22}-+-{'-'*3}-+-{'-'*8}-+-{'-'*4}-+-{'-'*12}")

        per_wallet = {}
        asset_positions: Dict[str, List[Dict]] = defaultdict(list)
        rewards_sum = 0.0

        for w in wallets:
            addr = w["address"]
            name = w.get("name") or addr[:10]
            activity = self.fetch_activity(addr)
            rewards_sum += self.rewards_total(activity)
            posmap = self.positions_map(addr)
            closed = self.reconstruct_positions(activity, posmap)

            pnl = sum(p["realized_pnl"] for p in closed.values())
            bought = sum(p["bought"] for p in closed.values())
            wins = sum(1 for p in closed.values() if p["realized_pnl"] > 0)
            n = len(closed)
            roi = (pnl / bought) if bought > 0 else 0.0
            per_wallet[addr] = {
                "name": name, "positions": n, "pnl": pnl, "bought": bought,
                "roi": roi, "win_rate": (wins / n if n else 0.0),
            }
            print(f"  {name[:22]:22} | {n:3} | {roi:7.1%} | {(wins/n if n else 0):4.0%} | ${pnl:>11,.0f}")

            for asset, p in closed.items():
                q = dict(p)
                q["wallet"] = addr
                asset_positions[asset].append(q)
            time.sleep(0.3)

        # COPY: tutte le posizioni decise (equal-weight = nostra size fissa)
        copy_positions = [p for plist in asset_positions.values() for p in plist]
        if late_entry:
            copy_positions = [self._late_copy_position(p) for p in copy_positions]
        copy_stats = self._aggregate(copy_positions)

        # CONSENSO: asset detenuti da >= K wallet diversi
        consensus_positions = []
        for a, plist in asset_positions.items():
            if len(plist) < min_consensus:
                continue
            consensus_positions.append(self._build_consensus_position(plist, late_entry))

        # --- Analisi per fascia di prezzo d'ingresso (prima di applicare il filtro) ---
        self._print_price_bands("COPY", copy_positions)
        self._print_price_bands("CONSENSO", consensus_positions)

        # --- Applica filtro fascia di prezzo (ex-ante: lo conosciamo all'ingresso) ---
        def in_band(p):
            ep = p.get("entry_price", 0.0)
            return min_price <= ep <= max_price

        if min_price > 0.0 or max_price < 1.0:
            copy_positions = [p for p in copy_positions if in_band(p)]
            consensus_positions = [p for p in consensus_positions if in_band(p)]

        copy_stats = self._aggregate(copy_positions)
        consensus_stats = self._aggregate(consensus_positions)

        capital = BUDGET["initial_capital"]
        band_lbl = f" | filtro prezzo {min_price:.2f}-{max_price:.2f}" if (min_price > 0 or max_price < 1) else ""
        mode_lbl = " | ENTRATA TARDIVA (slippage+fee)" if late_entry else " | ottimistico (prezzo del wallet)"
        self._print_strategy(f"COPY (tutte le posizioni, equal-weight){band_lbl}{mode_lbl}", copy_stats, capital)
        self._print_strategy(f"CONSENSO (>= {min_consensus} wallet sullo stesso asset){band_lbl}{mode_lbl}", consensus_stats, capital)

        if consensus_positions:
            print("\n  Posizioni di consenso:")
            for p in sorted(consensus_positions, key=lambda x: -x["holders"])[:15]:
                print(f"    [{p['holders']}x] {p['title'][:45]:45} {str(p['outcome'])[:12]:12} ROI {p['roi']:+.0%}")

        print(f"\n{'='*70}")
        print(f"  REWARD / MAKER_REBATE totali (NON copiabili): ${rewards_sum:,.0f}")
        print(f"  -> profitto da market making che un copiatore retail non ottiene")
        print(f"{'='*70}\n")

        report = {
            "report_type": "wallet_history_profiler",
            "edge_claim": False,
            "methodology_warning": (
                "In-sample wallet selection; wallet average entry, not "
                "detection-time executable CLOB ask."
            ),
            "generated_at": datetime.now().isoformat(),
            "activity_limit": self.activity_limit,
            "min_consensus": min_consensus,
            "late_entry": late_entry,
            "price_filter": {"min": min_price, "max": max_price},
            "per_wallet": per_wallet,
            "copy": copy_stats,
            "consensus": consensus_stats,
            "non_copyable_rewards": rewards_sum,
        }
        out = DATA_DIR / "backtest_results.json"
        with open(out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[BT] Report salvato in {out}\n")
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def _aggregate(positions: List[Dict]) -> Dict:
        n = len(positions)
        if n == 0:
            return {"positions": 0, "roi_equal_weight": 0.0, "roi_capital_weight": 0.0,
                    "win_rate": 0.0, "total_pnl": 0.0, "total_bought": 0.0,
                    "median_roi": 0.0}
        rois = sorted(p["roi"] for p in positions)
        roi_eq = sum(rois) / n
        median = rois[n // 2] if n % 2 else (rois[n // 2 - 1] + rois[n // 2]) / 2
        total_pnl = sum(p["realized_pnl"] for p in positions)
        total_bought = sum(p["bought"] for p in positions)
        roi_cap = (total_pnl / total_bought) if total_bought > 0 else 0.0
        wins = sum(1 for p in positions if p["realized_pnl"] > 0)
        return {
            "positions": n,
            "roi_equal_weight": roi_eq,
            "median_roi": median,
            "roi_capital_weight": roi_cap,
            "win_rate": wins / n,
            "total_pnl": total_pnl,
            "total_bought": total_bought,
        }

    @staticmethod
    def _print_price_bands(label: str, positions: List[Dict]):
        bands = [(0.0, 0.10), (0.10, 0.30), (0.30, 0.50),
                 (0.50, 0.70), (0.70, 0.90), (0.90, 1.01)]
        print(f"\n{'-'*70}")
        print(f"  {label} - ANALISI PER FASCIA DI PREZZO D'INGRESSO")
        print(f"{'-'*70}")
        print(f"  {'fascia':>11} | {'pos':>4} | {'WR':>5} | {'ROI med':>8} | {'PnL tot':>12}")
        print(f"  {'-'*11}-+-{'-'*4}-+-{'-'*5}-+-{'-'*8}-+-{'-'*12}")
        for lo, hi in bands:
            grp = [p for p in positions if lo <= p.get("entry_price", 0.0) < hi]
            if not grp:
                continue
            rois = sorted(p["roi"] for p in grp)
            n = len(grp)
            med = rois[n // 2] if n % 2 else (rois[n // 2 - 1] + rois[n // 2]) / 2
            wins = sum(1 for p in grp if p["realized_pnl"] > 0)
            pnl = sum(p["realized_pnl"] for p in grp)
            print(f"  {lo:.2f}-{hi if hi <= 1 else 1.0:.2f} | {n:4} | {wins/n:4.0%} | "
                  f"{med:+7.1%} | ${pnl:>11,.0f}")

    @staticmethod
    def _print_strategy(label: str, stats: Dict, capital: float):
        print(f"\n{'-'*70}")
        print(f"  STRATEGIA: {label}")
        print(f"{'-'*70}")
        if stats["positions"] == 0:
            print("  Nessuna posizione decisa in questa finestra.")
            return
        print(f"  Posizioni decise:    {stats['positions']}")
        print(f"  Win rate:            {stats['win_rate']:.1%}")
        print(f"  ROI mediano:         {stats['median_roi']:+.1%}   <- robusto agli outlier")
        print(f"  ROI medio (eq.peso): {stats['roi_equal_weight']:+.1%}")
        print(f"  ROI ponderato cap.:  {stats['roi_capital_weight']:+.1%}")
        print(f"  Equity su ${capital:.0f} (ROI mediano): ${capital * (1 + stats['median_roi']):,.0f}")


if __name__ == "__main__":
    import argparse
    results_file = DATA_DIR / "scan_results.json"
    if not results_file.exists():
        print("[BT] data/scan_results.json mancante. Esegui prima lo scanner.")
        sys.exit(1)
    with open(results_file) as f:
        wallets = json.load(f).get("wallets", [])

    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=len(wallets))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--consensus", type=int, default=STRATEGY.get("min_wallets_consensus", 2))
    parser.add_argument("--min-price", type=float,
                        default=STRATEGY.get("entry_price_min", 0.0),
                        help="prezzo minimo d'ingresso (default config)")
    parser.add_argument("--max-price", type=float,
                        default=STRATEGY.get("entry_price_max", 1.0),
                        help="prezzo massimo d'ingresso (default config)")
    parser.add_argument("--late-entry", action="store_true",
                        help="simula entrata tardiva (slippage+fee, prezzo del K-esimo wallet)")
    args = parser.parse_args()

    bt = Backtester(activity_limit=args.limit)
    bt.run(wallets[:args.top], min_consensus=args.consensus,
           min_price=args.min_price, max_price=args.max_price,
           late_entry=args.late_entry)
