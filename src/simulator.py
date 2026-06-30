"""
Simulator per paper trading Polymarket.

Architettura a "mirroring di portafoglio": invece di reagire ai singoli trade,
ad ogni ciclo confrontiamo lo snapshot delle posizioni dei wallet monitorati con
il nostro portafoglio simulato e:
  - apriamo nuove posizioni (secondo la strategia copy/consenso),
  - aggiorniamo il prezzo corrente delle posizioni aperte (PnL reale),
  - chiudiamo quando il wallet sorgente esce,
  - realizziamo il PnL quando il mercato si risolve.
"""
import json
import uuid
import os
import tempfile
import shutil
from datetime import datetime
from typing import Dict, Optional, List, Set
from models import Trade, Position, Portfolio
from categories import categorize_market, taker_fee_fraction
from config import BUDGET, FEES, SIMULATOR, STRATEGY, DATA_DIR


class PaperTradingSimulator:
    """Simula trading reale con budget virtuale tramite mirroring delle posizioni."""

    def __init__(self, initial_capital: float = BUDGET["initial_capital"]):
        self.portfolio = Portfolio(
            initial_capital=initial_capital,
            cash=initial_capital,
            positions={},
            closed_positions=[],
            trades=[]
        )

        self.state_file = DATA_DIR / "portfolio_state.json"
        self.trades_log = DATA_DIR / "trades_log.json"
        self.equity_file = DATA_DIR / "equity_curve.json"

        # Asset gia detenuti dai wallet all'avvio: registrati come baseline e NON
        # copiati (evita ingressi tardivi su posizioni vecchie a prezzo "live").
        self.baseline_assets: Set[str] = set()
        self.baseline_done: bool = False
        self.strategy_mode: str = STRATEGY["mode"]

        self._load_state()
        self._cleanup_legacy_positions()
        self.wallet_quality: Dict[str, Dict] = {}
        self._load_wallet_quality()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get_open_assets(self) -> Dict[str, Position]:
        """Mappa asset -> Position per le posizioni aperte (con asset valorizzato)."""
        return {p.asset: p for p in self.portfolio.positions.values() if p.asset}

    def has_asset(self, asset: str) -> bool:
        return any(p.asset == asset for p in self.portfolio.positions.values())

    def _cleanup_legacy_positions(self):
        """
        Bonifica posizioni salvate col vecchio formato (senza `asset`/token id):
        il motore di mirroring non saprebbe agganciarle ai dati live, quindi le
        chiudiamo in modo neutro al loro ultimo prezzo noto. Operazione una-tantum.
        """
        legacy = [p for p in self.portfolio.positions.values() if not p.asset]
        if not legacy:
            return
        print(f"[CLEANUP] Rimuovo {len(legacy)} posizione/i legacy senza asset id "
              f"(dati di test, rimborso cash, nessun impatto sulle statistiche)")
        for pos in legacy:
            # Rimborsa il capitale impegnato e scarta la posizione contaminata
            self.portfolio.cash += pos.size_usdc
            del self.portfolio.positions[pos.position_id]
        self._save_state()

    # ------------------------------------------------------------------
    # Phase B: qualita wallet (soft-disable, non rimozione)
    # ------------------------------------------------------------------
    def _load_wallet_quality(self):
        """Carica win_rate/decided per indirizzo da data/scan_results.json."""
        scan_file = DATA_DIR / "scan_results.json"
        if not scan_file.exists():
            return
        try:
            with open(scan_file) as f:
                data = json.load(f)
            for w in data.get("wallets", []):
                addr = (w.get("address") or "").lower()
                if addr:
                    self.wallet_quality[addr] = {
                        "win_rate": float(w.get("win_rate", 0.0) or 0.0),
                        "decided": int(w.get("decided_positions", 0) or 0),
                        "name": w.get("name", ""),
                    }
        except Exception as e:
            print(f"[SIMULATOR] load wallet_quality fallito: {e}")

    def _wallet_size_factor(self, source_wallet: str) -> float:
        """Phase B: size factor (1.0 o soft_disable_size_factor) per wallet."""
        q = self.wallet_quality.get((source_wallet or "").lower())
        if not q:
            return 1.0  # wallet non in scan: non penalizzare (sconosciuto)
        thr = STRATEGY.get("soft_disable_wr_threshold", 0.55)
        if q["win_rate"] < thr:
            return STRATEGY.get("soft_disable_size_factor", 0.5)
        return 1.0

    def _positions_for_wallet(self, source_wallet: str) -> int:
        return sum(
            1 for p in self.portfolio.positions.values()
            if (p.source_wallet or "").lower() == (source_wallet or "").lower()
        )

    def _positions_for_category(self, category: str) -> int:
        return sum(
            1 for p in self.portfolio.positions.values()
            if (p.category or "") == category
        )

    def _find_position_by_asset(self, asset: str):
        for pid, p in self.portfolio.positions.items():
            if p.asset == asset:
                return pid, p
        return None, None

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def _calculate_position_size(self, target_wallet_size: float) -> float:
        """
        Calcola la size della posizione in base al budget e al notional del wallet target.
        Strategia: percentuale del portafoglio scalata sulla size del wallet target,
        limitata da max_position_size e min_position_size.
        """
        max_size = self.portfolio.total_value * BUDGET["max_position_size"]

        if target_wallet_size < 1000:
            size = self.portfolio.total_value * BUDGET["max_position_size"]
        elif target_wallet_size < 10000:
            size = self.portfolio.total_value * (BUDGET["max_position_size"] * 0.8)
        elif target_wallet_size < 100000:
            size = self.portfolio.total_value * (BUDGET["max_position_size"] * 0.6)
        else:
            size = self.portfolio.total_value * (BUDGET["max_position_size"] * 0.4)

        size = min(size, max_size)
        size = max(size, BUDGET["min_position_size"])
        return size

    def _available_cash(self) -> float:
        """Cash spendibile mantenendo la riserva."""
        reserve = self.portfolio.initial_capital * BUDGET["reserve_ratio"]
        return self.portfolio.cash - reserve

    # ------------------------------------------------------------------
    # Apertura posizioni
    # ------------------------------------------------------------------
    def open_position(self, source_wallet: str, info: Dict, num_holders: int = 1,
                      fetcher=None) -> bool:
        """
        Apre una posizione simulata da uno snapshot di wallet.

        Phase D: filtra per scadenza (max_days_to_expiry) e liquidita (book size/spread).
        Phase E: caps per wallet sorgente e per categoria (anti-correlazione).
        Phase B: soft-disable (size dimezzata) per wallet con win-rate basso.
        Phase C: chiamata solo per asset NUOVI (delta-snapshot), vedi reconcile.
        """
        asset = info["asset"]
        price = info["cur_price"]

        if not asset or self.has_asset(asset):
            return False

        if price <= 0 or price >= 1:
            return False

        # Guardrail 1 - banda di prezzo (Phase D): 0.30-0.70
        price_min = STRATEGY.get("entry_price_min", 0.0)
        price_max = STRATEGY.get("entry_price_max", 1.0)
        if price < price_min or price > price_max:
            print(f"[SKIP] Prezzo {price:.3f} fuori banda [{price_min:.2f},{price_max:.2f}]: "
                  f"{info['title'][:45]}")
            return False

        # Guardrail 2 - anti entrata tardiva (Phase C: drift 5%)
        avg_price = info.get("avg_price", 0.0)
        max_drift = STRATEGY.get("max_entry_drift", 1.0)
        if avg_price > 0 and price > avg_price * (1 + max_drift):
            print(f"[SKIP] Entrata tardiva: prezzo {price:.3f} > avg wallet {avg_price:.3f} "
                  f"+{max_drift:.0%}: {info['title'][:40]}")
            return False

        # Phase D: filtro scadenza (no capital-lock > 60gg tipo 2028 elections,
        # nè coin-flip 5-min crypto < 24h: scartiamo mercati troppo brevi
        # dove i wallet fanno market-making con rebate NON copiabile dal retail).
        max_days = STRATEGY.get("max_days_to_expiry")
        min_days = STRATEGY.get("min_days_to_expiry", 0.0)
        if max_days is not None or min_days > 0:
            end_iso = info.get("end_date_iso") or info.get("end_date", "")
            if end_iso:
                days = None
                if fetcher is not None:
                    days = fetcher.days_to_expiry(end_iso)
                if days is not None:
                    if max_days is not None and days > max_days:
                        print(f"[SKIP] Scadenza {days:.0f}gg > {max_days}gg: "
                              f"{info['title'][:40]}")
                        return False
                    if min_days > 0 and days < min_days:
                        print(f"[SKIP] Scadenza {days:.1f}gg < {min_days}gg (coin-flip/MM): "
                              f"{info['title'][:40]}")
                        return False

        # Phase D: filtro liquidita (book size + spread)
        if fetcher is not None and STRATEGY.get("min_book_size_usdc"):
            book = fetcher.get_book(asset)
            ok = fetcher.passes_liquidity(
                book, side_size_min=STRATEGY["min_book_size_usdc"],
                max_spread_ticks=STRATEGY.get("max_spread_ticks", 3))
            if not ok:
                print(f"[SKIP] Liquidita insufficiente: {info['title'][:40]}")
                return False

        if self.portfolio.open_positions_count >= BUDGET["max_open_positions"]:
            return False

        # Phase E: cap per wallet sorgente (max 1 posizione aperta per wallet)
        max_per_wallet = BUDGET.get("max_positions_per_wallet", 1)
        if self._positions_for_wallet(source_wallet) >= max_per_wallet:
            print(f"[SKIP] Cap wallet raggiunto ({max_per_wallet}) per "
                  f"{source_wallet[:10]}: {info['title'][:40]}")
            return False

        # Phase E: cap per categoria (anti-correlazione, es. 2 bet politica 2028)
        max_per_cat = BUDGET.get("max_positions_per_category", 99)
        category = info.get("category") or categorize_market(
            info["title"], event_slug=info.get("slug", ""))
        if max_per_cat < 99 and self._positions_for_category(category) >= max_per_cat:
            print(f"[SKIP] Cap categoria '{category}' raggiunto ({max_per_cat}): "
                  f"{info['title'][:40]}")
            return False

        size = self._calculate_position_size(info.get("notional_usdc", 0.0))

        # Phase B: soft-disable wallet win-rate basso (NON rimosso, size dimezzata)
        factor = self._wallet_size_factor(source_wallet)
        if factor < 1.0:
            size *= factor
            print(f"[SOFT-DISABLE] wallet {source_wallet[:10]} WR basso: "
                  f"size x{factor:.2f} -> ${size:.2f}")

        if size < BUDGET["min_position_size"]:
            return False

        if size > self._available_cash():
            print(f"[SIMULATOR] Cash insufficiente (riserva): "
                  f"${self._available_cash():.2f} < ${size:.2f}")
            return False

        # Categoria (per fee) e costo d'ingresso: slippage + taker fee per categoria
        category = info.get("category") or categorize_market(info["title"], event_slug=info.get("slug", ""))
        slippage = SIMULATOR["entry_slippage"]
        eff_price = min(0.999, price * (1 + slippage))
        fee_frac = taker_fee_fraction(category, eff_price)
        # Prezzo effettivo pagato includendo la fee taker (sport ~ rate*min(p,1-p))
        eff_price_with_fee = min(0.999, eff_price * (1 + fee_frac))
        shares = size / eff_price_with_fee

        position_id = str(uuid.uuid4())
        position = Position(
            position_id=position_id,
            market_title=info["title"],
            market_slug=info["slug"],
            condition_id=info["condition_id"],
            outcome=info["outcome"],
            entry_price=eff_price_with_fee,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet=source_wallet,
            asset=asset,
            category=category,
            current_price=price,
        )

        self.portfolio.add_position(position)
        self._log_trade(source_wallet, position, num_holders)

        print(f"\n[POSIZIONE APERTA] ({self.strategy_mode}, holders={num_holders}, cat={category})")
        print(f"  Mercato: {info['title'][:50]}")
        print(f"  Outcome: {info['outcome']}")
        print(f"  Size: ${size:.2f} | Pagato: ${eff_price_with_fee:.3f} "
              f"(mkt ${price:.3f}, slip+fee {((eff_price_with_fee/price)-1)*100:.1f}%)")
        print(f"  Shares: {shares:.2f}")
        print(f"  Cash rimanente: ${self.portfolio.cash:.2f} | "
              f"Posizioni: {self.portfolio.open_positions_count}/{BUDGET['max_open_positions']}")

        self._save_state()
        return True

    # ------------------------------------------------------------------
    # Aggiornamento prezzi e chiusure
    # ------------------------------------------------------------------
    def update_price_by_asset(self, asset: str, price: float):
        for p in self.portfolio.positions.values():
            if p.asset == asset:
                p.current_price = price

    def close_by_asset(self, asset: str, exit_price: float, reason: str) -> bool:
        """Chiude la posizione associata a un asset al prezzo dato."""
        pid, pos = self._find_position_by_asset(asset)
        if pos is None:
            return False

        exit_price = max(0.0, min(1.0, exit_price))
        pos.current_price = exit_price
        pos.close_reason = reason
        pnl = (exit_price - pos.entry_price) * pos.shares

        self.portfolio.close_position(pid, exit_price, datetime.now())

        label = {
            "resolved": "RISOLTA",
            "exit": "CHIUSA (wallet uscito)",
            "stop_loss": "STOP LOSS",
            "take_profit": "TAKE PROFIT",
        }.get(reason, f"CHIUSA ({reason})")
        outcome_label = "PROFIT" if pnl > 0 else "LOSS"
        print(f"\n[POSIZIONE {label}] {outcome_label}")
        print(f"  Mercato: {pos.market_title[:50]} ({pos.outcome})")
        print(f"  Entry: ${pos.entry_price:.3f} -> Exit: ${exit_price:.3f}")
        print(f"  P&L: ${pnl:.2f} | Cash: ${self.portfolio.cash:.2f}")

        self._save_state()
        return True

    # ------------------------------------------------------------------
    # Riconciliazione: cuore del mirroring
    # ------------------------------------------------------------------
    def reconcile(self, aggregate: Dict[str, Dict], min_wallets: int, fetcher,
                  new_assets: Optional[set] = None) -> None:
        """
        Allinea il portafoglio simulato allo snapshot dei wallet.

        Phase C: copy-trade PUNTOLE via delta-snapshot. Si aprono SOLO gli asset
        in `new_assets` (comparsi dall'ultimo ciclo), non il bag intero.

        Args:
            aggregate: asset -> {"info", "holders", "max_notional"}
            min_wallets: soglia di consenso (1 = copy puro)
            fetcher: PolymarketPositionFetcher per fallback + filtri D
            new_assets: set di asset NUOVI rilevati dal main loop (delta). Se e'
                None e delta_copy e attivo, NON si apre nulla (safety).
        """
        # Asset che soddisfano la strategia (consenso)
        qualifying = {a for a, e in aggregate.items() if len(e["holders"]) >= min_wallets}

        # Gestione baseline (posizioni preesistenti non copiate)
        if STRATEGY.get("prime_baseline_on_start", True) and not self.baseline_done:
            self.baseline_assets = set(qualifying)
            self.baseline_done = True
            print(f"[BASELINE] Registrate {len(self.baseline_assets)} posizioni "
                  f"preesistenti (non copiate)")
        else:
            # Tieni in baseline solo gli asset ancora qualificanti: se un asset esce e
            # rientra, verra' trattato come ingresso NUOVO.
            self.baseline_assets &= qualifying

        # 1) Gestisci le posizioni gia aperte: realizza / chiudi / aggiorna prezzo
        stop_loss = BUDGET.get("stop_loss_pct", -0.30)
        take_profit = BUDGET.get("take_profit_pct", 0.50)

        for asset, pos in list(self.get_open_assets().items()):
            entry = aggregate.get(asset)
            if entry is not None:
                info = entry["info"]
                cur = info["cur_price"]
                if info["redeemable"] or cur <= 0.0 or cur >= 1.0:
                    resolved_price = 1.0 if cur >= 0.5 else 0.0
                    self.close_by_asset(asset, resolved_price, "resolved")
                elif asset not in qualifying:
                    self.close_by_asset(asset, cur, "exit")
                else:
                    self.update_price_by_asset(asset, cur)
                    pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                    if pnl_pct <= stop_loss:
                        print(f"[STOP LOSS] {pos.market_title[:40]} P&L {pnl_pct:.1%} <= {stop_loss:.0%}")
                        self.close_by_asset(asset, cur, "stop_loss")
                    elif pnl_pct >= take_profit:
                        print(f"[TAKE PROFIT] {pos.market_title[:40]} P&L {pnl_pct:.1%} >= {take_profit:.0%}")
                        self.close_by_asset(asset, cur, "take_profit")
            else:
                # Nessun wallet monitorato detiene piu l'asset: prezzo dal CLOB
                price = fetcher.get_price(asset)
                if price is None:
                    # Nessun orderbook: probabile risoluzione. Usa l'ultimo prezzo noto;
                    # se vicino agli estremi consideralo risolto.
                    last = pos.current_price
                    if last <= 0.05 or last >= 0.95:
                        self.close_by_asset(asset, 1.0 if last >= 0.5 else 0.0, "resolved")
                    else:
                        # Manteniamo la posizione: prezzo ignoto, riproveremo al prossimo ciclo
                        continue
                elif price <= 0.0 or price >= 1.0:
                    self.close_by_asset(asset, 1.0 if price >= 0.5 else 0.0, "resolved")
                else:
                    self.close_by_asset(asset, price, "exit")

        # 2) Apri nuove posizioni target (escludendo baseline e gia possedute).
        # Ordina per priorita: piu consenso (holders) prima, poi ingressi piu
        # "freschi" (prezzo corrente vicino al prezzo medio del wallet), cosi con
        # piu candidati che cash riempiamo il portafoglio con i segnali migliori.
        def _candidate_key(asset: str):
            entry = aggregate[asset]
            info = entry["info"]
            avg = info.get("avg_price", 0.0)
            cur = info.get("cur_price", 0.0)
            drift = (cur / avg - 1) if avg > 0 else 0.0
            return (-len(entry["holders"]), drift)

        # Phase C:候选 = qualifying intersecato con gli asset NUOVI (delta).
        # Se delta_copy attivo e new_assets non passato, NON aprire (safety):
        # evita di riversare il bag intero se il main non sta tracciando il delta.
        delta_on = STRATEGY.get("delta_copy", False)
        if delta_on and new_assets is None:
            candidates = []
        elif delta_on and new_assets is not None:
            candidates = [
                a for a in qualifying
                if a in new_assets
                and a not in self.baseline_assets
                and not self.has_asset(a)
            ]
        else:
            # Fallback legacy (mirror intero, disattivato di default)
            candidates = [
                a for a in qualifying
                if a not in self.baseline_assets and not self.has_asset(a)
            ]
        for asset in sorted(candidates, key=_candidate_key):
            entry = aggregate[asset]
            source = sorted(entry["holders"])[0]
            self.open_position(source, entry["info"], num_holders=len(entry["holders"]),
                              fetcher=fetcher)

        # 3) Registra equity e salva
        self.record_equity()
        self._save_state()

    # ------------------------------------------------------------------
    # Summary / metriche
    # ------------------------------------------------------------------
    def get_portfolio_summary(self) -> Dict:
        unrealized_pnl = sum(pos.pnl for pos in self.portfolio.positions.values())
        realized_pnl = sum(pos.pnl for pos in self.portfolio.closed_positions)

        winning_trades = sum(1 for pos in self.portfolio.closed_positions if pos.pnl > 0)
        losing_trades = sum(1 for pos in self.portfolio.closed_positions if pos.pnl <= 0)
        total_closed = len(self.portfolio.closed_positions)
        win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0

        return {
            "strategy_mode": self.strategy_mode,
            "initial_capital": self.portfolio.initial_capital,
            "current_value": self.portfolio.total_value,
            "cash": self.portfolio.cash,
            "total_pnl": self.portfolio.total_pnl,
            "total_pnl_pct": self.portfolio.total_pnl_pct,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "open_positions": self.portfolio.open_positions_count,
            "closed_positions": total_closed,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": win_rate,
            "best_trade": self._get_best_trade(),
            "worst_trade": self._get_worst_trade()
        }

    def _get_best_trade(self) -> Optional[Dict]:
        if not self.portfolio.closed_positions:
            return None
        best = max(self.portfolio.closed_positions, key=lambda p: p.pnl)
        return {"market": best.market_title, "pnl": best.pnl, "pnl_pct": best.pnl_pct}

    def _get_worst_trade(self) -> Optional[Dict]:
        if not self.portfolio.closed_positions:
            return None
        worst = min(self.portfolio.closed_positions, key=lambda p: p.pnl)
        return {"market": worst.market_title, "pnl": worst.pnl, "pnl_pct": worst.pnl_pct}

    def print_portfolio_summary(self):
        summary = self.get_portfolio_summary()

        print(f"\n{'='*60}")
        print(f"PORTFOLIO SUMMARY  [strategia: {summary['strategy_mode']}]")
        print(f"{'='*60}")
        print(f"\nCAPITALE:")
        print(f"  Iniziale: ${summary['initial_capital']:.2f}")
        print(f"  Attuale: ${summary['current_value']:.2f}")
        print(f"  Cash: ${summary['cash']:.2f}")
        print(f"\nPERFORMANCE:")
        print(f"  P&L Totale: ${summary['total_pnl']:.2f} ({summary['total_pnl_pct']:.2f}%)")
        print(f"  P&L Non Realizzato: ${summary['unrealized_pnl']:.2f}")
        print(f"  P&L Realizzato: ${summary['realized_pnl']:.2f}")
        print(f"\nPOSIZIONI:")
        print(f"  Aperte: {summary['open_positions']}/{BUDGET['max_open_positions']}")
        print(f"  Chiuse: {summary['closed_positions']} "
              f"({summary['winning_trades']}W / {summary['losing_trades']}L)")
        print(f"  Win Rate: {summary['win_rate']:.1f}%")

        if summary['best_trade']:
            print(f"\nBEST TRADE:")
            print(f"  {summary['best_trade']['market'][:40]}")
            print(f"  P&L: ${summary['best_trade']['pnl']:.2f} ({summary['best_trade']['pnl_pct']:.2f}%)")

        if summary['worst_trade']:
            print(f"\nWORST TRADE:")
            print(f"  {summary['worst_trade']['market'][:40]}")
            print(f"  P&L: ${summary['worst_trade']['pnl']:.2f} ({summary['worst_trade']['pnl_pct']:.2f}%)")

        if self.portfolio.positions:
            print(f"\n{'='*60}")
            print(f"POSIZIONI APERTE ({len(self.portfolio.positions)})")
            print(f"{'='*60}")
            for pos in self.portfolio.positions.values():
                symbol = "+" if pos.pnl > 0 else "-"
                print(f"{symbol} {pos.market_title[:40]} ({pos.outcome})")
                print(f"  Entry: ${pos.entry_price:.3f} | Current: ${pos.current_price:.3f}")
                print(f"  Size: ${pos.size_usdc:.2f} | P&L: ${pos.pnl:.2f} ({pos.pnl_pct:.2f}%)")
                print()

        print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Equity curve
    # ------------------------------------------------------------------
    def record_equity(self):
        """Appende un punto alla curva equity per analisi storica."""
        try:
            unrealized = sum(pos.pnl for pos in self.portfolio.positions.values())
            realized = sum(pos.pnl for pos in self.portfolio.closed_positions)
            point = {
                "timestamp": datetime.now().isoformat(),
                "strategy": self.strategy_mode,
                "equity": round(self.portfolio.total_value, 2),
                "cash": round(self.portfolio.cash, 2),
                "unrealized_pnl": round(unrealized, 2),
                "realized_pnl": round(realized, 2),
                "open_positions": self.portfolio.open_positions_count,
                "closed_positions": len(self.portfolio.closed_positions),
            }

            if self.equity_file.exists():
                with open(self.equity_file, "r") as f:
                    curve = json.load(f)
            else:
                curve = []

            curve.append(point)
            curve = curve[-10000:]  # cap

            with open(self.equity_file, "w") as f:
                json.dump(curve, f, indent=2)
        except Exception as e:
            print(f"[ERRORE] Salvataggio equity curve: {e}")

    # ------------------------------------------------------------------
    # Logging trade
    # ------------------------------------------------------------------
    def _log_trade(self, wallet_address: str, position: Position, num_holders: int):
        trade_log = {
            "timestamp": datetime.now().isoformat(),
            "strategy": self.strategy_mode,
            "wallet_address": wallet_address,
            "num_holders": num_holders,
            "position_id": position.position_id,
            "asset": position.asset,
            "market": position.market_title,
            "outcome": position.outcome,
            "side": "BUY",
            "entry_price": position.entry_price,
            "size": position.size_usdc,
        }

        try:
            if self.trades_log.exists():
                with open(self.trades_log, 'r') as f:
                    logs = json.load(f)
            else:
                logs = []
            logs.append(trade_log)
            with open(self.trades_log, 'w') as f:
                json.dump(logs, f, indent=2)
        except Exception as e:
            print(f"[ERRORE] Salvataggio trade log: {e}")

    # ------------------------------------------------------------------
    # Persistenza stato
    # ------------------------------------------------------------------
    def _save_state(self):
        try:
            state = {
                "initial_capital": self.portfolio.initial_capital,
                "cash": self.portfolio.cash,
                "strategy_mode": self.strategy_mode,
                "baseline_done": self.baseline_done,
                "baseline_assets": sorted(self.baseline_assets),
                "positions": {
                    pid: self._serialize_position(pos)
                    for pid, pos in self.portfolio.positions.items()
                },
                "closed_positions": [
                    self._serialize_position(pos)
                    for pos in self.portfolio.closed_positions
                ],
                "closed_count": len(self.portfolio.closed_positions),
                "saved_at": datetime.now().isoformat()
            }
            self._atomic_write_json(self.state_file, state)
        except Exception as e:
            print(f"[ERRORE] Salvataggio stato: {e}")

    def _atomic_write_json(self, filepath, data):
        """Scrittura atomica: scrive su temp file, poi rinomina. Crea backup."""
        filepath = str(filepath)
        backup_path = filepath + ".bak"
        dir_name = os.path.dirname(filepath) or "."

        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                if os.path.exists(filepath):
                    shutil.copy2(filepath, backup_path)

                if os.path.exists(filepath):
                    os.replace(tmp_path, filepath)
                else:
                    os.rename(tmp_path, filepath)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception as e:
            print(f"[ERRORE] Scrittura atomica fallita: {e}")
            raise

    @staticmethod
    def _serialize_position(pos: Position) -> Dict:
        return {
            "position_id": pos.position_id,
            "market_title": pos.market_title,
            "market_slug": pos.market_slug,
            "condition_id": pos.condition_id,
            "asset": pos.asset,
            "category": pos.category,
            "outcome": pos.outcome,
            "entry_price": pos.entry_price,
            "size_usdc": pos.size_usdc,
            "shares": pos.shares,
            "entry_time": pos.entry_time.isoformat(),
            "source_wallet": pos.source_wallet,
            "current_price": pos.current_price,
            "exit_price": pos.exit_price,
            "exit_time": pos.exit_time.isoformat() if pos.exit_time else None,
            "is_closed": pos.is_closed,
            "close_reason": pos.close_reason,
        }

    def _deserialize_position(self, data: Dict) -> Position:
        return Position(
            position_id=data["position_id"],
            market_title=data["market_title"],
            market_slug=data["market_slug"],
            condition_id=data["condition_id"],
            outcome=data.get("outcome", ""),
            entry_price=data["entry_price"],
            size_usdc=data["size_usdc"],
            shares=data["shares"],
            entry_time=datetime.fromisoformat(data["entry_time"]),
            source_wallet=data["source_wallet"],
            asset=data.get("asset", ""),
            category=data.get("category", ""),
            current_price=data.get("current_price", data["entry_price"]),
            exit_price=data.get("exit_price"),
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            is_closed=data.get("is_closed", False),
            close_reason=data.get("close_reason", ""),
        )

    def _load_state(self):
        if not self.state_file.exists():
            return

        state = None

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                raw = f.read()
            state = json.loads(raw)
            if not isinstance(state, dict) or "cash" not in state:
                raise ValueError("Stato corrotto: struttura invalida")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[WARNING] Stato principale corrotto ({e}), provo backup...")
            backup_path = str(self.state_file) + ".bak"
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        state = json.load(f)
                    print(f"[OK] Stato ripristinato da backup ({len(state.get('closed_positions', []))} chiuse)")
                except Exception as e2:
                    print(f"[ERRORE] Anche il backup e' corrotto: {e2}")
                    return
            else:
                print("[ERRORE] Nessun backup disponibile, parto da zero")
                return

        try:
            self.portfolio.cash = state["cash"]
            self.baseline_done = state.get("baseline_done", False)
            self.baseline_assets = set(state.get("baseline_assets", []))

            for pid, pos_data in state.get("positions", {}).items():
                self.portfolio.positions[pid] = self._deserialize_position(pos_data)

            for pos_data in state.get("closed_positions", []):
                self.portfolio.closed_positions.append(self._deserialize_position(pos_data))

            print(f"[SIMULATOR] Stato ripristinato: ${self.portfolio.cash:.2f} cash, "
                  f"{len(self.portfolio.positions)} aperte, "
                  f"{len(self.portfolio.closed_positions)} chiuse")

        except Exception as e:
            print(f"[ERRORE] Caricamento stato: {e}")


if __name__ == "__main__":
    sim = PaperTradingSimulator(initial_capital=300.0)
    sim.print_portfolio_summary()
