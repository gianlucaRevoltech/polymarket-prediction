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

from config import STRATEGIES, BUDGET, POLYMARKET_API, DATA_DIR, SCANNER
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
    # Phase CI2 (Guida 2: liquidity ≥$50K per uscite pulite). Volume totale
    # mercato (gamma volumeNum) — non depth best-level. Usato da execute_opportunity
    # come filtro hard: skip se < min_market_volume_usdc config.
    market_volume: float = 0.0


# ----------------------------------------------------------------------
# Utility fee modellate come in categories.taker_fee_fraction (sport = rate*min(p,1-p))
# ----------------------------------------------------------------------
def _leg_fee_fraction(fee_type: str, price: float) -> float:
    cat = "sport" if (fee_type and "sport" in fee_type.lower()) else "other"
    return taker_fee_fraction(cat, price)


def _min_market_volume_usdc(strategy_name: str) -> float:
    """Phase CI2 (Guida 2: liquidity ≥$50K per uscite pulite)."""
    return float(STRATEGIES.get(strategy_name, {}).get("min_market_volume_usdc", 0.0) or 0.0)


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
        min_market_vol = _min_market_volume_usdc(self.name)
        opps: List[Opportunity] = []
        for m in markets:
            # Phase CI2 (Guida 2: liquidity ≥$50K): skip mercato se volume < soglia.
            # Garantita uscita pulita senza bid-ask che mangia il gain residual.
            if min_market_vol > 0 and m.get("volume", 0) < min_market_vol:
                continue
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
                market_volume=m.get("volume", 0.0),
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
        min_market_vol = _min_market_volume_usdc(self.name)
        opps: List[Opportunity] = []
        for m in markets:
            # Phase CI2 (Guida 2: liquidity ≥$50K): skip market if volume < threshold.
            # Gate hard: harvest ha SL -5 cent + hold-to-resolution; vuole uscite
            # pulite in caso di black-swan reversal, che richiede book profondo.
            if min_market_vol > 0 and m.get("volume", 0) < min_market_vol:
                continue
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
                    market_volume=m.get("volume", 0.0),
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
        min_market_vol = _min_market_volume_usdc(self.name)
        opps: List[Opportunity] = []
        seen_events = set()
        for ev in events[:self.scan_events]:
            slug = ev["slug"]
            if not slug or slug in seen_events:
                continue
            seen_events.add(slug)
            # Phase CI2 (Guida 2: liquidity ≥$50K per uscite pulite su bundle n-leg).
            # get_active_events espone volume aggregato evento in ev['volume']. In
            # alternativa prendiamo come proxy il volume del primo sotto-mercato.
            ev_volume = float(ev.get("volume", 0) or 0)
            markets = fetcher.get_event_markets(slug)
            if not markets:
                continue
            if min_market_vol > 0:
                # se volume evento non popolato, proviamo col max dei sotto-mercati
                if ev_volume <= 0:
                    ev_volume = max((m.get("volume", 0) for m in markets), default=0.0)
                if ev_volume < min_market_vol:
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
                market_volume=ev_volume,
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
        self.windows = cfg.get("windows", [self.window])
        self.min_windows_confirmed = cfg.get("min_windows_confirmed", 1)

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
            # Phase JJ: multi-timeframe — conferma trend su piu finestre
            windows = getattr(self, 'windows', [self.window])
            min_conf = getattr(self, 'min_windows_confirmed', 1)
            directions = []
            for w in windows:
                win = self.price_history.get_window(m["condition_id"], w)
                if not win or len(win) < 2:
                    continue
                fp = win[0][1]; lp = win[-1][1]
                if fp <= 0 or fp >= 1:
                    continue
                mv = (lp - fp) / fp
                if abs(mv) >= self.min_move:
                    directions.append(1 if mv > 0 else -1)
            if len(directions) < min_conf:
                continue
            trend_dir = 1 if sum(directions) > 0 else -1
            side_idx = 0 if trend_dir > 0 else 1   # YES se salita, NO se discesa
            tok = tokens[side_idx]
            out = outcomes[side_idx] if side_idx < len(outcomes) else ("Yes" if side_idx == 0 else "No")
            book = fetcher.get_book(tok)
            if not book:
                continue
            ask = book.get("best_ask")
            bid = book.get("best_bid")
            if ask is None or ask <= 0 or ask >= 1:
                continue
            # Phase CE: entry band — no prezzi estremi dove 5% move = rumore
            _mcfg = STRATEGIES.get("momentum", {})
            ep_min = _mcfg.get("entry_price_min", 0.15)
            ep_max = _mcfg.get("entry_price_max", 0.85)
            if ask < ep_min or ask > ep_max:
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


# ----------------------------------------------------------------------
# Phase BB: Whale strategy — segui movimenti istituzionali (wallet enormi)
# ----------------------------------------------------------------------
class WhaleStrategy:
    """
    Monitora wallet istituzionali (whale) e segue i loro INGRESSI recenti.

    Tesi: le whale muovono i mercati Polymarket. Un ingresso whale (BUY >= $5K)
    in un mercato segnala informazione/conviction istituzionale. Seguendo
    i loro buy recenti catturiamo momentum da SIZE, non correlato con:
      - copy (wallet bravi per WR storico)
      - momentum (trend prezzo)
      - harvest (near-certain resolution)

    Flusso:
      1) discover_whales: scan top mercati per volume -> get_market_holders
         -> filtra holders con amount >= min_whale_position_usdc -> lista whale
      2) scan: per ogni whale, fetch activity recente (last 45min)
         -> filtra BUY con usdcSize >= min_whale_buy_usdc
         -> raggruppa per conditionId (consenso whale)
         -> genera Opportunity per ogni mercato con >= min_whales_consensus
      3) Esecuzione: compra stesso outcome della whale, TP+10%/SL-6%
    """
    name = "whale"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.min_whale_pos = cfg["min_whale_position_usdc"]
        self.max_whales = cfg["max_whales_tracked"]
        self.refresh_interval = cfg["whale_refresh_interval_sec"]
        self.lookback_min = cfg["activity_lookback_min"]
        self.min_consensus = cfg["min_whales_consensus"]
        self.min_buy_usdc = cfg["min_whale_buy_usdc"]
        self.max_days = cfg["max_days_to_expiry"]
        self.min_days = cfg["min_days_to_expiry"]
        self.min_book = cfg["min_book_size"]
        self.max_spread = cfg["max_spread_ticks"]
        self.tp = cfg["take_profit_pct"]
        self.sl = cfg["stop_loss_pct"]
        self.scan_markets = cfg["scan_markets"]
        self.min_volume = cfg.get("min_volume", 5000.0)

        self.whale_file = DATA_DIR / "whale_wallets.json"
        self.whale_addresses: List[str] = []
        self.last_discover: Optional[datetime] = None
        self._load_whales()

    def _load_whales(self):
        try:
            if self.whale_file.exists():
                import json as _j
                with open(self.whale_file) as f:
                    data = _j.load(f)
                self.whale_addresses = data.get("addresses", [])
                ts = data.get("last_discover")
                if ts:
                    self.last_discover = datetime.fromisoformat(ts)
        except Exception:
            self.whale_addresses = []

    def _save_whales(self):
        try:
            import json as _j
            with open(self.whale_file, "w") as f:
                _j.dump({"addresses": self.whale_addresses,
                         "last_discover": self.last_discover.isoformat() if self.last_discover else ""},
                        f, indent=2)
        except Exception:
            pass

    def _should_discover(self) -> bool:
        if not self.whale_addresses:
            return True
        if self.last_discover is None:
            return True
        return (datetime.now() - self.last_discover).total_seconds() >= self.refresh_interval

    def discover_whales(self, fetcher) -> int:
        """Scansiona top mercati, raccoglie holder con posizione >= min_whale_pos.
        Ritorna n whale scoperte."""
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=self.min_volume)
        whale_set = set()
        for m in markets:
            cid = m.get("condition_id")
            if not cid:
                continue
            holders = self._get_holders(fetcher, cid)
            for h in holders:
                # amount = shares; valore ~= amount * price (~0.5 mid). Approssimazione
                # conservativa: usa amount come proxy (shares). Per essere sicuri
                # consideriamo amount >= min_whale_pos (shares; a prezzo 0.5 = $12.5K value)
                amt = h.get("amount", 0)
                if amt >= self.min_whale_pos:
                    addr = h.get("address", "").lower()
                    if addr:
                        whale_set.add(addr)
            if len(whale_set) >= self.max_whales * 2:
                break
        self.whale_addresses = list(whale_set)[:self.max_whales]
        self.last_discover = datetime.now()
        self._save_whales()
        print(f"  [WHALE] Scoperte {len(self.whale_addresses)} whale (pos >= {self.min_whale_pos/1000:.0f}K shares)")
        return len(self.whale_addresses)

    def _get_holders(self, fetcher, condition_id: str) -> list:
        """Wrapper get_market_holders via data-api."""
        try:
            url = f"{fetcher.data_api}/holders"
            r = fetcher.session.get(url, params={"market": condition_id, "limit": 50}, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception:
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
                    "amount": float(h.get("amount", 0) or 0),
                    "outcome_index": h.get("outcomeIndex", -1),
                })
        return holders

    def _fetch_whale_activity(self, fetcher, address: str, limit: int = 50) -> list:
        """Activity recente di una whale via data-api."""
        try:
            url = f"{fetcher.data_api}/activity"
            r = fetcher.session.get(url, params={"user": address, "limit": limit}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception:
            return []

    def scan(self, fetcher) -> List[Opportunity]:
        """Rileva BUY whale recenti e genera opportunita per consenso."""
        # 1) discover se necessario
        if self._should_discover():
            self.discover_whales(fetcher)
        if not self.whale_addresses:
            return []

        # 2) activity recente di ogni whale, filtra BUY >= min_buy_usdc in lookback
        now_ts = datetime.now().timestamp()
        cutoff = now_ts - self.lookback_min * 60
        # conditionId -> {whales: set, buys: [...], title, slug, asset, outcome, price}
        signals: Dict[str, dict] = {}
        for addr in self.whale_addresses:
            acts = self._fetch_whale_activity(fetcher, addr, limit=50)
            for a in acts:
                if a.get("type") != "TRADE":
                    continue
                if a.get("side", "").upper() != "BUY":
                    continue
                try:
                    ts = int(a.get("timestamp", 0))
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                usdc = float(a.get("usdcSize", 0) or 0)
                if usdc < self.min_buy_usdc:
                    continue
                cid = a.get("conditionId", "")
                if not cid:
                    continue
                asset = a.get("asset", "")
                outcome = a.get("outcome", "")
                price = float(a.get("price", 0) or 0)
                title = a.get("title", "")
                slug = a.get("slug", "")
                sig = signals.setdefault(cid, {"whales": set(), "buys": [], "title": title,
                                                "slug": slug, "asset": asset, "outcome": outcome,
                                                "price": price})
                sig["whales"].add(addr.lower())
                sig["buys"].append({"addr": addr, "outcome": outcome, "price": price,
                                    "usdc": usdc, "asset": asset})
            import time as _t
            _t.sleep(0.1)  # rate limit gentile tra whale

        # 3) filtra per consenso >= min_whales_consensus, genera Opportunity
        opps: List[Opportunity] = []
        for cid, sig in signals.items():
            n_whales = len(sig["whales"])
            if n_whales < self.min_consensus:
                continue
            asset = sig["asset"]
            if not asset:
                continue
            # recupera info mercato per end_date + tokens + filtri
            m = fetcher.get_market(cid)
            if not m or len(m.get("tokens", [])) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days > self.max_days or days < self.min_days:
                continue
            # book per liquidita + entry price
            book = fetcher.get_book(asset)
            if not book:
                continue
            ask = book.get("best_ask")
            bid = book.get("best_bid")
            if ask is None or ask <= 0 or ask >= 1:
                continue
            # Phase CE: entry band — no prezzi estremi dove SL e' rumore-trigger
            _wcfg = STRATEGIES.get("whale", {})
            ep_min = _wcfg.get("entry_price_min", 0.15)
            ep_max = _wcfg.get("entry_price_max", 0.85)
            if ask < ep_min or ask > ep_max:
                continue
            spread_c = ((ask - bid) * 100) if bid is not None else 99
            if spread_c > self.max_spread * 0.01 * 100:
                continue
            ask_sz = float(book.get("ask_size") or 0.0)
            if ask_sz < self.min_book:
                continue
            max_fill = ask_sz * ask
            if max_fill < BUDGET["min_position_size"]:
                continue
            # score: n_whales * total_usdc_buy (conviction istituzionale)
            total_usdc = sum(b["usdc"] for b in sig["buys"])
            score = n_whales * (total_usdc / 1000.0)
            # profit_per_share stima: whale move -> +TP target (conservativo 50% TP)
            fee = _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
            profit_per_share = ask * (self.tp * 0.5) - fee
            out_label = sig["outcome"] or (m["outcomes"][0] if m["outcomes"] else "Yes")
            opp = Opportunity(
                strategy=self.name,
                condition_id=cid,
                market_title=sig["title"] or m["question"],
                event_slug=sig["slug"] or m.get("event_slug", ""),
                category=m["category"],
                end_date=m["end_date"],
                assets=[asset, ""],
                outcomes=[out_label, ""],
                cost_per_share=ask,
                best_asks=[ask],
                book_sizes=[ask_sz],
                spread_cents=[spread_c],
                payout_per_share=1.0,
                profit_per_share=profit_per_share,
                max_fill_size=max_fill,
                fee_type=m.get("fee_type", ""),
                score=score,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Phase DD: Sniper Harvest — risoluzione imminente <24h, APR astronomica
# ----------------------------------------------------------------------
class SniperStrategy:
    """Sub-strategia harvest per mercati con endTime < 24h.
    Compra lato vincente 0.85-0.97 → payout $1 a risoluzione (12-24h). APR >1000%."""
    name = "sniper"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.fav_min = cfg["fav_min"]
        self.fav_max = cfg["fav_max"]
        self.min_hours = cfg["min_hours_to_expiry"]
        self.max_hours = cfg["max_hours_to_expiry"]
        self.min_book = cfg["min_book_size"]
        self.max_spread = cfg["max_spread_ticks"]
        self.scan_markets = cfg["scan_markets"]
        self.min_volume = cfg.get("min_volume", 1000.0)
        self.skip_politics = cfg.get("skip_politics", True)
        self.tp = cfg.get("take_profit_pct", 0.05)
        self.sl = cfg.get("stop_loss_pct", -0.05)

    def scan(self, fetcher) -> List[Opportunity]:
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=self.min_volume)
        opps: List[Opportunity] = []
        for m in markets:
            tokens = m["tokens"]; outcomes = m["outcomes"]
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None:
                continue
            hours = days * 24
            if hours < self.min_hours or hours > self.max_hours:
                continue
            if self.skip_politics and m["category"] == "politics":
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
                if spread_c > self.max_spread * 0.01 * 100:
                    continue
                ask_sz = float(book.get("ask_size") or 0.0)
                if ask_sz < self.min_book:
                    continue
                profit_per_share = 1.0 - ask - _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
                if profit_per_share <= 0:
                    continue
                max_fill = ask_sz * ask
                if max_fill < BUDGET["min_position_size"]:
                    continue
                apr = (profit_per_share / ask) / max(hours, 0.1) * 365 * 24
                opp = Opportunity(
                    strategy=self.name, condition_id=m["condition_id"],
                    market_title=m["question"], event_slug=m["event_slug"],
                    category=m["category"], end_date=m["end_date"],
                    assets=[tok, ""], outcomes=[out, ""],
                    cost_per_share=ask, best_asks=[ask], book_sizes=[ask_sz],
                    spread_cents=[spread_c], payout_per_share=1.0,
                    profit_per_share=profit_per_share, max_fill_size=max_fill,
                    fee_type=m.get("fee_type", ""), score=apr,
                )
                opps.append(opp)
                break
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Phase GG: Theta / time-decay — "Will X by [date]" dove X NON successo
# ----------------------------------------------------------------------
class ThetaStrategy:
    """Mercati con domanda "Will X by <date>" dove X NON e' successo.
    Il "No" drifta verso $1 man mano che il tempo passa (theta decay)."""
    name = "theta"
    THETA_KEYWORDS = ["by ", "before ", "until ", "this month", "this week",
                      "by july", "by august", "by end", "in july", "in august"]

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.no_min = cfg["no_price_min"]
        self.no_max = cfg["no_price_max"]
        self.min_days = cfg["min_days_to_expiry"]
        self.max_days = cfg["max_days_to_expiry"]
        self.min_book = cfg["min_book_size"]
        self.max_spread = cfg["max_spread_ticks"]
        self.scan_markets = cfg["scan_markets"]
        self.min_volume = cfg.get("min_volume", 2000.0)
        self.tp = cfg.get("take_profit_pct", 0.08)
        self.sl = cfg.get("stop_loss_pct", -0.06)

    def _is_theta_market(self, title: str) -> bool:
        t = (title or "").lower()
        return any(kw in t for kw in self.THETA_KEYWORDS)

    def scan(self, fetcher) -> List[Opportunity]:
        markets = fetcher.get_active_markets(limit=self.scan_markets, min_volume=self.min_volume)
        opps: List[Opportunity] = []
        for m in markets:
            if not self._is_theta_market(m["question"]):
                continue
            tokens = m["tokens"]; outcomes = m["outcomes"]
            if len(tokens) < 2 or len(outcomes) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days < self.min_days or days > self.max_days:
                continue
            no_idx = 1 if (len(outcomes) > 1 and "no" in outcomes[1].lower()) else 0
            tok = tokens[no_idx]
            out = outcomes[no_idx] if no_idx < len(outcomes) else "No"
            book = fetcher.get_book(tok)
            if not book:
                continue
            ask = book.get("best_ask")
            if ask is None or ask < self.no_min or ask > self.no_max:
                continue
            bid = book.get("best_bid")
            if bid is None:
                continue
            spread_c = (ask - bid) * 100
            if spread_c > self.max_spread * 0.01 * 100:
                continue
            ask_sz = float(book.get("ask_size") or 0.0)
            if ask_sz < self.min_book:
                continue
            profit_per_share = 1.0 - ask - _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
            if profit_per_share <= 0:
                continue
            max_fill = ask_sz * ask
            if max_fill < BUDGET["min_position_size"]:
                continue
            theta_rate = profit_per_share / max(days, 0.1)
            opp = Opportunity(
                strategy=self.name, condition_id=m["condition_id"],
                market_title=m["question"], event_slug=m["event_slug"],
                category=m["category"], end_date=m["end_date"],
                assets=[tok, ""], outcomes=[out, ""],
                cost_per_share=ask, best_asks=[ask], book_sizes=[ask_sz],
                spread_cents=[spread_c], payout_per_share=1.0,
                profit_per_share=profit_per_share, max_fill_size=max_fill,
                fee_type=m.get("fee_type", ""), score=theta_rate,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


# ----------------------------------------------------------------------
# Phase II: Contrarian / fade extreme — whale VENDONO mercato estremo 0.93+
# ----------------------------------------------------------------------
class ContrarianStrategy:
    """Rileva whale SELL su mercati a estremo (>= 0.93). Fade: compra lato opposto."""
    name = "contrarian"

    def __init__(self):
        cfg = STRATEGIES[self.name]
        self.cap_pct = cfg["cap_pct"]
        self.max_single = cfg["max_single"]
        self.extreme_min = cfg["extreme_min"]
        self.extreme_max = cfg["extreme_max"]
        self.min_sell_usdc = cfg["min_whale_sell_usdc"]
        self.lookback = cfg["activity_lookback_min"]
        self.min_days = cfg["min_days_to_expiry"]
        self.max_days = cfg["max_days_to_expiry"]
        self.min_book = cfg["min_book_size"]
        self.max_spread = cfg["max_spread_ticks"]
        self.scan_markets = cfg["scan_markets"]
        self.min_volume = cfg.get("min_volume", 5000.0)
        self.tp = cfg.get("take_profit_pct", 0.15)
        self.sl = cfg.get("stop_loss_pct", -0.04)

    def scan(self, fetcher) -> List[Opportunity]:
        whale_file = DATA_DIR / "whale_wallets.json"
        whale_addrs = []
        try:
            if whale_file.exists():
                import json as _j
                with open(whale_file) as f:
                    whale_addrs = _j.load(f).get("addresses", [])
        except Exception:
            pass
        if not whale_addrs:
            return []
        now_ts = datetime.now().timestamp()
        cutoff = now_ts - self.lookback * 60
        signals: Dict[str, dict] = {}
        for addr in whale_addrs:
            try:
                url = f"{POLYMARKET_API['data']}/activity"
                r = fetcher.session.get(url, params={"user": addr, "limit": 50}, timeout=15)
                if not r.ok:
                    continue
                for a in r.json():
                    if a.get("type") != "TRADE" or a.get("side", "").upper() != "SELL":
                        continue
                    try:
                        ts = int(a.get("timestamp", 0))
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    usdc = float(a.get("usdcSize", 0) or 0)
                    if usdc < self.min_sell_usdc:
                        continue
                    price = float(a.get("price", 0) or 0)
                    if price < self.extreme_min or price > self.extreme_max:
                        continue
                    cid = a.get("conditionId", "")
                    if not cid:
                        continue
                    sig = signals.setdefault(cid, {"sells": [], "title": a.get("title", ""),
                                                    "slug": a.get("slug", ""),
                                                    "asset": a.get("asset", ""),
                                                    "outcome": a.get("outcome", ""),
                                                    "price": price})
                    sig["sells"].append({"addr": addr, "usdc": usdc, "price": price,
                                         "asset": a.get("asset", ""), "outcome": a.get("outcome", "")})
            except Exception:
                continue
            import time as _t
            _t.sleep(0.1)
        opps: List[Opportunity] = []
        for cid, sig in signals.items():
            m = fetcher.get_market(cid)
            if not m or len(m.get("tokens", [])) < 2:
                continue
            days = fetcher.days_to_expiry(m["end_date"])
            if days is None or days < self.min_days or days > self.max_days:
                continue
            whale_outcome = (sig["outcome"] or "").lower()
            fade_idx = 1 if "yes" in whale_outcome else 0
            tok = m["tokens"][fade_idx]
            out = m["outcomes"][fade_idx] if fade_idx < len(m["outcomes"]) else "No"
            book = fetcher.get_book(tok)
            if not book:
                continue
            ask = book.get("best_ask")
            if ask is None or ask <= 0 or ask >= 1:
                continue
            # Phase CE: entry band per il FADE — no longshot a 0.02 dove SL = 1 tick
            _ccfg = STRATEGIES.get("contrarian", {})
            ep_min = _ccfg.get("entry_price_min", 0.10)
            ep_max = _ccfg.get("entry_price_max", 0.90)
            if ask < ep_min or ask > ep_max:
                continue
            bid = book.get("best_bid")
            spread_c = ((ask - bid) * 100) if bid else 99
            if spread_c > self.max_spread * 0.01 * 100:
                continue
            ask_sz = float(book.get("ask_size") or 0.0)
            if ask_sz < self.min_book:
                continue
            max_fill = ask_sz * ask
            if max_fill < BUDGET["min_position_size"]:
                continue
            total_sell = sum(s["usdc"] for s in sig["sells"])
            score = len(sig["sells"]) * (total_sell / 1000.0)
            fee = _leg_fee_fraction(m.get("fee_type", ""), ask) * ask
            profit_per_share = ask * (self.tp * 0.5) - fee
            opp = Opportunity(
                strategy=self.name, condition_id=cid,
                market_title=sig["title"] or m["question"],
                event_slug=sig["slug"] or m.get("event_slug", ""),
                category=m["category"], end_date=m["end_date"],
                assets=[tok, ""], outcomes=[out, ""],
                cost_per_share=ask, best_asks=[ask], book_sizes=[ask_sz],
                spread_cents=[spread_c], payout_per_share=1.0,
                profit_per_share=profit_per_share, max_fill_size=max_fill,
                fee_type=m.get("fee_type", ""), score=score,
            )
            opps.append(opp)
        opps.sort(key=lambda o: o.score, reverse=True)
        return opps


STRATEGY_REGISTRY = {
    "arb_binary": ArbBinaryStrategy,
    "harvest": HarvestStrategy,
    "arb_cross": ArbCrossStrategy,
    "momentum": MomentumStrategy,
    "whale": WhaleStrategy,
    "sniper": SniperStrategy,
    "theta": ThetaStrategy,
    "contrarian": ContrarianStrategy,
}