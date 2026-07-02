"""
Strategie complementari al copy-trading (Phase M/Q).

COPY vive ancora nel simulator.reconcile (gestione delta per-wallet complessa);
qui ci sono le strategie che non dipendono dai wallet monitorati:

  - ArbBinaryStrategy  (Phase N): YES+NO <$1 stesso conditionId → risk-free-ish
  - HarvestStrategy   (Phase O): lato vincente 0.85-0.975, endTime <7gg
  - ArbCrossStrategy  (Phase P): eventi esaustivi multi-outcome, sum_ask <$1

Ogni strategia espone:
  - scan(fetcher)         -> List[Opportunity]
  - build_position_info(opp, fetcher) -> dict pronto per simulator.open_strategy_position

L'esecuzione reale (sizing, cap, reservation, open/close) è nel simulator, che è
l'unica entità che tocca il portfolio. Le strategie producono solo candidati.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from datetime import datetime
import json
from pathlib import Path

from config import STRATEGIES, BUDGET, POLYMARKET_API, DATA_DIR
from categories import taker_fee_fraction


@dataclass
class Opportunity:
    """Candidato generato da una strategia. È astratto finché non eseguito."""
    strategy: str                       # arb_binary | harvest | arb_cross
    condition_id: str
    market_title: str
    event_slug: str
    category: str
    end_date: str
    # per arb_binary: (asset_yes, asset_no)
    # per harvest:    (asset_favored, None)  -- asset su cui compriamo
    # per arb_cross:  (asset_yes_a, asset_yes_b, ...) lista completa
    assets: List[str] = field(default_factory=list)
    outcomes: List[str] = field(default_factory=list)
    # prezzo di entrata per il bundle (arb_binary/cross) o singolo leg (harvest)
    # cost_per_share = sum(best_ask) per le gambe del bundle
    cost_per_share: float = 0.0
    best_asks: List[float] = field(default_factory=list)
    book_sizes: List[float] = field(default_factory=list)
    spread_cents: List[float] = field(default_factory=list)
    payout_per_share: float = 1.0       # bundle paga $1/share a resolution
    profit_per_share: float = 0.0       # payout - cost - fees - safety
    max_fill_size: float = 0.0          # USDC riempibili dati book size (min sulle gambe)
    fee_type: str = ""
    score: float = 0.0                  # ranking (profit_per_share o APR)


# ----------------------------------------------------------------------
# Utility fee modellate come in categories.taker_fee_fraction (sport = rate*min(p,1-p))
# ----------------------------------------------------------------------
def _leg_fee_fraction(fee_type: str, price: float) -> float:
    cat = "sport" if (fee_type and "sport" in fee_type.lower()) else "other"
    return taker_fee_fraction(cat, price)


# ----------------------------------------------------------------------
# Arb binario: YES + NO < $1 su stesso conditionId
# ----------------------------------------------------------------------
class ArbBinaryStrategy:
    name = "arb_binary"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.min_profit_abs = cfg["min_profit_abs"]
        self.safety = cfg["safety_margin"]
        self.max_days = cfg["max_days_to_expiry"]
        self.scan_markets = cfg["scan_markets"]

        self.gamma = POLYMARKET_API["gamma"]

    def scan(self, fetcher) -> List[Opportunity]:
        """Scansiona top mercati attivi, cerca YES+NO<$1 post-fees+safety."""
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=1000.0)
        opps: List[Opportunity] = []
        for m in markets:
            # saltiamo sport: fee mangia quasi tutto lo spread; LNGa olive
            if m.get("category") == "sport":
                continue
            tokens = m["tokens"]
            outcomes = m["outcomes"]
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            # end date filter (APR alta)
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days > self.max_days or days <= 0:
                continue
            book_yes = fetcher.get_book(tokens[0])
            book_no = fetcher.get_book(tokens[1])
            if not book_yes or not book_no:
                continue
            ask_yes = book_yes.get("best_ask"); ask_no = book_no.get("best_ask")
            if ask_yes is None or ask_no is None:
                continue
            if ask_yes <= 0 or ask_no <= 0 or ask_yes >= 1 or ask_no >= 1:
                continue
            sz_yes = float(book_yes.get("ask_size") or 0.0)
            sz_no = float(book_no.get("ask_size") or 0.0)
            # fee per leg (sport no qui, ma footballto)
            fee_yes = _leg_fee_fraction(m.get("fee_type", ""), ask_yes) * ask_yes
            fee_no = _leg_fee_fraction(m.get("fee_type", ""), ask_no) * ask_no
            cost = ask_yes + ask_no
            profit_per_share = 1.0 - cost - fee_yes - fee_no - self.safety
            if profit_per_share <= 0:
                continue
            # fill size in USDC: min(book) — diamo both fills a stesso prezzo e
            # riduciamo a "equal shares" (req per lock payout $1)
            max_fillable_shares = min(sz_yes, sz_no)
            max_fill_size = max_fillable_shares * cost
            if max_fill_size < BUDGET["min_position_size"]:
                continue
            # profitto assoluto sul fill massimo
            abs_profit = max_fillable_shares * profit_per_share
            if abs_profit < self.min_profit_abs:
                continue
            spread_cents = max((ask_yes - book_yes.get("best_bid", ask_yes)) * 100 if book_yes.get("best_bid") else 99,
                               (ask_no - book_no.get("best_bid", ask_no)) * 100 if book_no.get("best_bid") else 99)
            opp = Opportunity(
                strategy=self.name,
                condition_id=m["condition_id"],
                market_title=m["question"],
                event_slug=m["event_slug"],
                category=m["category"],
                end_date=m["end_date"],
                assets=[tokens[0], tokens[1]],
                outcomes=[outcomes[0], outcomes[1]],
                cost_per_share=cost,
                best_asks=[ask_yes, ask_no],
                book_sizes=[sz_yes, sz_no],
                spread_cents=[spread_cents, spread_cents],
                payout_per_share=1.0,
                profit_per_share=profit_per_share,
                max_fill_size=max_fill_size,
                fee_type=m.get("fee_type", ""),
                score=profit_per_share,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Harvest: lato vincente 0.85-0.975, endTime <7gg
# ----------------------------------------------------------------------
class HarvestStrategy:
    name = "harvest"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.fav_min = cfg["fav_min"]
        self.fav_max = cfg["fav_max"]
        self.max_days = cfg["max_days_to_expiry"]
        self.min_book = cfg["min_book_size"]
        self.max_spread_ticks = cfg["max_spread_ticks"]
        self.scan_markets = cfg["scan_markets"]

    def scan(self, fetcher) -> List[Opportunity]:
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=500.0)
        opps: List[Opportunity] = []
        for m in markets:
            tokens = m["tokens"]; outcomes = m["outcomes"]
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days > self.max_days or days <= 0:
                continue
            # blowout risk: skip politics/referendum (sorprendibili); ok sport/crypto/weather/other
            if m["category"] == "politics":
                continue
            for i, (tok, out) in enumerate(zip(tokens, outcomes)):
                book = fetcher.get_book(tok)
                if not book:
                    continue
                ask = book.get("best_ask")
                if ask is None or ask < self.fav_min or ask > self.fav_max:
                    continue
                bid = book.get("best_bid")
                if bid is None:
                    continue
                spread_c = (ask - bid) * 100
                if spread_c > self.max_spread_ticks * 0.01 * 100:
                    continue
                ask_sz = float(book.get("ask_size") or 0.0)
                if ask_sz < self.min_book:
                    continue
                # payout $1, profitto (1-ask) per share; APR = profit/days
                profit_per_share = 1.0 - ask - _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
                if profit_per_share <= 0:
                    continue
                apr = (profit_per_share / ask) / max(days, 0.1) * 365
                # fill size
                max_fill = ask_sz * ask
                if max_fill < BUDGET["min_position_size"]:
                    continue
                # hard SL economico: se prezzo <0.90 ci siamo freschi; imponiamo ask<=0.975 (gia)
                opp = Opportunity(
                    strategy=self.name,
                    condition_id=m["condition_id"],
                    market_title=m["question"],
                    event_slug=m["event_slug"],
                    category=m["category"],
                    end_date=m["end_date"],
                    assets=[tok, ""],
                    outcomes=[out, ""],
                    cost_per_share=ask,
                    best_asks=[ask],
                    book_sizes=[ask_sz],
                    spread_cents=[spread_c],
                    payout_per_share=1.0,
                    profit_per_share=profit_per_share,
                    max_fill_size=max_fill,
                    fee_type=m.get("fee_type", ""),
                    score=apr,   # meglio APR alto
                )
                opps.append(opp)
                break  # un solo lato per mercato per evitare sovrapposizione
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Arb cross: eventi esaustivi multi-outcome, sum best_ask(YES_i) < $1
# ----------------------------------------------------------------------
class ArbCrossStrategy:
    name = "arb_cross"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.min_profit_abs = cfg["min_profit_abs"]
        self.safety = cfg["safety_margin"]
        self.min_outcomes = cfg["min_outcomes"]
        self.max_outcomes = cfg["max_outcomes"]
        self.scan_events = cfg["scan_events"]

    def scan(self, fetcher) -> List[Opportunity]:
        events = fetcher.get_active_events(limit=self.scan_events * 2)
        opps: List[Opportunity] = []
        seen_events = set()
        for ev in events[:self.scan_events]:
            slug = ev["slug"]
            if not slug or slug in seen_events:
                continue
            seen_events.add(slug)
            markets = fetcher.get_event_markets(slug)
            if not markets:
                continue
            n = len(markets)
            if n < self.min_outcomes or n > self.max_outcomes:
                continue
            # per ogni sotto-mercato prendiamo asset YES (tokens[0] nelle Normalizzate
            # gamma outcomes=[Yes,No]) ed il best_ask
            asks, assets, sizes, fee_type, spread_cents = [], [], [], "", []
            ok = True
            for m in markets:
                if len(m["tokens"]) < 2:
                    ok = False; break
                # salta eventi con mercato gia risolto
                book = fetcher.get_book(m["tokens"][0])
                if not book or book.get("best_ask") is None:
                    ok = False; break
                ask = book["best_ask"]
                ask_sz = float(book.get("ask_size") or 0.0)
                bid = book.get("best_bid", ask)
                asks.append(ask); assets.append(m["tokens"][0])
                sizes.append(ask_sz); spread_cents.append((ask - bid) * 100)
                if fee_type == "":
                    fee_type = m.get("fee_type", "")
            if not ok:
                continue
            # end date del primo mercato (proxy scadenza evento)
            end_date = markets[0]["end_date"]
            days = fetcher.days_to_expiry(end_date)
            if days is None or days > STRATEGIES[self.name].get("max_days", 21) or days <= 0:
                # fallback: accetta se giorni < 30 (no capital lock lungo)
                if days is not None and days > 30:
                    continue
            sum_ask = sum(asks)
            # fee per ogni leg
            total_fee = sum(_leg_fee_fraction(fee_type, a) * a for a in asks)
            profit_per_share = 1.0 - sum_ask - total_fee - self.safety
            if profit_per_share <= 0:
                continue
            max_fillable_shares = min(sizes)  # equal shares su ogni leg
            max_fill_size = max_fillable_shares * sum_ask
            if max_fill_size < BUDGET["min_position_size"]:
                continue
            abs_profit = max_fillable_shares * profit_per_share
            if abs_profit < self.min_profit_abs:
                continue
            # title sintetico
            title = f"{ev['title']} ({n}-way basket)"
            opp = Opportunity(
                strategy=self.name,
                condition_id=slug,  # uso slug come id bundle (n-mercato)
                market_title=title,
                event_slug=slug,
                category="other",   # n-leg, variegato
                end_date=end_date,
                assets=assets,
                outcomes=["Yes"] * len(assets),
                cost_per_share=sum_ask,
                best_asks=asks,
                book_sizes=sizes,
                spread_cents=spread_cents,
                payout_per_share=1.0,
                profit_per_share=profit_per_share,
                max_fill_size=max_fill_size,
                fee_type=fee_type,
                score=profit_per_share,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Phase W: PriceHistory tracker — memorizza prezzi per market across cicli
# ----------------------------------------------------------------------
class PriceHistory:
    """Tracker prezzi per-market persistente. Usato da MomentumStrategy."""

    def __init__(self, max_points: int = 60):
        self.max_points = max_points
        self._file = DATA_DIR / "price_history.json"
        # condition_id -> list of (cycle_index, price, ts_iso)
        self.history: Dict[str, list] = {}
        self._load()

    def _load(self):
        try:
            if self._file.exists():
                with open(self._file, "r") as f:
                    self.history = json.load(f)
        except Exception:
            self.history = {}

    def _save(self):
        try:
            with open(self._file, "w") as f:
                json.dump(self.history, f)
        except Exception:
            pass

    def record(self, condition_id: str, price: float, cycle: int):
        """Aggiunge un punto prezzo per condition_id al ciclo cycle."""
        if not condition_id or price <= 0 or price >= 1:
            return
        pts = self.history.setdefault(condition_id, [])
        # evita duplicati stesso ciclo
        if pts and pts[-1][0] == cycle:
            pts[-1] = [cycle, price, datetime.now().isoformat()]
        else:
            pts.append([cycle, price, datetime.now().isoformat()])
        # trim
        if len(pts) > self.max_points:
            pts = pts[-self.max_points:]
            self.history[condition_id] = pts

    def get_window(self, condition_id: str, window_cycles: int) -> Optional[List[Tuple[int, float]]]:
        """Ritorna ultimi N punti (cycle, price) o None se insufficienti."""
        pts = self.history.get(condition_id)
        if not pts or len(pts) < 2:
            return None
        return [(p[0], p[1]) for p in pts[-window_cycles:]]

    def cleanup_stale(self, max_age_cycles: int = 200, current_cycle: int = 0):
        """Rimuove condition_id con history troppo vecchia (non aggiornata di recente).
        NON rimuove entry con 1 solo punto se sono freschi (serve warmup)."""
        if current_cycle == 0:
            # fallback: usa l'ultimo cycle noto in history
            for pts in self.history.values():
                if pts and pts[-1][0] > current_cycle:
                    current_cycle = pts[-1][0]
        stale = []
        for cid, pts in self.history.items():
            if not pts:
                stale.append(cid)
                continue
            last_cycle = pts[-1][0]
            # rimuovi solo se non aggiornato da max_age_cycles (vecchio/stale)
            if current_cycle - last_cycle > max_age_cycles:
                stale.append(cid)
        for cid in stale:
            del self.history[cid]
        if len(self.history) > 500:
            items = sorted(self.history.items(), key=lambda kv: kv[1][-1][0] if kv[1] else 0, reverse=True)
            self.history = dict(items[:300])

    def save(self):
        self._save()


# ----------------------------------------------------------------------
# Momentum: trend-following su prezzo Polymarket (Phase W)
# ----------------------------------------------------------------------
class MomentumStrategy:
    """
    Rileva mercati con forte trend di prezzo recente e compra il lato trending.

    Meccanismo: traccia prezzi ogni ciclo. Se in finestra N cicli il prezzo si è
    mosso >= min_move_pct in una direzione, compra quel lato (momentum continuation).
    - YES salito >=5% → compra YES (trend up)
    - YES sceso >=5%  → compra NO (trend down = NO up)
    TP +6% / SL -5% (inversione → esci).
    """
    name = "momentum"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.min_move = cfg["min_move_pct"]
        self.window = cfg["window_cycles"]
        self.min_book = cfg["min_book_size"]
        self.max_spread = cfg["max_spread_ticks"]
        self.min_days = cfg["min_days_to_expiry"]
        self.max_days = cfg["max_days_to_expiry"]
        self.scan_markets = cfg["scan_markets"]
        self.tp = cfg["take_profit_pct"]
        self.sl = cfg["stop_loss_pct"]
        self.min_volume = cfg.get("min_volume", 2000.0)
        self.price_history = PriceHistory()
        self._last_cycle = 0

    def update_prices(self, fetcher, cycle: int) -> None:
        """Aggiorna price history con prezzi correnti dei top mercati."""
        self._last_cycle = cycle
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=self.min_volume)
        for m in markets:
            tokens = m["tokens"]
            if len(tokens) < 2:
                continue
            book = fetcher.get_book(tokens[0])
            if not book or book.get("mid") is None:
                continue
            self.price_history.record(m["condition_id"], book["mid"], cycle)
        self.price_history.cleanup_stale(current_cycle=cycle)
        self.price_history.save()

    def scan(self, fetcher) -> List[Opportunity]:
        """Rileva trend e genera opportunità momentum."""
        opps: List[Opportunity] = []
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=self.min_volume)
        for m in markets:
            tokens = m["tokens"]; outcomes = m["outcomes"]
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days > self.max_days or days < self.min_days:
                continue
            # skip sport per fee alta? No — momentum su sport è valido (trend info)
            # ma modelliamo fee nel profitto
            win = self.price_history.get_window(m["condition_id"], self.window)
            if not win or len(win) < 2:
                continue
            first_price = win[0][1]
            last_price = win[-1][1]
            if first_price <= 0 or first_price >= 1:
                continue
            move = (last_price - first_price) / first_price
            # determina lato trending: move>0 → YES, move<0 → NO
            if abs(move) < self.min_move:
                continue
            side_idx = 0 if move > 0 else 1   # YES se salita, NO se discesa
            tok = tokens[side_idx]
            out = outcomes[side_idx] if side_idx < len(outcomes) else ("Yes" if side_idx == 0 else "No")
            book = fetcher.get_book(tok)
            if not book:
                continue
            ask = book.get("best_ask")
            bid = book.get("best_bid")
            if ask is None or ask <= 0 or ask >= 1:
                continue
            spread_c = ((ask - bid) * 100) if bid is not None else 99
            if spread_c > self.max_spread * 0.01 * 100:
                continue
            ask_sz = float(book.get("ask_size") or 0.0)
            if ask_sz < self.min_book:
                continue
            # entry price = ask; target: trend continuation. Non c'è payout $1 fisso
            # qui è una scommessa direzionale. profit_per_share = stima move residuo
            # (conservativo: 50% del move osservato come target)
            est_remaining = abs(move) * 0.5
            fee = _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
            profit_per_share = ask * est_remaining - fee
            if profit_per_share <= 0:
                continue
            max_fill = ask_sz * ask
            if max_fill < BUDGET["min_position_size"]:
                continue
            # score: move strength × liquidity
            score = abs(move) * (ask_sz / 100.0)
            opp = Opportunity(
                strategy=self.name,
                condition_id=m["condition_id"],
                market_title=m["question"],
                event_slug=m["event_slug"],
                category=m["category"],
                end_date=m["end_date"],
                assets=[tok, ""],
                outcomes=[out, ""],
                cost_per_share=ask,
                best_asks=[ask],
                book_sizes=[ask_sz],
                spread_cents=[spread_c],
                payout_per_share=1.0,   # non usato per momentum (direzionale)
                profit_per_share=profit_per_share,
                max_fill_size=max_fill,
                fee_type=m.get("fee_type", ""),
                score=score,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


STRATEGY_REGISTRY = {
    "arb_binary": ArbBinaryStrategy,
    "harvest": HarvestStrategy,
    "arb_cross": ArbCrossStrategy,
    "momentum": MomentumStrategy,
}