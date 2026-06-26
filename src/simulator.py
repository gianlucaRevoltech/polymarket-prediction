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
from datetime import datetime
from typing import Dict, Optional, List, Set
from models import Trade, Position, Portfolio
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
        Calcola la size della posizione in base al budget e al notional del wallet.
        Strategia: percentuale del portafoglio scalata sulla size del wallet target,
        limitata da max_position_size e min_position_size.
        """
        max_size = self.portfolio.total_value * BUDGET["max_position_size"]

        if target_wallet_size < 1000:
            size = self.portfolio.total_value * 0.10
        elif target_wallet_size < 10000:
            size = self.portfolio.total_value * 0.08
        elif target_wallet_size < 100000:
            size = self.portfolio.total_value * 0.05
        else:
            size = self.portfolio.total_value * 0.03

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
    def open_position(self, source_wallet: str, info: Dict, num_holders: int = 1) -> bool:
        """
        Apre una posizione simulata a partire dallo snapshot di un wallet.

        Args:
            source_wallet: wallet sorgente (per logging)
            info: posizione normalizzata (vedi portfolio_sync._normalize)
            num_holders: quanti wallet monitorati detengono l'asset (consenso)
        """
        asset = info["asset"]
        price = info["cur_price"]

        if not asset or self.has_asset(asset):
            return False

        if price <= 0 or price >= 1:
            return False

        if self.portfolio.open_positions_count >= BUDGET["max_open_positions"]:
            return False

        size = self._calculate_position_size(info.get("notional_usdc", 0.0))

        if size < BUDGET["min_position_size"]:
            return False

        if size > self._available_cash():
            print(f"[SIMULATOR] Cash insufficiente (riserva): "
                  f"${self._available_cash():.2f} < ${size:.2f}")
            return False

        # Slippage in ingresso: paghiamo un prezzo leggermente peggiore
        slippage = SIMULATOR["entry_slippage"]
        eff_price = min(0.999, price * (1 + slippage))
        shares = size / eff_price

        position_id = str(uuid.uuid4())
        position = Position(
            position_id=position_id,
            market_title=info["title"],
            market_slug=info["slug"],
            condition_id=info["condition_id"],
            outcome=info["outcome"],
            entry_price=eff_price,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet=source_wallet,
            asset=asset,
            current_price=price,
        )

        self.portfolio.add_position(position)
        self._log_trade(source_wallet, position, num_holders)

        print(f"\n[POSIZIONE APERTA] ({self.strategy_mode}, holders={num_holders})")
        print(f"  Mercato: {info['title'][:50]}")
        print(f"  Outcome: {info['outcome']}")
        print(f"  Size: ${size:.2f} | Prezzo: ${eff_price:.3f} (mkt ${price:.3f})")
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

        label = "RISOLTA" if reason == "resolved" else "CHIUSA (wallet uscito)"
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
    def reconcile(self, aggregate: Dict[str, Dict], min_wallets: int, fetcher) -> None:
        """
        Allinea il portafoglio simulato allo snapshot dei wallet.

        Args:
            aggregate: asset -> {"info", "holders", "max_notional"} (da snapshot_wallets)
            min_wallets: soglia di consenso (1 = copy puro)
            fetcher: PolymarketPositionFetcher per il prezzo di fallback
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

        # 2) Apri nuove posizioni target (escludendo baseline e gia possedute)
        for asset in qualifying:
            if asset in self.baseline_assets or self.has_asset(asset):
                continue
            entry = aggregate[asset]
            source = sorted(entry["holders"])[0]
            self.open_position(source, entry["info"], num_holders=len(entry["holders"]))

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
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[ERRORE] Salvataggio stato: {e}")

    @staticmethod
    def _serialize_position(pos: Position) -> Dict:
        return {
            "position_id": pos.position_id,
            "market_title": pos.market_title,
            "market_slug": pos.market_slug,
            "condition_id": pos.condition_id,
            "asset": pos.asset,
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
            current_price=data.get("current_price", data["entry_price"]),
            exit_price=data.get("exit_price"),
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            is_closed=data.get("is_closed", False),
            close_reason=data.get("close_reason", ""),
        )

    def _load_state(self):
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)

            self.portfolio.cash = state["cash"]
            self.baseline_done = state.get("baseline_done", False)
            self.baseline_assets = set(state.get("baseline_assets", []))
            # strategy_mode resta quello del config (run corrente), non sovrascritto

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
