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
import hashlib
import os
import tempfile
import shutil
from collections import Counter, deque
from datetime import datetime, date
from typing import Dict, Optional, List, Set, Tuple
from models import Trade, Position, Portfolio
from categories import categorize_market, taker_fee_fraction
from config import (
    BUDGET, FEES, SIMULATOR, STRATEGY, STRATEGIES, MONITOR, DATA_DIR, LOGS_DIR,
    EXECUTION,
)
from time_utils import parse_utc, utc_iso, utc_now_iso
from validation import evaluate_shadow_run
from wallet_registry import quarantine_wallet


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
        self.candidate_journal = DATA_DIR / "candidate_journal.jsonl"
        self.shadow_state_file = DATA_DIR / "shadow_state.json"
        self.shadow_journal = DATA_DIR / "shadow_journal.jsonl"
        self.shadow_equity_file = DATA_DIR / "shadow_equity_curve.json"

        self.execution_mode = EXECUTION.get("mode", "observe")
        self.run_id = f"run-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.state_saved_at: Optional[str] = None
        self.halt_reason: str = ""
        self.blocked_conditions: Dict[str, Dict] = {}
        self.strategy_loss_streaks: Dict[str, int] = {}
        self.quarantined_strategies: Set[str] = set()
        self.run_start_equity: Optional[float] = None
        self.daily_start_equity: Optional[float] = None
        self.daily_start_date: str = date.today().isoformat()

        # Phase Z: hook per wallet manager (registra copy close per wallet P&L tracking)
        self.on_copy_close = None  # callback(source_wallet, pnl)

        # Asset gia detenuti dai wallet all'avvio: registrati come baseline e NON
        # copiati (evita ingressi tardivi su posizioni vecchie a prezzo "live").
        self.baseline_assets: Set[str] = set()
        self.baseline_done: bool = False
        self.strategy_mode: str = STRATEGY["mode"]

        self._load_state()
        self.run_intended_domains: List[str] = []
        self.wallet_allowed_domains: Dict[str, Set[str]] = {}
        self.run_domains_frozen: bool = False
        self._load_run_domain_policy()
        self.shadow_positions: Dict[str, Position] = {}
        self.shadow_closed_positions: List[Position] = []
        self.shadow_seen_signal_ids: Set[str] = set()
        self.shadow_state_saved_at: Optional[str] = None
        self.shadow_initial_capital = float(
            EXECUTION.get("shadow_initial_capital", initial_capital)
        )
        self.shadow_cash = self.shadow_initial_capital
        self.shadow_run_start_equity = self.shadow_initial_capital
        self.shadow_daily_start_equity = self.shadow_initial_capital
        self.shadow_daily_start_date = date.today().isoformat()
        self.shadow_peak_equity = self.shadow_initial_capital
        self.shadow_max_drawdown = 0.0
        self.shadow_halt_reason = ""
        self.shadow_loss_streak = 0
        self.shadow_wallet_loss_streaks: Dict[str, int] = {}
        self.shadow_blocked_conditions: Dict[str, Dict] = {}
        self.shadow_legacy_unconstrained = False
        self._load_shadow_state()
        self.seen_candidate_signal_ids: Set[str] = set()
        self._load_seen_candidate_signals()
        self._cleanup_legacy_positions()
        self._load_safety_state()
        self.wallet_quality: Dict[str, Dict] = {}
        self._load_wallet_quality()

        # Phase K/L: tracking peak equity + drawdown + equity floor
        self.peak_equity: float = max(
            self.portfolio.initial_capital, self.portfolio.total_value
        )
        self._load_peak_equity()
        # Phase I: dedup anti-reopen (asset/condition_id -> ultimo timestamp apertura)
        self.recent_opens: Dict[str, datetime] = {}
        self._load_recent_opens()
        self._alert_path = getattr(MONITOR, "alert_log_path", LOGS_DIR / "alerts.log")
        # Phase CI1 (Guida 2: risk mgmt hardening): DAILY loss limit + halt.
        # Realized P&L delle posizioni chiuse oggi (exit_time.date == today).
        # Recuperato al volo da closed_positions; reset implicito a mezzanotte.
        self.daily_halt_date: Optional[date] = None
        self.daily_halt_active: bool = False
        self._load_daily_halt()

    # ------------------------------------------------------------------
    # Phase CK: quarantena, circuit breaker e journal append-only
    # ------------------------------------------------------------------
    def _safety_file(self):
        return DATA_DIR / "safety_state.json"

    def _load_safety_state(self):
        current = self.portfolio.total_value
        self.run_start_equity = current
        self.daily_start_equity = current
        try:
            if self._safety_file().exists():
                with open(self._safety_file(), encoding="utf-8") as fh:
                    data = json.load(fh)
                # Un file safety di un altro run non deve contaminare il run
                # corrente, ma i blocchi legacy senza run_id restano conservativi.
                stored_run = data.get("run_id")
                if not stored_run or stored_run == self.run_id:
                    self.blocked_conditions = dict(data.get("blocked_conditions", {}))
                    self.strategy_loss_streaks = {
                        str(k): int(v) for k, v in
                        data.get("strategy_loss_streaks", {}).items()
                    }
                    self.quarantined_strategies = set(
                        data.get("quarantined_strategies", [])
                    )
                    self.run_start_equity = float(
                        data.get("run_start_equity", current)
                    )
                    self.daily_start_equity = float(
                        data.get("daily_start_equity", current)
                    )
                    self.daily_start_date = str(
                        data.get("daily_start_date", date.today().isoformat())
                    )
                    self.halt_reason = str(data.get("halt_reason", ""))
        except Exception as exc:
            print(f"[WARNING] safety_state non leggibile: {exc}")

    def _save_safety_state(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._atomic_write_json(self._safety_file(), {
                "run_id": self.run_id,
                "run_start_equity": self.run_start_equity,
                "daily_start_equity": self.daily_start_equity,
                "daily_start_date": self.daily_start_date,
                "halt_reason": self.halt_reason,
                "blocked_conditions": self.blocked_conditions,
                "strategy_loss_streaks": self.strategy_loss_streaks,
                "quarantined_strategies": sorted(self.quarantined_strategies),
                "saved_at": utc_now_iso(),
            })
        except Exception as exc:
            print(f"[ERRORE] Salvataggio safety_state: {exc}")

    def _evaluate_equity_halts(self) -> str:
        """Circuit breaker sull'equity, quindi include il P&L non realizzato."""
        if self.halt_reason.startswith(("daily_loss", "run_loss")):
            return self.halt_reason
        equity = self.portfolio.total_value
        run_start = self.run_start_equity if self.run_start_equity is not None else equity
        daily_start = (
            self.daily_start_equity if self.daily_start_equity is not None else equity
        )
        run_loss = equity - run_start
        daily_loss = equity - daily_start
        if run_loss <= -abs(float(EXECUTION.get("run_loss_usdc", 6.0))):
            self.halt_reason = f"run_loss {run_loss:.2f} USD"
        elif daily_loss <= -abs(float(EXECUTION.get("daily_loss_usdc", 3.0))):
            self.halt_reason = f"daily_loss {daily_loss:.2f} USD"
        if self.halt_reason.startswith(("daily_loss", "run_loss")):
            self._save_safety_state()
            return self.halt_reason
        return ""

    def _opening_halt_reason(self, strategy: str) -> str:
        if self.execution_mode != "paper_validation":
            return "execution_mode=observe"
        cfg = STRATEGIES.get(strategy, {})
        if not cfg.get("paper_enabled", False):
            return f"{strategy}:paper_disabled"
        equity_halt = self._evaluate_equity_halts()
        if equity_halt:
            return equity_halt
        if strategy in self.quarantined_strategies:
            return f"{strategy}:quarantined_after_losses"
        return ""

    def reactivate_strategy(self, strategy: str) -> None:
        """Riattivazione manuale esplicita dopo una quarantena per loss streak."""
        self.quarantined_strategies.discard(strategy)
        self.strategy_loss_streaks[strategy] = 0
        if self.halt_reason.startswith(f"{strategy}:"):
            self.halt_reason = ""
        self._save_safety_state()

    def _record_close_risk(self, pos: Position, pnl: float, reason: str):
        strategy = pos.strategy or "copy"
        if reason == "stop_loss" and pos.condition_id:
            self.blocked_conditions[pos.condition_id] = {
                "blocked_at": utc_now_iso(),
                "event_slug": pos.event_slug,
                "market": pos.market_title,
                "reason": "stop_loss_until_resolution",
            }
        elif reason == "resolved" and pos.condition_id:
            self.blocked_conditions.pop(pos.condition_id, None)

        if pnl < 0:
            self.strategy_loss_streaks[strategy] = (
                self.strategy_loss_streaks.get(strategy, 0) + 1
            )
            max_losses = int(EXECUTION.get("max_consecutive_losses", 3))
            if self.strategy_loss_streaks[strategy] >= max_losses:
                self.quarantined_strategies.add(strategy)
                self.halt_reason = f"{strategy}: {max_losses} consecutive losses"
        else:
            self.strategy_loss_streaks[strategy] = 0
        self._evaluate_equity_halts()
        self._save_safety_state()

    def _journal(self, decision: str, reason: str, *, strategy: str,
                 signal_id: str = "", wallet: str = "", info: Optional[Dict] = None,
                 opp=None, book: Optional[Dict] = None, position: Optional[Position] = None,
                 costs: Optional[Dict] = None,
                 evaluation: Optional[Dict] = None) -> bool:
        """Una riga JSON immutabile per ogni candidato/decisione/chiusura."""
        info = info or {}
        evaluation = evaluation or {}
        now = utc_now_iso()
        resolved_signal_id = (
            signal_id or getattr(position, "signal_id", "") or uuid.uuid4().hex
        )
        candidate_decisions = {"eligible", "rejected", "opened"}
        if (
            decision in candidate_decisions
            and resolved_signal_id in self.seen_candidate_signal_ids
        ):
            return False
        source_trade_at = utc_iso(
            info.get("source_trade_at") or info.get("source_trade_timestamp")
        )
        source_dt = parse_utc(source_trade_at)
        detected_dt = parse_utc(now)
        latency_seconds = (
            max(0.0, (detected_dt - source_dt).total_seconds())
            if source_dt and detected_dt else None
        )
        row = {
            "journal_version": 5,
            "run_id": self.run_id,
            "signal_id": resolved_signal_id,
            "strategy": strategy,
            "wallet": wallet or info.get("source_wallet", ""),
            "transaction_hash": info.get("transaction_hash", ""),
            "event_id": info.get("event_id", "") or getattr(opp, "event_id", "") or
                        getattr(position, "event_id", ""),
            "event_slug": info.get("event_slug", "") or getattr(opp, "event_slug", "") or
                          getattr(position, "event_slug", ""),
            "event_title": info.get("event_title", "") or
                           getattr(opp, "event_title", "") or
                           getattr(position, "event_title", ""),
            "condition_id": info.get("condition_id", "") or
                            getattr(opp, "condition_id", "") or
                            getattr(position, "condition_id", ""),
            "asset": info.get("asset", "") or
                     ((getattr(opp, "assets", None) or [""])[0]) or
                     getattr(position, "asset", ""),
            "market": info.get("title", "") or getattr(opp, "market_title", "") or
                      getattr(position, "market_title", ""),
            "outcome": info.get("outcome", "") or getattr(position, "outcome", ""),
            "category": info.get("category", "") or getattr(position, "category", ""),
            "fees_enabled": evaluation.get(
                "fees_enabled", info.get("fees_enabled",
                getattr(position, "fees_enabled", None))
            ),
            "fee_schedule": evaluation.get(
                "fee_schedule", info.get("fee_schedule") or (
                    {
                        "rate": getattr(position, "fee_rate", None),
                        "exponent": getattr(position, "fee_exponent", 1.0),
                        "taker_only": True,
                    }
                    if position is not None
                    and getattr(position, "fee_rate", None) is not None
                    else None
                )
            ),
            "fee_source": evaluation.get(
                "fee_source", info.get("fee_source",
                getattr(position, "fee_source", ""))
            ),
            "source_trade_status": info.get("source_trade_status", ""),
            "source_trade_at": source_trade_at,
            "source_trade_price": info.get("source_trade_price"),
            "source_trade_size": info.get("source_trade_size"),
            "detected_at": now,
            "detection_latency_seconds": latency_seconds,
            "end_date": info.get("end_date", ""),
            "end_date_iso": info.get("end_date_iso", "") or info.get("end_date", ""),
            "book_observed_at": (book or {}).get("observed_at"),
            "best_bid": (book or {}).get("best_bid"),
            "best_ask": (book or {}).get("best_ask"),
            "bid_depth": (book or {}).get("bid_size"),
            "ask_depth": (book or {}).get("ask_size"),
            "executable_ask_vwap": evaluation.get("executable_ask_vwap"),
            "executable_bid_vwap": evaluation.get("executable_bid_vwap"),
            "ask_requested_shares": evaluation.get("ask_requested_shares"),
            "ask_available_shares": evaluation.get("ask_available_shares"),
            "ask_levels_used": evaluation.get("ask_levels_used", []),
            "bid_requested_shares": evaluation.get("bid_requested_shares"),
            "bid_available_shares": evaluation.get("bid_available_shares"),
            "bid_levels_used": evaluation.get("bid_levels_used", []),
            "planned_size_usdc": evaluation.get("planned_size_usdc"),
            "decision": decision,
            "reason": reason,
            "pretrade_eligible": bool(evaluation.get("eligible", False)),
            "pretrade_reason": evaluation.get("reason", ""),
            "entry_price": (
                getattr(position, "entry_price", None)
                or evaluation.get("entry_price")
            ),
            "exit_price": getattr(position, "exit_price", None),
            "costs": costs or {},
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.candidate_journal, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            if decision in candidate_decisions:
                self.seen_candidate_signal_ids.add(resolved_signal_id)
            return True
        except Exception as exc:
            print(f"[ERRORE] candidate journal: {exc}")
            return False

    def _load_seen_candidate_signals(self) -> None:
        """Ricostruisce il dedup append-only del run corrente dopo restart."""
        if not self.candidate_journal.exists():
            return
        try:
            with open(self.candidate_journal, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("run_id") != self.run_id:
                        continue
                    if row.get("decision") not in {"eligible", "rejected", "opened"}:
                        continue
                    signal_id = str(row.get("signal_id", ""))
                    if signal_id:
                        self.seen_candidate_signal_ids.add(signal_id)
        except Exception as exc:
            print(f"[WARNING] candidate journal non leggibile: {exc}")

    def _load_run_domain_policy(self) -> None:
        """Carica i domini immutabili del manifest wallet del run corrente."""
        path = DATA_DIR / "monitored_wallets.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("run_id") != self.run_id or not data.get("frozen"):
                return
            intended = {
                str(domain).strip().lower()
                for domain in data.get("intended_domains", [])
                if str(domain).strip()
            }
            wallet_domains: Dict[str, Set[str]] = {}
            for raw in data.get("wallets", []):
                if not isinstance(raw, dict) or not raw.get("address"):
                    continue
                allowed = raw.get("allowed_domains") or raw.get("categories") or (
                    [raw.get("category")] if raw.get("category") else []
                )
                wallet_domains[str(raw["address"]).lower()] = {
                    str(domain).strip().lower()
                    for domain in allowed if str(domain).strip()
                }
            self.run_intended_domains = sorted(intended)
            self.wallet_allowed_domains = wallet_domains
            self.run_domains_frozen = bool(
                int(data.get("domain_policy_version", 0) or 0) >= 1
                and wallet_domains
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[WARNING] domain policy non leggibile: {exc}")

    def _candidate_domain_rejection(self, source_wallet: str,
                                    category: str) -> str:
        if not self.run_domains_frozen:
            self._load_run_domain_policy()
        if not self.run_domains_frozen:
            return ""
        wallet = str(source_wallet or "").lower()
        allowed = self.wallet_allowed_domains.get(wallet)
        if allowed is None:
            return "wallet_not_in_frozen_manifest"
        if not allowed:
            return "wallet_domains_unavailable"
        if str(category or "other").lower() not in allowed:
            return "wallet_domain_mismatch"
        return ""

    # ------------------------------------------------------------------
    # Phase CS: cohort shadow indipendente dal portfolio paper
    # ------------------------------------------------------------------
    def _shadow_enabled(self) -> bool:
        return bool(EXECUTION.get("shadow_validation_enabled", True))

    def _shadow_total_value(self) -> float:
        return self.shadow_cash + sum(
            pos.current_value for pos in self.shadow_positions.values()
        )

    def _record_shadow_equity(self) -> None:
        point = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "equity": round(self._shadow_total_value(), 6),
            "cash": round(self.shadow_cash, 6),
            "open_positions": len(self.shadow_positions),
            "closed_positions": len(self.shadow_closed_positions),
            "halt_reason": self.shadow_halt_reason,
        }
        try:
            curve = []
            if self.shadow_equity_file.exists():
                curve = json.loads(
                    self.shadow_equity_file.read_text(encoding="utf-8")
                )
                if not isinstance(curve, list):
                    curve = []
            curve.append(point)
            self._atomic_write_json(self.shadow_equity_file, curve[-10000:])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"[ERRORE] shadow equity curve: {exc}")

    def _update_shadow_risk(self, *, record_curve: bool = False) -> str:
        equity = self._shadow_total_value()
        self.shadow_peak_equity = max(self.shadow_peak_equity, equity)
        if self.shadow_peak_equity > 0:
            self.shadow_max_drawdown = max(
                self.shadow_max_drawdown,
                (self.shadow_peak_equity - equity) / self.shadow_peak_equity,
            )
        today = date.today().isoformat()
        if today != self.shadow_daily_start_date and not self.shadow_halt_reason:
            self.shadow_daily_start_date = today
            self.shadow_daily_start_equity = equity
        if not self.shadow_halt_reason:
            run_loss = equity - self.shadow_run_start_equity
            daily_loss = equity - self.shadow_daily_start_equity
            if run_loss <= -abs(float(
                EXECUTION.get("shadow_run_loss_usdc", 6.0)
            )):
                self.shadow_halt_reason = f"run_loss {run_loss:.2f} USD"
            elif daily_loss <= -abs(float(
                EXECUTION.get("shadow_daily_loss_usdc", 3.0)
            )):
                self.shadow_halt_reason = f"daily_loss {daily_loss:.2f} USD"
        if record_curve:
            self._record_shadow_equity()
        return self.shadow_halt_reason

    def _save_shadow_state(self) -> None:
        if not self._shadow_enabled():
            return
        saved_at = utc_now_iso()
        try:
            self._atomic_write_json(self.shadow_state_file, {
                "shadow_version": 2,
                "run_id": self.run_id,
                "enabled": True,
                "initial_capital": self.shadow_initial_capital,
                "cash": self.shadow_cash,
                "run_start_equity": self.shadow_run_start_equity,
                "daily_start_equity": self.shadow_daily_start_equity,
                "daily_start_date": self.shadow_daily_start_date,
                "peak_equity": self.shadow_peak_equity,
                "max_drawdown": self.shadow_max_drawdown,
                "halt_reason": self.shadow_halt_reason,
                "loss_streak": self.shadow_loss_streak,
                "wallet_loss_streaks": self.shadow_wallet_loss_streaks,
                "blocked_conditions": self.shadow_blocked_conditions,
                "legacy_unconstrained": self.shadow_legacy_unconstrained,
                "intended_domains": self.run_intended_domains,
                "positions": {
                    pid: self._serialize_position(pos)
                    for pid, pos in self.shadow_positions.items()
                },
                "closed_positions": [
                    self._serialize_position(pos)
                    for pos in self.shadow_closed_positions
                ],
                "saved_at": saved_at,
            })
            self.shadow_state_saved_at = saved_at
        except Exception as exc:
            print(f"[ERRORE] Salvataggio shadow state: {exc}")

    def _load_shadow_state(self) -> None:
        if not self._shadow_enabled():
            return
        state = None
        loaded_shadow_version = 0
        candidates = [self.shadow_state_file, str(self.shadow_state_file) + ".bak"]
        for candidate in candidates:
            path = str(candidate)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if loaded.get("run_id") != self.run_id:
                    continue
                state = loaded
                break
            except Exception:
                continue
        if state is not None:
            self.shadow_state_saved_at = state.get("saved_at")
            shadow_version = int(state.get("shadow_version", 1) or 1)
            loaded_shadow_version = shadow_version
            if shadow_version >= 2:
                self.shadow_initial_capital = float(
                    state.get("initial_capital", self.shadow_initial_capital)
                )
                self.shadow_cash = float(
                    state.get("cash", self.shadow_initial_capital)
                )
                self.shadow_run_start_equity = float(
                    state.get("run_start_equity", self.shadow_initial_capital)
                )
                self.shadow_daily_start_equity = float(
                    state.get("daily_start_equity", self.shadow_initial_capital)
                )
                self.shadow_daily_start_date = str(
                    state.get("daily_start_date", date.today().isoformat())
                )
                self.shadow_peak_equity = float(
                    state.get("peak_equity", self.shadow_initial_capital)
                )
                self.shadow_max_drawdown = float(
                    state.get("max_drawdown", 0.0)
                )
                self.shadow_halt_reason = str(state.get("halt_reason", ""))
                self.shadow_loss_streak = int(state.get("loss_streak", 0) or 0)
                self.shadow_wallet_loss_streaks = {
                    str(wallet).lower(): int(streak)
                    for wallet, streak in state.get(
                        "wallet_loss_streaks", {}
                    ).items()
                }
                self.shadow_blocked_conditions = dict(
                    state.get("blocked_conditions", {})
                )
                self.shadow_legacy_unconstrained = bool(
                    state.get("legacy_unconstrained", False)
                )
            else:
                # Il cohort v1 non aveva cash/cap e puo contenere oltre due
                # posizioni. Viene preservato e gestito, ma non riceve nuovi
                # ingressi: soltanto un new-run crea un campione v2 valido.
                self.shadow_legacy_unconstrained = True
                self.shadow_halt_reason = (
                    "legacy_unconstrained_shadow_requires_new_run"
                )
            for pid, raw in state.get("positions", {}).items():
                pos = self._deserialize_position(raw)
                self.shadow_positions[pid] = pos
                if pos.signal_id:
                    self.shadow_seen_signal_ids.add(pos.signal_id)
            for raw in state.get("closed_positions", []):
                pos = self._deserialize_position(raw)
                self.shadow_closed_positions.append(pos)
                if pos.signal_id:
                    self.shadow_seen_signal_ids.add(pos.signal_id)
        if self.shadow_journal.exists():
            try:
                with open(self.shadow_journal, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("run_id") != self.run_id:
                            continue
                        signal_id = str(row.get("signal_id", ""))
                        if signal_id:
                            self.shadow_seen_signal_ids.add(signal_id)
            except OSError:
                pass
        if state is not None and loaded_shadow_version < 2:
            all_positions = (
                list(self.shadow_positions.values())
                + self.shadow_closed_positions
            )
            self.shadow_cash = self.shadow_initial_capital - sum(
                pos.size_usdc for pos in all_positions
            ) + sum(
                pos.shares * float(pos.exit_price or 0.0)
                for pos in self.shadow_closed_positions
            )
        self.shadow_peak_equity = max(
            self.shadow_initial_capital,
            self.shadow_peak_equity,
            self._shadow_total_value(),
        )
        self._update_shadow_risk()

    def _shadow_log(self, action: str, pos: Position, *, raw_price=None,
                    reason: str = "", costs: Optional[Dict] = None) -> None:
        row = {
            "shadow_version": 2,
            "run_id": self.run_id,
            "timestamp": utc_now_iso(),
            "action": action,
            "signal_id": pos.signal_id,
            "position_id": pos.position_id,
            "wallet": pos.source_wallet,
            "asset": pos.asset,
            "condition_id": pos.condition_id,
            "event_slug": pos.event_slug,
            "event_title": pos.event_title,
            "market": pos.market_title,
            "outcome": pos.outcome,
            "category": pos.category,
            "source_trade_price": pos.source_trade_price,
            "source_trade_size": pos.source_trade_size,
            "entry_best_bid": pos.entry_best_bid,
            "entry_best_ask": pos.entry_best_ask,
            "entry_price": pos.entry_price,
            "raw_price": raw_price,
            "net_price": pos.exit_price if action == "closed" else pos.current_price,
            "size_usdc": pos.size_usdc,
            "shares": pos.shares,
            "pnl": pos.pnl,
            "reason": reason,
            "costs": costs or {},
            "shadow_cash": self.shadow_cash,
            "shadow_equity": self._shadow_total_value(),
            "shadow_halt_reason": self.shadow_halt_reason,
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.shadow_journal, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[ERRORE] shadow journal: {exc}")

    def _shadow_log_candidate(self, action: str, source_wallet: str,
                              info: Dict, evaluation: Dict,
                              reason: str) -> None:
        row = {
            "shadow_version": 2,
            "run_id": self.run_id,
            "timestamp": utc_now_iso(),
            "action": action,
            "signal_id": str(evaluation.get("signal_id", "")),
            "wallet": source_wallet,
            "asset": info.get("asset", ""),
            "condition_id": info.get("condition_id", ""),
            "event_slug": info.get("event_slug", ""),
            "event_title": info.get("event_title", ""),
            "market": info.get("title", ""),
            "outcome": info.get("outcome", ""),
            "category": info.get("category", ""),
            "entry_best_bid": evaluation.get("executable_bid_vwap"),
            "entry_best_ask": evaluation.get("executable_ask_vwap"),
            "entry_price": evaluation.get("entry_price"),
            "size_usdc": evaluation.get("planned_size_usdc"),
            "reason": reason,
            "shadow_cash": self.shadow_cash,
            "shadow_equity": self._shadow_total_value(),
            "shadow_halt_reason": self.shadow_halt_reason,
        }
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.shadow_journal, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[ERRORE] shadow candidate journal: {exc}")

    def _shadow_opening_rejection(self, source_wallet: str, info: Dict) -> str:
        if self.shadow_legacy_unconstrained:
            return "legacy_unconstrained_shadow_requires_new_run"
        halt = self._update_shadow_risk()
        if halt:
            return halt
        max_positions = int(EXECUTION.get("shadow_max_open_positions", 2))
        if len(self.shadow_positions) >= max_positions:
            return "max_open_positions"
        asset = str(info.get("asset", ""))
        condition_id = str(info.get("condition_id", ""))
        event_slug = str(info.get("event_slug", ""))
        if not event_slug:
            return "missing_event_slug"
        if any(pos.asset == asset for pos in self.shadow_positions.values()):
            return "duplicate_open_asset"
        if condition_id and any(
            pos.condition_id == condition_id
            for pos in self.shadow_positions.values()
        ):
            return "duplicate_open_condition"
        if condition_id in self.shadow_blocked_conditions:
            return "condition_blocked_after_stop_loss"
        if any(
            (pos.event_slug or pos.condition_id) == event_slug
            for pos in self.shadow_positions.values()
        ):
            return "event_position_cap"
        size = float(EXECUTION.get("paper_size_usdc", 5.0))
        event_cap = self.shadow_initial_capital * float(
            EXECUTION.get("shadow_event_cap_pct", 0.03)
        )
        if size > event_cap + 1e-9:
            return "event_exposure_cap"
        if self.shadow_cash + 1e-9 < size:
            return "insufficient_shadow_cash"
        return ""

    def _open_shadow_candidate(self, source_wallet: str, info: Dict,
                               evaluation: Dict) -> bool:
        if not self._shadow_enabled() or not evaluation.get("eligible"):
            return False
        signal_id = str(evaluation.get("signal_id", ""))
        if not signal_id or signal_id in self.shadow_seen_signal_ids:
            return False
        rejection = self._shadow_opening_rejection(source_wallet, info)
        if rejection:
            self.shadow_seen_signal_ids.add(signal_id)
            self._shadow_log_candidate(
                "rejected", source_wallet, info, evaluation, rejection
            )
            self._save_shadow_state()
            return False
        entry_price = float(evaluation["entry_price"])
        shares = float(evaluation["shares"])
        raw_bid = float(evaluation["executable_bid_vwap"])
        raw_ask = float(evaluation["executable_ask_vwap"])
        position = Position(
            position_id=f"shadow-{signal_id}",
            market_title=info.get("title", ""),
            market_slug=info.get("slug", ""),
            condition_id=info.get("condition_id", ""),
            outcome=info.get("outcome", ""),
            entry_price=entry_price,
            size_usdc=float(evaluation.get("planned_size_usdc", 5.0)),
            shares=shares,
            entry_time=datetime.now(),
            source_wallet=source_wallet,
            asset=info.get("asset", ""),
            run_id=self.run_id,
            signal_id=signal_id,
            event_id=info.get("event_id", ""),
            event_slug=info.get("event_slug", ""),
            event_title=info.get("event_title", ""),
            category=info.get("category", ""),
            fees_enabled=evaluation.get("fees_enabled"),
            fee_rate=(evaluation.get("fee_schedule") or {}).get("rate"),
            fee_exponent=float(
                (evaluation.get("fee_schedule") or {}).get("exponent", 1.0)
            ),
            fee_source=evaluation.get("fee_source", "market_fee_schedule"),
            entry_best_bid=raw_bid,
            entry_best_ask=raw_ask,
            source_trade_price=info.get("source_trade_price"),
            source_trade_size=info.get("source_trade_size"),
            last_mark_at=datetime.now(),
        )
        position.strategy = "copy"
        position.current_price = self._net_liquidation_price(position, raw_bid)
        position.current_price_net_of_exit_fee = True
        self.shadow_cash -= position.size_usdc
        self.shadow_positions[position.position_id] = position
        self.shadow_seen_signal_ids.add(signal_id)
        self._shadow_log(
            "opened", position, raw_price=raw_ask,
            reason="passed_pretrade_checks", costs=evaluation.get("costs"),
        )
        self._update_shadow_risk(record_curve=True)
        self._save_shadow_state()
        # Anche una chiamata diretta in OBSERVE deve rendere persistente il
        # run_id prima di un restart; il ciclo normale salva comunque il ledger.
        if not self.state_file.exists():
            self._save_state()
        return True

    @staticmethod
    def _shadow_resolution_price(pos: Position, market: Optional[Dict]) -> Optional[float]:
        if not market or not market.get("closed"):
            return None
        tokens = list(market.get("tokens") or [])
        prices = list(market.get("outcome_prices") or [])
        if pos.asset in tokens and len(tokens) == len(prices):
            try:
                value = float(prices[tokens.index(pos.asset)])
            except (TypeError, ValueError):
                return None
            if value >= 0.95:
                return 1.0
            if value <= 0.05:
                return 0.0
        return None

    def _record_shadow_close_risk(self, pos: Position, reason: str) -> None:
        if reason == "stop_loss" and pos.condition_id:
            self.shadow_blocked_conditions[pos.condition_id] = {
                "blocked_at": utc_now_iso(),
                "event_slug": pos.event_slug,
                "market": pos.market_title,
                "reason": "stop_loss_until_resolution",
            }
        elif reason == "resolved" and pos.condition_id:
            self.shadow_blocked_conditions.pop(pos.condition_id, None)

        wallet = str(pos.source_wallet or "").lower()
        if pos.pnl < 0:
            self.shadow_loss_streak += 1
            if wallet:
                self.shadow_wallet_loss_streaks[wallet] = (
                    self.shadow_wallet_loss_streaks.get(wallet, 0) + 1
                )
            max_losses = int(
                EXECUTION.get("shadow_max_consecutive_losses", 3)
            )
            if self.shadow_loss_streak >= max_losses:
                self.shadow_halt_reason = (
                    f"copy: {max_losses} consecutive shadow losses"
                )
            wallet_max = int(
                EXECUTION.get("wallet_quarantine_consecutive_losses", 3)
            )
            wallet_streak = self.shadow_wallet_loss_streaks.get(wallet, 0)
            if wallet and wallet_streak >= wallet_max:
                try:
                    quarantine_wallet(
                        DATA_DIR,
                        wallet,
                        run_id=self.run_id,
                        reason="shadow_consecutive_losses",
                        loss_streak=wallet_streak,
                    )
                except OSError as exc:
                    print(f"[ERRORE] wallet quarantine registry: {exc}")
        else:
            self.shadow_loss_streak = 0
            if wallet:
                self.shadow_wallet_loss_streaks[wallet] = 0

    def _close_shadow(self, pid: str, raw_exit_price: float, reason: str) -> bool:
        pos = self.shadow_positions.get(pid)
        if pos is None:
            return False
        raw_exit = max(0.0, min(1.0, float(raw_exit_price)))
        exit_eff = self._exit_fee_adjusted(pos, raw_exit, reason)
        pos.close_reason = reason
        pos.close(exit_eff, datetime.now())
        pos.last_mark_at = datetime.now()
        self.shadow_cash += pos.shares * exit_eff
        self.shadow_closed_positions.append(pos)
        del self.shadow_positions[pid]
        self._record_shadow_close_risk(pos, reason)
        self._update_shadow_risk(record_curve=True)
        self._shadow_log(
            "closed", pos, raw_price=raw_exit, reason=reason,
            costs={
                "exit_fee_price": raw_exit - exit_eff,
                "exit_fee_usdc": pos.shares * (raw_exit - exit_eff),
            },
        )
        return True

    def _manage_shadow_positions(self, aggregate: Dict[str, Dict], fetcher,
                                 monitored_wallets: Optional[set],
                                 failed_wallets: Optional[set]) -> None:
        if not self._shadow_enabled() or not self.shadow_positions:
            return
        changed = False
        # La risoluzione gia confermata dal feed posizioni non dipende dal CLOB:
        # processarla prima permette di chiudere anche durante un outage /books.
        for pid, pos in list(self.shadow_positions.items()):
            entry = aggregate.get(pos.asset)
            info = (entry or {}).get("info", {})
            if info.get("redeemable", False):
                resolution_hint = info.get("cur_price")
                self._close_shadow(
                    pid, 1.0 if float(resolution_hint or 0.0) >= 0.5 else 0.0,
                    "resolved",
                )
                changed = True
        if changed:
            self._update_shadow_risk(record_curve=True)
            self._save_shadow_state()
        if not self.shadow_positions:
            return
        assets = sorted({pos.asset for pos in self.shadow_positions.values() if pos.asset})
        if hasattr(fetcher, "get_books"):
            books = fetcher.get_books(assets)
            # A failed batch is an unavailable market-data snapshot. Preserve
            # marks and retry next cycle instead of fanning out once per asset.
            if assets and not books:
                return
        else:
            books = {asset: fetcher.get_book(asset) for asset in assets}
        monitored = {str(wallet).lower() for wallet in (monitored_wallets or set())}
        failed = {str(wallet).lower() for wallet in (failed_wallets or set())}
        stop_loss = BUDGET.get("stop_loss_pct", -0.08)
        take_profit = BUDGET.get("take_profit_pct", 0.20)

        for pid, pos in list(self.shadow_positions.items()):
            entry = aggregate.get(pos.asset)
            info = (entry or {}).get("info", {})
            book = books.get(pos.asset)
            fill = self._book_fill(book, "SELL", pos.shares)
            raw_bid = fill.get("vwap")
            if raw_bid is None:
                market = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                resolved = self._shadow_resolution_price(pos, market)
                if resolved is not None:
                    self._close_shadow(pid, resolved, "resolved")
                    changed = True
                continue

            raw_bid = float(raw_bid)
            pos.current_price = self._net_liquidation_price(pos, raw_bid)
            pos.current_price_net_of_exit_fee = True
            pos.last_mark_at = datetime.now()
            changed = True

            source = (pos.source_wallet or "").lower()
            source_holds = bool(
                entry and source and any(
                    str(holder).lower() == source
                    for holder in entry.get("holders", set())
                )
            )
            if source and source in monitored and source not in failed and not source_holds:
                self._close_shadow(pid, raw_bid, "exit")
                continue

            decision = self._copy_sl_tp_decision(
                pos, raw_bid, stop_loss, take_profit
            )
            if decision in {"hard_sl", "stop_loss"}:
                self._close_shadow(pid, raw_bid, "stop_loss")
            elif decision == "take_profit":
                self._close_shadow(pid, raw_bid, "take_profit")

        if changed:
            self._update_shadow_risk(record_curve=True)
            self._save_shadow_state()

    @staticmethod
    def _copy_signal_id(source_wallet: str, info: Dict) -> str:
        explicit = info.get("signal_id") or info.get("transaction_hash")
        if explicit:
            return str(explicit)
        fingerprint = "|".join([
            "copy",
            str(source_wallet or "").lower(),
            str(info.get("asset", "")),
            str(info.get("source_trade_at") or ""),
            str(info.get("source_trade_price") or info.get("avg_price") or ""),
            str(info.get("source_trade_size") or info.get("size") or
                info.get("notional_usdc") or ""),
        ])
        return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()

    @staticmethod
    def _book_fill(book: Optional[Dict], side: str,
                   requested_shares: float) -> Dict:
        """VWAP e livelli consumati derivati da un singolo snapshot CLOB."""
        requested = max(0.0, float(requested_shares or 0.0))
        is_buy = side.upper() == "BUY"
        levels_key = "ask_levels" if is_buy else "bid_levels"
        top_key = "best_ask" if is_buy else "best_bid"
        size_key = "ask_size" if is_buy else "bid_size"
        raw_levels = list((book or {}).get(levels_key) or [])
        if not raw_levels and (book or {}).get(top_key) is not None:
            raw_levels = [{
                "price": (book or {}).get(top_key),
                "size": (book or {}).get(size_key, 0.0),
            }]
        levels = sorted(
            [
                {"price": float(level["price"]), "size": float(level["size"])}
                for level in raw_levels
                if float(level.get("size", 0) or 0) > 0
            ],
            key=lambda level: level["price"], reverse=not is_buy,
        )
        available = sum(level["size"] for level in levels)
        remaining = requested
        filled = 0.0
        notional = 0.0
        used = []
        for level in levels:
            if remaining <= 1e-9:
                break
            take = min(remaining, level["size"])
            if take <= 0:
                continue
            used.append({"price": level["price"], "size": take})
            filled += take
            notional += take * level["price"]
            remaining -= take
        full_fill = requested > 0 and remaining <= 1e-9 and filled > 0
        return {
            "vwap": (notional / filled) if full_fill else None,
            "requested_shares": requested,
            "filled_shares": filled,
            "available_shares": available,
            "levels_used": used,
            "full_fill": full_fill,
        }

    @staticmethod
    def _best_bid(fetcher, asset: str, size_shares: float = 0.0) -> Optional[float]:
        if not fetcher or not asset:
            return None
        if hasattr(fetcher, "get_executable_price"):
            try:
                return fetcher.get_executable_price(asset, "SELL", size_shares)
            except TypeError:
                return fetcher.get_executable_price(asset, "SELL")
        book = fetcher.get_book(asset)
        value = (book or {}).get("best_bid")
        return float(value) if value is not None else None

    def _condition_is_open(self, condition_id: str) -> bool:
        return any(
            p.condition_id == condition_id
            for p in self.portfolio.positions.values()
            if condition_id
        )

    # ------------------------------------------------------------------
    # Phase K: sizing compounding ladder
    # ------------------------------------------------------------------
    def _sizing_tier(self) -> Tuple[float, str]:
        """Restituisce (frazione_sizing, descr) basato su n_trade totali + WR gate."""
        n_closed = len(self.portfolio.closed_positions)
        wr = self._win_rate_closed()
        tiers = BUDGET["sizing_tiers"]
        chosen_frac = tiers[0][1]
        chosen_desc = tiers[0][2]
        for threshold, frac, desc in tiers:
            if n_closed >= threshold:
                gate = BUDGET.get("sizing_wr_gate", 0.55)
                # Se sotto WR gate e' gia oltre il tier1, resta a tier1 (conservativo)
                if threshold > 30 and wr < gate:
                    continue
                chosen_frac = frac; chosen_desc = desc
        return chosen_frac, chosen_desc

    def _win_rate_closed(self) -> float:
        c = len(self.portfolio.closed_positions)
        if c == 0:
            return 0.0
        wins = sum(1 for p in self.portfolio.closed_positions if p.pnl > 0)
        return wins / c

    def _risk_factor(self) -> float:
        """Phase K/L: sizing moltiplicatore per drawdown + equity floor."""
        now_val = self.portfolio.total_value
        # peak update
        if now_val > self.peak_equity:
            self.peak_equity = now_val
            self._save_peak_equity()
        # drawdown dal peak (de piu alto piuttosto che cash)
        dd = (self.peak_equity - now_val) / self.peak_equity if self.peak_equity > 0 else 0.0
        factor = 1.0
        if dd >= BUDGET.get("drawdown_halve_threshold", 0.12):
            factor *= BUDGET.get("drawdown_halve_factor", 0.5)
            self._alert(f"DD_HALVE equity ${now_val:.2f} peak ${self.peak_equity:.2f} dd {dd:.1%} -> sizing x{factor}")
        # equity floor bloque aperture nuove (gestisci pero' posizioni esistenti)
        pnl_pct = (now_val - self.portfolio.initial_capital) / self.portfolio.initial_capital
        if pnl_pct <= MONITOR.get("equity_floor_pct", -0.05) and pnl_pct > MONITOR.get("ruin_pct", -0.20):
            factor = 0.0
            self._alert(f"EQUITY_FLOOR pnl {pnl_pct:.1%} -> nuove aperture bloccate")
        if pnl_pct <= MONITOR.get("ruin_pct", -0.20):
            factor = 0.0
            self._alert(f"RUIN pnl {pnl_pct:.1%} -> stop totale aperture")
        return factor

    # ------------------------------------------------------------------
    # Phase CI5 (Guida nostri: copy-sport SL assoluto per tennis in-play)
    # ------------------------------------------------------------------
    def _copy_sl_tp_decision(self, pos, cur: float, stop_loss_pct: float,
                              take_profit_pct: float) -> str:
        """
        Restituisce la decisione per una posizione copy: 'hard_sl' | 'stop_loss' |
        'take_profit' | 'hold'.

        Phase CI5: per category="sport" usiamo SL assoluto (cent) invece del
        percentuale. Il tennis in-play (Swiss/Iasi) ha swing normali del 10-15%
        per break di game anche su risultato finale corretto; SL -8% su entry 0.42
        = -3.4 cent = rumore. SL assoluto -5 cent separa rumore da move reale.
        Per altre categorie manteniamo SL percentuale (valido in 0.30-0.70).
        """
        if pos.entry_price <= 0:
            return "hold"
        if (pos.category or "") == "sport":
            cfg = STRATEGIES.get("copy", {})
            sl_abs = cfg.get("sport_stop_loss_abs", -0.05)
            hard_abs = cfg.get("sport_hard_stop_loss_abs", -0.10)
            # Lo stop assoluto misura il movimento del mercato ask->bid. La
            # entry economica include la fee e anticiperebbe artificialmente lo
            # stop. I ledger legacy senza raw ask mantengono il fallback
            # conservativo all'entry economica.
            raw_entry = (
                float(pos.entry_best_ask)
                if pos.entry_best_ask is not None else float(pos.entry_price)
            )
            delta = cur - raw_entry
            pnl_pct = (cur - pos.entry_price) / pos.entry_price
            if delta <= hard_abs:
                return "hard_sl"
            if delta <= sl_abs:
                return "stop_loss"
            if pnl_pct >= take_profit_pct:
                return "take_profit"
            return "hold"
        # altre categorie: SL percentuale legacy
        pnl_pct = (cur - pos.entry_price) / pos.entry_price
        hard_sl = BUDGET.get("hard_stop_loss_pct", -0.15)
        if pnl_pct <= hard_sl:
            return "hard_sl"
        if pnl_pct <= stop_loss_pct:
            return "stop_loss"
        if pnl_pct >= take_profit_pct:
            return "take_profit"
        return "hold"

    # ------------------------------------------------------------------
    # Phase CI1 (Guida 2: daily loss limit + halt)
    # ------------------------------------------------------------------
    def _today_realized_pnl(self) -> Tuple[float, date]:
        """Realized P&L delle posizioni chiuse oggi (reset a mezzanotte)."""
        today = date.today()
        total = 0.0
        for p in self.portfolio.closed_positions:
            if p.exit_time and p.exit_time.date() == today:
                total += p.pnl
        return total, today

    def _daily_halt_check(self) -> bool:
        """True se le nuove aperture sono HALT per superamento daily loss limit."""
        realized, today = self._today_realized_pnl()
        # reset automatico a mezzanotte (giorno cambiato)
        if self.daily_halt_date != today:
            self.daily_halt_date = today
            self.daily_halt_active = False
            self._save_daily_halt()
        # valuta solo se negativo
        initial = self.portfolio.initial_capital
        if initial <= 0:
            return False
        pnl_pct = realized / initial
        limit = BUDGET.get("daily_loss_limit_pct", -0.08)
        warn = BUDGET.get("daily_loss_warn_pct", -0.05)
        if pnl_pct <= limit:
            if not self.daily_halt_active:
                self.daily_halt_active = True
                self._save_daily_halt()
                self._alert(f"DAILY_HALT realized oggi ${realized:.2f} ({pnl_pct:+.1%}) "
                            f"<= {limit:.0%} -> nuove aperture bloccate fino a mezzanotte")
            return True
        if pnl_pct <= warn:
            self._alert(f"DAILY_WARN realized oggi ${realized:.2f} ({pnl_pct:+.1%}) <= {warn:.0%}")
        return False

    def _daily_halt_file(self):
        return DATA_DIR / "daily_halt.json"

    def _load_daily_halt(self):
        try:
            f = self._daily_halt_file()
            if f.exists():
                with open(f) as fh:
                    d = json.load(fh)
                today = date.today()
                stored_date = None
                ds = d.get("date")
                if ds:
                    try:
                        stored_date = date.fromisoformat(ds)
                    except Exception:
                        stored_date = None
                if stored_date == today:
                    self.daily_halt_date = stored_date
                    self.daily_halt_active = bool(d.get("halt", False))
                else:
                    # nuovo giorno: reset
                    self.daily_halt_date = today
                    self.daily_halt_active = False
        except Exception:
            self.daily_halt_date = date.today()
            self.daily_halt_active = False

    def _save_daily_halt(self):
        try:
            with open(self._daily_halt_file(), "w") as f:
                json.dump({"date": (self.daily_halt_date or date.today()).isoformat(),
                           "halt": self.daily_halt_active,
                           "saved_at": utc_now_iso()}, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Phase CI3 (Guida 1: fee taker su USCITA per SL/TP/exit; resolution no fee)
    # ------------------------------------------------------------------
    def _exit_fee_adjusted(self, pos, exit_price: float, reason: str) -> float:
        """
        Applica la fee taker di uscita sul prezzo di chiusura per SL/TP/exit.
        Per `resolved` non c'è fee (è settlement $1/$0, non crossing order book)
        e per harvest hold-to-resolution idem.
        """
        if reason == "resolved":
            return exit_price
        cat = (pos.category or "other")
        fee_rate = getattr(pos, "fee_rate", None)
        fee_schedule = None
        if fee_rate is not None:
            fee_schedule = {
                "rate": fee_rate,
                "exponent": getattr(pos, "fee_exponent", 1.0),
                "taker_only": True,
            }
        fee_frac = taker_fee_fraction(
            cat, exit_price, fee_schedule=fee_schedule,
            fees_enabled=getattr(pos, "fees_enabled", None),
        )
        if fee_frac <= 0:
            return exit_price
        # l'uscita come taker paga il feelo OPPURE lo incassa se vende;
        # model: venditore riceve price_minore_fee = price*(1-fee_frac)
        return exit_price * (1.0 - fee_frac)

    def _net_liquidation_price(self, pos, best_bid: float) -> float:
        """Ricavo per share realmente incassabile vendendo subito al best bid."""
        best_bid = max(0.0, min(1.0, float(best_bid)))
        return self._exit_fee_adjusted(pos, best_bid, "mark_to_bid")

    def _alert(self, msg: str):
        line = f"[{utc_now_iso()}] {msg}"
        print(f"[ALERT] {msg}")
        try:
            self._alert_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._alert_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _peak_file(self):
        return DATA_DIR / "peak_equity.json"

    def _save_peak_equity(self):
        try:
            with open(self._peak_file(), "w") as f:
                json.dump({"peak_equity": self.peak_equity,
                           "saved_at": utc_now_iso()}, f)
        except Exception:
            pass

    def _load_peak_equity(self):
        try:
            persisted = 0.0
            if self._peak_file().exists():
                with open(self._peak_file()) as f:
                    d = json.load(f)
                    persisted = float(d.get("peak_equity", 0) or 0)
            self.peak_equity = max(
                self.portfolio.initial_capital,
                persisted,
                self.portfolio.total_value,
            )
        except Exception:
            self.peak_equity = max(
                self.portfolio.initial_capital, self.portfolio.total_value
            )

    def _recent_opens_file(self):
        return DATA_DIR / "recent_opens.json"

    def _load_recent_opens(self):
        try:
            if self._recent_opens_file().exists():
                with open(self._recent_opens_file()) as f:
                    d = json.load(f)
                now = datetime.now()
                dedup = BUDGET.get("dedup_window_sec", 3600)
                self.recent_opens = {
                    k: datetime.fromisoformat(v)
                    for k, v in d.items()
                    if (now - datetime.fromisoformat(v)).total_seconds() < dedup
                }
        except Exception:
            self.recent_opens = {}

    def _save_recent_opens(self):
        try:
            with open(self._recent_opens_file(), "w") as f:
                json.dump({k: v.isoformat() for k, v in self.recent_opens.items()}, f)
        except Exception:
            pass

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
    # Phase M: caps e cash per strategia
    # ------------------------------------------------------------------
    def _strategy_cap_value(self, strategy_name: str) -> float:
        """Valore massimo deployabile in posizioni di questa strategia (soft cap)."""
        cfg = STRATEGIES.get(strategy_name, {})
        cap_pct = cfg.get("cap_pct", 1.0)
        return self.portfolio.total_value * cap_pct

    def _strategy_current_deployed(self, strategy_name: str) -> float:
        return sum(p.size_usdc for p in self.portfolio.positions.values()
                   if (p.strategy or "copy") == strategy_name)

    def _strategy_available(self, strategy_name: str) -> float:
        """USDC disponibili per un nuovo trade di questa strategia (soft cap + cash)."""
        cap = self._strategy_cap_value(strategy_name)
        current = self._strategy_current_deployed(strategy_name)
        cash_avail = self._available_cash()
        return min(cap - current, cash_avail)

    def _max_single_for(self, strategy_name: str) -> float:
        cfg = STRATEGIES.get(strategy_name, {})
        max_single = cfg.get("max_single", BUDGET["max_position_size"])
        return self.portfolio.total_value * max_single

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
    def _sizing_compounding(self) -> float:
        """Phase K: sizing base per il tier corrente (frazione del portafoglio)."""
        if self.execution_mode == "paper_validation":
            return float(EXECUTION.get("paper_size_usdc", 5.0))
        frac, _ = self._sizing_tier()
        # riduce sizing (risk/reward) per compounding: valore attuale (no fixed capital)
        return self.portfolio.total_value * frac

    def _calculate_position_size(self, target_wallet_size: float) -> float:
        """
        Calcola la size della posizione in base al budget e al notional del wallet target.
        Phase K: sizing compounding ladder (3->5->8->12% gated) e drawdown halve.
        """
        base = self._sizing_compounding()
        if self.execution_mode == "paper_validation":
            return base
        # Phase K: scaling per size wallet target (rispettiamo notional whale)
        if target_wallet_size < 1000:
            size = base
        elif target_wallet_size < 10000:
            size = base * 0.8
        elif target_wallet_size < 100000:
            size = base * 0.6
        else:
            size = base * 0.4
        # Phase K/L: risk factor (drawdown halve + equity floor)
        size *= self._risk_factor()
        # Cap per strategia copy (max_single)
        max_single = self._max_single_for("copy")
        size = min(size, max_single)
        size = max(size, BUDGET["min_position_size"])
        return size

    def _available_cash(self) -> float:
        """Cash spendibile mantenendo la riserva."""
        reserve = self.portfolio.initial_capital * BUDGET["reserve_ratio"]
        return self.portfolio.cash - reserve

    # ------------------------------------------------------------------
    # Apertura posizioni
    # ------------------------------------------------------------------
    def evaluate_copy_candidate(self, source_wallet: str, info: Dict,
                                num_holders: int = 1, fetcher=None) -> Dict:
        """Valuta un candidato COPY senza modificare stato, cash o cooldown."""
        info = dict(info)
        asset = str(info.get("asset", ""))
        condition_id = str(info.get("condition_id", ""))

        market = None
        if fetcher is not None and condition_id:
            market = fetcher.get_market(condition_id)
            if market:
                for key in ("event_id", "event_slug", "event_title"):
                    if market.get(key):
                        info[key] = market[key]
                info["category"] = market.get("category") or info.get("category", "")
                info["fees_enabled"] = market.get("fees_enabled")
                info["fee_schedule"] = market.get("fee_schedule")
                info["fee_metadata_known"] = bool(
                    market.get("fee_metadata_known", False)
                )

        signal_id = self._copy_signal_id(source_wallet, info)
        result = {
            "eligible": False,
            "duplicate": signal_id in self.seen_candidate_signal_ids,
            "reason": "",
            "signal_id": signal_id,
            "info": info,
            "book": None,
            "planned_size_usdc": float(EXECUTION.get("paper_size_usdc", 5.0)),
        }
        if result["duplicate"]:
            result["reason"] = "duplicate_signal"
            return result

        def reject(reason: str) -> Dict:
            result["reason"] = reason
            return result

        if not asset:
            return reject("missing_asset")

        source_status = str(info.get("source_trade_status") or "").lower()
        if not source_status:
            source_status = (
                "ok" if info.get("source_trade_at")
                and info.get("source_trade_price") is not None else "not_found"
            )
            info["source_trade_status"] = source_status
        if source_status == "error":
            return reject("source_trade_lookup_error")
        if source_status != "ok":
            return reject("source_trade_unavailable")
        if not info.get("transaction_hash"):
            return reject("source_trade_missing_transaction_hash")
        source_dt = parse_utc(info.get("source_trade_at"))
        source_price = float(info.get("source_trade_price", 0.0) or 0.0)
        detected_dt = parse_utc(utc_now_iso())
        if source_dt is None or source_price <= 0 or source_price >= 1:
            return reject("source_trade_unavailable")
        source_age = (
            (detected_dt - source_dt).total_seconds() if detected_dt else None
        )
        result["source_trade_age_seconds"] = source_age
        if source_age is not None and source_age < -5:
            return reject("source_trade_in_future")
        max_source_age = float(STRATEGY.get("max_source_trade_age_sec", 60.0))
        if source_age is not None and source_age > max_source_age:
            return reject("source_trade_stale")

        book = fetcher.get_book(asset) if fetcher is not None else None
        result["book"] = book
        if not book or book.get("best_ask") is None or book.get("best_bid") is None:
            return reject("no_executable_two_sided_book")
        price = float(book["best_ask"])
        if price <= 0 or price >= 1:
            return reject("invalid_best_ask")

        price_min = float(STRATEGY.get("entry_price_min", 0.0))
        price_max = float(STRATEGY.get("entry_price_max", 1.0))
        soft_min = float(STRATEGY.get("soft_price_min", price_min))
        soft_max = float(STRATEGY.get("soft_price_max", price_max))
        soft_consensus = int(STRATEGY.get("soft_requires_consensus", 99))
        if not (
            price_min <= price <= price_max
            or (num_holders >= soft_consensus and soft_min <= price <= soft_max)
        ):
            return reject("entry_price_out_of_band")

        max_drift = float(STRATEGY.get("max_entry_drift", 1.0))
        if price > source_price * (1 + max_drift):
            return reject("entry_drift_too_high")

        end_iso = info.get("end_date_iso") or info.get("end_date", "")
        if end_iso and fetcher is not None:
            days = fetcher.days_to_expiry(end_iso)
            max_days = STRATEGY.get("max_days_to_expiry")
            min_days = float(STRATEGY.get("min_days_to_expiry", 0.0))
            if days is not None and max_days is not None and days > max_days:
                return reject("expiry_too_far")
            if days is not None and min_days > 0 and days < min_days:
                return reject("expiry_too_near")

        if fetcher is not None and STRATEGY.get("min_book_size_usdc"):
            min_depth = float(STRATEGY["min_book_size_usdc"])
            if (
                float(book.get("bid_size", 0) or 0) < min_depth
                or float(book.get("ask_size", 0) or 0) < min_depth
            ):
                return reject("insufficient_top_level_depth")
            spread = book.get("spread")
            max_spread = float(STRATEGY.get("max_spread_ticks", 3)) * 0.01
            if spread is None or float(spread) > max_spread:
                return reject("spread_too_wide")

        category = info.get("category") or categorize_market(
            info.get("title", ""), event_slug=info.get("event_slug", "")
        )
        info["category"] = category
        domain_rejection = self._candidate_domain_rejection(
            source_wallet, category
        )
        if domain_rejection:
            return reject(domain_rejection)
        if not info.get("fee_metadata_known", False):
            return reject("fee_schedule_unavailable")
        fees_enabled = info.get("fees_enabled")
        fee_schedule = info.get("fee_schedule")
        size = result["planned_size_usdc"]
        planned_shares = size / price
        ask_fill = self._book_fill(book, "BUY", planned_shares)
        result.update({
            "ask_requested_shares": ask_fill["requested_shares"],
            "ask_available_shares": ask_fill["available_shares"],
            "ask_levels_used": ask_fill["levels_used"],
        })
        executable_ask = ask_fill["vwap"]
        if executable_ask is None:
            return reject("insufficient_ask_depth_for_full_fill")
        executable_ask = float(executable_ask)
        if executable_ask <= 0 or executable_ask >= 1:
            return reject("invalid_executable_ask_vwap")
        try:
            fee_fraction = taker_fee_fraction(
                category, executable_ask, fee_schedule=fee_schedule,
                fees_enabled=fees_enabled,
            )
        except (TypeError, ValueError):
            return reject("fee_schedule_invalid")
        entry_price = min(0.999, executable_ask * (1 + fee_fraction))
        shares = size / entry_price
        bid_fill = self._book_fill(book, "SELL", shares)
        result.update({
            "bid_requested_shares": bid_fill["requested_shares"],
            "bid_available_shares": bid_fill["available_shares"],
            "bid_levels_used": bid_fill["levels_used"],
        })
        executable_bid = bid_fill["vwap"]
        if executable_bid is None:
            return reject("insufficient_bid_depth_for_full_exit")

        result.update({
            "eligible": True,
            "reason": "passed_pretrade_checks",
            "executable_ask_vwap": executable_ask,
            "executable_bid_vwap": float(executable_bid),
            "entry_price": entry_price,
            "shares": shares,
            "fee_fraction": fee_fraction,
            "fees_enabled": fees_enabled,
            "fee_schedule": fee_schedule,
            "fee_source": "market_fee_schedule",
            "costs": {
                "fee_fraction": fee_fraction,
                "fee_price": entry_price - executable_ask,
                "fee_usdc": shares * (entry_price - executable_ask),
                "slippage_price": executable_ask - price,
            },
        })
        return result

    def open_position(self, source_wallet: str, info: Dict, num_holders: int = 1,
                      fetcher=None) -> bool:
        """
        Apre una posizione simulata da uno snapshot di wallet.

        Phase D: filtra per scadenza (max_days_to_expiry) e liquidita (book size/spread).
        Phase E: caps per wallet sorgente e per categoria (anti-correlazione).
        Phase B: soft-disable (size dimezzata) per wallet con win-rate basso.
        Phase C: chiamata solo per asset NUOVI (delta-snapshot), vedi reconcile.
        """
        evaluation = self.evaluate_copy_candidate(
            source_wallet, info, num_holders=num_holders, fetcher=fetcher
        )
        if evaluation["duplicate"]:
            return False
        info = evaluation["info"]
        asset = info.get("asset", "")
        condition_id = info.get("condition_id", "")
        signal_id = evaluation["signal_id"]

        # Arricchisce l'identità evento per snapshot data-api legacy che non la
        # espongono. Per la correlazione non usiamo mai market_slug come evento.
        if fetcher is not None and condition_id and not info.get("event_slug"):
            market = fetcher.get_market(condition_id)
            if market:
                info["event_id"] = market.get("event_id", "")
                info["event_slug"] = market.get("event_slug", "")
                info["event_title"] = market.get("event_title", "")
                info["category"] = market.get("category") or info.get("category", "")

        book = evaluation["book"]
        if not evaluation["eligible"]:
            self._journal(
                "rejected", evaluation["reason"], strategy="copy",
                signal_id=signal_id, wallet=source_wallet, info=info, book=book,
                evaluation=evaluation,
            )
            return False
        self._open_shadow_candidate(source_wallet, info, evaluation)
        if self.execution_mode == "observe":
            self._journal(
                "eligible", "passed_pretrade_checks", strategy="copy",
                signal_id=signal_id, wallet=source_wallet, info=info, book=book,
                costs=evaluation.get("costs"), evaluation=evaluation,
            )
            return False

        def reject(reason: str) -> bool:
            self._journal("rejected", reason, strategy="copy",
                          signal_id=signal_id, wallet=source_wallet,
                          info=info, book=book, evaluation=evaluation,
                          costs=evaluation.get("costs"))
            return False

        halt = self._opening_halt_reason("copy")
        if halt:
            return reject(halt)
        if self.portfolio.open_positions_count >= int(
            EXECUTION.get("max_open_positions", BUDGET["max_open_positions"])
        ):
            return reject("max_open_positions")
        if not book or book.get("best_ask") is None or book.get("best_bid") is None:
            return reject("no_executable_two_sided_book")
        price = float(book["best_ask"])
        mark_bid = float(book["best_bid"])

        if not asset or self.has_asset(asset):
            return reject("duplicate_open_asset")
        if condition_id and self._condition_is_open(condition_id):
            return reject("duplicate_open_condition")
        if condition_id in self.blocked_conditions:
            market = fetcher.get_market(condition_id) if fetcher is not None else None
            if market and market.get("closed"):
                self.blocked_conditions.pop(condition_id, None)
                self._save_safety_state()
                reason = "condition_resolved"
            else:
                reason = "condition_blocked_after_stop_loss"
            return reject(reason)
        if not self._cluster_check(
            "copy", condition_id, info.get("event_slug", ""),
            float(EXECUTION.get("paper_size_usdc", 5.0)),
        ):
            return reject("event_exposure_limit")

        if price <= 0 or price >= 1:
            return reject("invalid_best_ask")

        # Phase I: dedup anti-reopen stesso asset entro dedup_window
        now = datetime.now()
        dedup = BUDGET.get("dedup_window_sec", 3600)
        last_open = self.recent_opens.get(asset)
        if last_open and (now - last_open).total_seconds() < dedup:
            return reject("recent_asset_cooldown")
        last_cond = self.recent_opens.get(info.get("condition_id", ""))
        if last_cond and (now - last_cond).total_seconds() < dedup:
            return reject("recent_condition_cooldown")

        # Equity floor legacy resta un'ulteriore cintura di sicurezza.
        if self._risk_factor() <= 0.0:
            return reject("legacy_equity_floor")

        # Guardrail 1 - banda di prezzo (Phase D + J soft):
        price_min = STRATEGY.get("entry_price_min", 0.0)
        price_max = STRATEGY.get("entry_price_max", 1.0)
        soft_min = STRATEGY.get("soft_price_min", price_min)
        soft_max = STRATEGY.get("soft_price_max", price_max)
        soft_consensus = STRATEGY.get("soft_requires_consensus", 99)
        if price < price_min or price > price_max:
            if num_holders >= soft_consensus and soft_min <= price <= soft_max:
                pass  # consentito: consenso alto compensa banda allargata
            else:
                print(f"[SKIP] Prezzo {price:.3f} fuori banda [{price_min:.2f},{price_max:.2f}]"
                      f" (consenso {num_holders} < {soft_consensus}): {info['title'][:45]}")
                return reject("entry_price_out_of_band")

        # Guardrail 2 - anti entrata tardiva (Phase C: drift 5%)
        source_price = float(info.get("source_trade_price", 0.0) or 0.0)
        max_drift = STRATEGY.get("max_entry_drift", 1.0)
        if source_price > 0 and price > source_price * (1 + max_drift):
            print(f"[SKIP] Entrata tardiva: prezzo {price:.3f} > BUY sorgente {source_price:.3f} "
                  f"+{max_drift:.0%}: {info['title'][:40]}")
            return reject("entry_drift_too_high")

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
                        return reject("expiry_too_far")
                    if min_days > 0 and days < min_days:
                        print(f"[SKIP] Scadenza {days:.1f}gg < {min_days}gg (coin-flip/MM): "
                              f"{info['title'][:40]}")
                        return reject("expiry_too_near")

        # Phase D: filtro liquidita (book size + spread)
        if fetcher is not None and STRATEGY.get("min_book_size_usdc"):
            ok = fetcher.passes_liquidity(
                book, side_size_min=STRATEGY["min_book_size_usdc"],
                max_spread_ticks=STRATEGY.get("max_spread_ticks", 3))
            if not ok:
                print(f"[SKIP] Liquidita insufficiente: {info['title'][:40]}")
                return reject("insufficient_liquidity")

        if self.portfolio.open_positions_count >= BUDGET["max_open_positions"]:
            return reject("max_open_positions")

        # Phase M: cap posizioni per strategia copy (lascia slot ad arb/harvest)
        copy_max_pos = STRATEGIES.get("copy", {}).get("max_positions", BUDGET["max_open_positions"])
        n_copy = sum(1 for p in self.portfolio.positions.values()
                    if (p.strategy or "copy") == "copy")
        if n_copy >= copy_max_pos:
            print(f"[SKIP] Cap posizioni copy ({copy_max_pos}) raggiunto: {info['title'][:40]}")
            return reject("copy_position_cap")

        # Phase E: cap per wallet sorgente (max 1 posizione aperta per wallet)
        max_per_wallet = BUDGET.get("max_positions_per_wallet", 1)
        if self._positions_for_wallet(source_wallet) >= max_per_wallet:
            print(f"[SKIP] Cap wallet raggiunto ({max_per_wallet}) per "
                  f"{source_wallet[:10]}: {info['title'][:40]}")
            return reject("source_wallet_position_cap")

        # Phase E: cap per categoria (anti-correlazione, es. 2 bet politica 2028)
        max_per_cat = BUDGET.get("max_positions_per_category", 99)
        category = info.get("category") or categorize_market(
            info["title"], event_slug=info.get("event_slug", ""))
        if max_per_cat < 99 and self._positions_for_category(category) >= max_per_cat:
            print(f"[SKIP] Cap categoria '{category}' raggiunto ({max_per_cat}): "
                  f"{info['title'][:40]}")
            return reject("category_position_cap")

        size = self._calculate_position_size(info.get("notional_usdc", 0.0))

        # cap per strategia copy (soft): non superare cap_pct deployato
        copy_avail = self._strategy_available("copy")
        if size > copy_avail:
            size = max(BUDGET["min_position_size"], copy_avail)
        if size < BUDGET["min_position_size"]:
            return reject("strategy_cap_below_min_size")

        # Phase B: soft-disable wallet win-rate basso (NON rimosso, size dimezzata)
        factor = (
            1.0 if self.execution_mode == "paper_validation"
            else self._wallet_size_factor(source_wallet)
        )
        if factor < 1.0:
            size *= factor
            print(f"[SOFT-DISABLE] wallet {source_wallet[:10]} WR basso: "
                  f"size x{factor:.2f} -> ${size:.2f}")

        if size < BUDGET["min_position_size"]:
            return reject("wallet_factor_below_min_size")

        if size > self._available_cash():
            print(f"[SIMULATOR] Cash insufficiente (riserva): "
                  f"${self._available_cash():.2f} < ${size:.2f}")
            return reject("insufficient_cash_after_reserve")

        # Categoria (per fee) e costo d'ingresso: slippage + taker fee per categoria
        category = info.get("category") or categorize_market(
            info["title"], event_slug=info.get("event_slug", "")
        )
        # Riusa la valutazione derivata dall'unico snapshot CLOB pre-trade.
        # In paper_validation la size è fissa e coincide con planned_size_usdc.
        eff_price = float(evaluation["executable_ask_vwap"])
        if eff_price <= 0 or eff_price >= 1:
            return reject("invalid_executable_ask_vwap")
        fee_frac = float(evaluation.get("fee_fraction", 0.0))
        # Prezzo effettivo pagato includendo la fee taker per-market gia
        # calcolata sul medesimo snapshot della valutazione pre-trade.
        eff_price_with_fee = min(0.999, eff_price * (1 + fee_frac))
        shares = size / eff_price_with_fee
        mark_bid = evaluation.get("executable_bid_vwap")
        if mark_bid is None:
            return reject("insufficient_bid_depth_for_full_exit")
        mark_bid = float(mark_bid)

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
            run_id=self.run_id,
            signal_id=signal_id,
            event_id=info.get("event_id", ""),
            event_slug=info.get("event_slug", ""),
            event_title=info.get("event_title", ""),
            category=category,
            fees_enabled=evaluation.get("fees_enabled"),
            fee_rate=(evaluation.get("fee_schedule") or {}).get("rate"),
            fee_exponent=float(
                (evaluation.get("fee_schedule") or {}).get("exponent", 1.0)
            ),
            fee_source=evaluation.get("fee_source", "market_fee_schedule"),
            entry_best_bid=mark_bid,
            entry_best_ask=eff_price,
            source_trade_price=info.get("source_trade_price"),
            source_trade_size=info.get("source_trade_size"),
            last_mark_at=datetime.now(),
            current_price=mark_bid,
        )

        # Equity e circuit breaker devono includere anche il costo di uscita:
        # il mark e' il ricavo netto liquidabile, non il best bid lordo.
        position.current_price = self._net_liquidation_price(position, mark_bid)
        position.current_price_net_of_exit_fee = True

        position.strategy = "copy"
        self.portfolio.add_position(position)
        self._log_trade(source_wallet, position, num_holders)
        # Phase I: registra apertura per dedup
        self.recent_opens[asset] = now
        if info.get("condition_id"):
            self.recent_opens[info["condition_id"]] = now
        self.recent_opens = {
            k: v for k, v in self.recent_opens.items()
            if (now - v).total_seconds() < BUDGET.get("dedup_window_sec", 3600)
        }
        self._save_recent_opens()
        self._journal(
            "opened", "paper_validation", strategy="copy", signal_id=signal_id,
            wallet=source_wallet, info=info, book=book, position=position,
            costs={
                "fee_fraction": fee_frac,
                "fee_price": eff_price_with_fee - eff_price,
                "fee_usdc": shares * (eff_price_with_fee - eff_price),
                "slippage_price": eff_price - price,
            },
            evaluation=evaluation,
        )

        print(f"\n[POSIZIONE APERTA] ({self.strategy_mode}, holders={num_holders}, cat={category})")
        print(f"  Mercato: {info['title'][:50]}")
        print(f"  Outcome: {info['outcome']}")
        print(f"  Size: ${size:.2f} | Pagato: ${eff_price_with_fee:.3f} "
              f"(ask ${price:.3f}, fee {((eff_price_with_fee/price)-1)*100:.1f}%)")
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
                p.current_price = self._net_liquidation_price(p, price)
                p.current_price_net_of_exit_fee = True
                p.last_mark_at = datetime.now()

    def close_by_asset(self, asset: str, exit_price: float, reason: str) -> bool:
        """Chiude la posizione associata a un asset al prezzo dato."""
        pid, pos = self._find_position_by_asset(asset)
        if pos is None:
            return False

        exit_price = max(0.0, min(1.0, exit_price))
        # Phase CI3 (Guida 1: taker fee anche su USCITA per SL/TP/exit; no fee per resolved).
        # Il pnl mostrato è ora NETTO delle fee di ingresso + uscita.
        exit_eff = self._exit_fee_adjusted(pos, exit_price, reason)
        pos.close_reason = reason
        pnl = (exit_eff - pos.entry_price) * pos.shares

        self.portfolio.close_position(pid, exit_eff, datetime.now())

        label = {
            "resolved": "RISOLTA",
            "exit": "CHIUSA (wallet uscito)",
            "stop_loss": "STOP LOSS",
            "take_profit": "TAKE PROFIT",
        }.get(reason, f"CHIUSA ({reason})")
        outcome_label = "PROFIT" if pnl > 0 else "LOSS"
        fee_note = "" if exit_eff == exit_price else f" (exit_fee -> {exit_eff:.3f})"
        print(f"\n[POSIZIONE {label}] {outcome_label}")
        print(f"  Mercato: {pos.market_title[:50]} ({pos.outcome})")
        print(f"  Entry: ${pos.entry_price:.3f} -> Exit: ${exit_price:.3f}{fee_note}")
        print(f"  P&L: ${pnl:.2f} | Cash: ${self.portfolio.cash:.2f}")

        # Phase CI3: loggaclose con exit NETTO delle fee di uscita (come il pnl)
        self._log_close_trade(pos, exit_eff, reason)
        self._record_close_risk(pos, pnl, reason)
        self._journal("closed", reason, strategy=pos.strategy or "copy",
                      wallet=pos.source_wallet, position=pos,
                      costs={
                          "exit_fee_price": exit_price - exit_eff,
                          "exit_fee_usdc": pos.shares * (exit_price - exit_eff),
                      })
        # Phase Z: notifica wallet manager per tracking P&L per-wallet (solo copy)
        if (pos.strategy or "copy") == "copy" and self.on_copy_close and pos.source_wallet:
            try:
                self.on_copy_close(pos.source_wallet, pnl)
            except Exception:
                pass
        self._save_state()
        return True

    # ------------------------------------------------------------------
    # Riconciliazione: cuore del mirroring
    # ------------------------------------------------------------------
    def reconcile(self, aggregate: Dict[str, Dict], min_wallets: int, fetcher,
                  new_holdings: Optional[set] = None,
                  monitored_wallets: Optional[set] = None,
                  failed_wallets: Optional[set] = None) -> None:
        """
        Phase I: copy-trade puntuale via DELTA per-WALLET.

        Si aprono SOLO asset in `new_holdings` (insieme di (wallet, asset)
        comparsi dall'ultimo ciclo). Questo cattura anche ingressi multi-wallet
        sullo stesso asset (caso P10: il vecchio delta per-asset li perdeva).

        Args:
            aggregate: asset -> {"info", "holders", "max_notional"}
            min_wallets: soglia di consenso (1 = copy puro)
            fetcher: PolymarketPositionFetcher per fallback + filtri D
            new_holdings: set di (wallet, asset) NUOVI dal main loop. Se e'
                None e delta_copy e attivo, NON si apre nulla (safety).
            monitored_wallets: set di wallet attualmente monitorati. Se il wallet
                sorgente di una posizione copy NON e' piu' in questo set (rotazione/
                swap), la posizione NON viene chiusa a "exit" forzato — viene gestita
                solo con SL/TP. Evita chiusure premature da wallet rotation.
            failed_wallets: wallet il cui snapshot corrente è fallito. L'assenza
                di un asset da questi wallet non prova una vendita e non chiude.
        """
        qualifying = {a for a, e in aggregate.items() if len(e["holders"]) >= min_wallets}

        # Baseline: posizioni preesistenti non copiate (zero-dump al primo ciclo)
        if not self.baseline_done:
            self.baseline_assets = set(qualifying)
            self.baseline_done = True
            print(f"[BASELINE] Registrate {len(self.baseline_assets)} posizioni "
                  f"preesistenti (non copiate)")
        else:
            self.baseline_assets &= qualifying

        stop_loss = BUDGET.get("stop_loss_pct", -0.30)
        take_profit = BUDGET.get("take_profit_pct", 0.50)

        # Cohort osservazionale completo: viene aggiornato prima delle posizioni
        # paper e non modifica cash, cooldown, halt o quarantene.
        self._manage_shadow_positions(
            aggregate, fetcher, monitored_wallets, failed_wallets
        )

        # 1) Gestisci posizioni COPY aperte (SL/TP/exit/resolved)
        for asset, pos in list(self.get_open_assets().items()):
            if (pos.strategy or "copy") != "copy":
                continue  # arb/harvest/cross gestiti in manage_strategy_positions
            # posizionamientó prezzo: usa asset detentuto da wallet → aggregate;
            # altrimenti fallback CLOB
            cur = None
            resolution_hint = None
            entry = aggregate.get(asset)
            redeemable = False
            if entry is not None:
                info = entry["info"]
                resolution_hint = info.get("cur_price")
                redeemable = info.get("redeemable", False)
            # Mark e uscita devono essere vendibili: sempre best bid, mai midpoint
            # o prezzo indicativo del wallet sorgente.
            cur = self._best_bid(fetcher, asset, pos.shares)

            if redeemable:
                resolved_price = (
                    1.0 if (resolution_hint is not None and resolution_hint >= 0.5)
                    else 0.0
                )
                self.close_by_asset(asset, resolved_price, "resolved")
                continue
            if cur is None:
                market = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                if market is not None and market.get("closed"):
                    hint = resolution_hint if resolution_hint is not None else pos.current_price
                    self.close_by_asset(asset, 1.0 if hint >= 0.5 else 0.0, "resolved")
                continue
            if asset not in qualifying:
                # Solo uno snapshot riuscito in cui il wallet sorgente non ha
                # più l'asset prova una vendita. Timeout, rotazione o semplice
                # perdita del consenso non autorizzano un'uscita.
                src = (pos.source_wallet or "").lower()
                mon_set = monitored_wallets or set()
                mon_set_lower = {w.lower() for w in mon_set}
                failed_set_lower = {w.lower() for w in (failed_wallets or set())}
                source_still_holds = bool(
                    entry and src and any(
                        str(holder).lower() == src
                        for holder in entry.get("holders", set())
                    )
                )
                if (
                    src and src in mon_set_lower
                    and src not in failed_set_lower
                    and not source_still_holds
                ):
                    # Snapshot sorgente riuscito e asset assente: exit legittimo.
                    self.close_by_asset(asset, cur, "exit")
                    continue
                else:
                    # Sorgente non confermata: aggiorna e applica solo SL/TP.
                    self.update_price_by_asset(asset, cur)
                    # Phase CI5: SL assoluto per copy-sport, percentuale per altri.
                    decision = self._copy_sl_tp_decision(pos, cur, stop_loss, take_profit)
                    pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                    if decision == "hard_sl":
                        print(f"[HARD SL] {pos.market_title[:40]} P&L {pnl_pct:.1%} (sorgente non confermata)")
                        self.close_by_asset(asset, cur, "stop_loss")
                    elif decision == "stop_loss":
                        print(f"[STOP LOSS] {pos.market_title[:40]} P&L {pnl_pct:.1%} (sorgente non confermata)")
                        self.close_by_asset(asset, cur, "stop_loss")
                    elif decision == "take_profit":
                        print(f"[TAKE PROFIT] {pos.market_title[:40]} P&L {pnl_pct:.1%} (sorgente non confermata)")
                        self.close_by_asset(asset, cur, "take_profit")
                    continue
            self.update_price_by_asset(asset, cur)
            # Phase CI5: SL assoluto per copy-sport, percentuale per altri.
            decision = self._copy_sl_tp_decision(pos, cur, stop_loss, take_profit)
            pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
            if decision == "hard_sl":
                print(f"[HARD SL] {pos.market_title[:40]} P&L {pnl_pct:.1%}")
                self.close_by_asset(asset, cur, "stop_loss")
            elif decision == "stop_loss":
                print(f"[STOP LOSS] {pos.market_title[:40]} P&L {pnl_pct:.1%}")
                self.close_by_asset(asset, cur, "stop_loss")
            elif decision == "take_profit":
                print(f"[TAKE PROFIT] {pos.market_title[:40]} P&L {pnl_pct:.1%}")
                self.close_by_asset(asset, cur, "take_profit")

        # 2) Apri nuove posizioni COPY solo su asset presenti in new_holdings
        def _candidate_key(asset: str):
            entry = aggregate[asset]
            info = entry["info"]
            avg = info.get("avg_price", 0.0)
            cur = info.get("cur_price", 0.0)
            drift = (cur / avg - 1) if avg > 0 else 0.0
            return (-len(entry["holders"]), drift)

        delta_on = STRATEGY.get("delta_copy", False)
        if delta_on and new_holdings is None:
            candidates = []
        elif delta_on and new_holdings is not None:
            # asset che hanno almeno un (wallet, asset) nuovo → aperture multi-wallet
            new_assets_set = {a for (w, a) in new_holdings}
            # mappa wallet sorgente per asset (qualsiasi wallet fresco)
            new_by_asset: Dict[str, set] = {}
            for (w, a) in new_holdings:
                new_by_asset.setdefault(a, set()).add(w)
            candidates = [
                a for a in qualifying
                if a in new_assets_set
                and a not in self.baseline_assets
                and not self.has_asset(a)
            ]
        else:
            candidates = [
                a for a in qualifying
                if a not in self.baseline_assets and not self.has_asset(a)
            ]

        for asset in sorted(candidates, key=_candidate_key):
            entry = aggregate[asset]
            # scegli il wallet sorgente: se l'asset ha wallet freschi (delta), usa
            # quello; altrimenti un holder qualunque.
            if delta_on and new_holdings is not None:
                fresh_wallets = new_by_asset.get(asset, set())
                source_pool = fresh_wallets & entry.get("holders", set()) or entry["holders"]
            else:
                source_pool = entry["holders"]
            source = sorted(source_pool)[0]
            candidate_info = dict(entry["info"])
            candidate_info["source_wallet"] = source
            if hasattr(fetcher, "get_recent_buy_result"):
                lookup = fetcher.get_recent_buy_result(source, asset)
                candidate_info["source_trade_status"] = lookup.status
                if lookup.trade:
                    candidate_info.update(lookup.trade)
                if lookup.error:
                    candidate_info["source_trade_error"] = lookup.error
            elif hasattr(fetcher, "get_recent_buy"):
                source_trade = fetcher.get_recent_buy(source, asset)
                candidate_info["source_trade_status"] = (
                    "ok" if source_trade else "not_found"
                )
                if source_trade:
                    candidate_info.update(source_trade)
            else:
                candidate_info["source_trade_status"] = "not_found"
            self.open_position(source, candidate_info,
                               num_holders=len(entry["holders"]),
                               fetcher=fetcher)

        # 3) Gestisce posizioni NON-copy (arb/harvest/cross) separate
        # (aggiornamento prezzo + resolution + SL per harvest)
        # Non apriamo qui; aperture via execute_opportunity (main loop).

        # 4) Registra equity e salva
        self.record_equity()
        self._save_state()

    # ------------------------------------------------------------------
    # Phase M: gestione posizioni arb/harvest/arb_cross (SL/TP/resolution)
    # ------------------------------------------------------------------
    def manage_strategy_positions(self, fetcher) -> None:
        """Aggiorna e chiude posizioni NON-copy aperte (resolution + SL harvest)."""
        for pid, pos in list(self.portfolio.positions.items()):
            strat = (pos.strategy or "copy")
            if strat == "copy":
                continue
            # prezzo corrente: bundle = somma mids (arb), o mid del token (harvest)
            if strat in ("arb_binary", "arb_cross"):
                # bundle: estimiamo valore corrente come somma best_bid_leg
                # (in paper, approssimiamo con last/current noto). A resolution → 1.0.
                m = fetcher.get_market(pos.condition_id) if strat == "arb_binary" else None
                resolved = False
                if m is not None:
                    resolved = bool(m.get("closed"))
                if not resolved and strat == "arb_cross":
                    # arb_cross: condition_id = event slug; risolto quando tutti
                    # sotto-mercati sono closed (approssimazione: primo mercato)
                    pass
                if resolved:
                    self._close_by_pid(pid, 1.0, "resolved")
                    continue
                # aggiorna prezzo corrente = payout atteso mark-to-mid (per equity)
                # approssimazione: lascia entry come current (risk-free-ish, no MTM)
                continue
            if strat == "harvest":
                cur = self._best_bid(fetcher, pos.asset, pos.shares)
                if cur is None:
                    # market may be resolved: prova a leggere via gamma
                    m = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                    if m is not None and m.get("closed"):
                        exit_price = 1.0 if (pos.entry_price >= 0.50) else 0.0
                        self._close_by_pid(pid, exit_price, "resolved")
                    continue
                if cur <= 0.0 or cur >= 1.0:
                    self._close_by_pid(pid, 1.0 if cur >= 0.5 else 0.0, "resolved")
                    continue
                pos.current_price = self._net_liquidation_price(pos, cur)
                pos.current_price_net_of_exit_fee = True
                pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                # Phase CD: SL assoluto (cent) — robusto a prezzi estremi dove
                # SL % triggera su rumore. Un near-certain market che scende 5 cent
                # ha davvero problemi; 2 cent e' rumore normale.
                hard_abs = BUDGET.get("harvest_hard_stop_abs", -0.05)
                soft_exit_abs = BUDGET.get("harvest_soft_exit_abs", -0.15)
                early_tp = BUDGET.get("harvest_take_profit_pct", 0.0)
                price_delta = cur - pos.entry_price   # assoluto in $
                # Phase CF: hold-to-resolution. Early TP solo se > 0 (ora 0.0 = disabled).
                if early_tp > 0 and pnl_pct >= early_tp:
                    print(f"[HARVEST EARLY TP] {pos.market_title[:40]} P&L {pnl_pct:.1%} >= {early_tp:.0%}")
                    self._close_by_pid(pid, cur, "take_profit")
                elif price_delta <= soft_exit_abs:
                    # black-swan: prezzo crollato >15 cent → esito non era certo
                    print(f"[HARVEST EXIT] {pos.market_title[:40]} delta {price_delta:+.3f} (black-swan)")
                    self._close_by_pid(pid, cur, "stop_loss")
                elif price_delta <= hard_abs and cur < 0.85:
                    # prezzo sceso >5 cent E sotto 0.85 → esito non era certo; esci
                    print(f"[HARVEST HARD SL] {pos.market_title[:40]} cur {cur:.3f} delta {price_delta:+.3f}")
                    self._close_by_pid(pid, cur, "stop_loss")
                # else: hold-to-resolution (payout $1 è l'edge reale)
            elif strat == "momentum":
                # Phase W: gestione momentum — SL/TP direzionale + resolution
                cur = self._best_bid(fetcher, pos.asset, pos.shares)
                if cur is None:
                    m = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                    if m is not None and m.get("closed"):
                        # resolved: payout $1 se nostro outcome vince, $0 altro
                        # heuristic: usciamo al prezzo corrente di resolution
                        exit_price = 1.0 if (pos.outcome and pos.outcome.lower() in ("yes",)) else 0.0
                        self._close_by_pid(pid, exit_price, "resolved")
                    continue
                if cur <= 0.0 or cur >= 1.0:
                    self._close_by_pid(pid, 1.0 if cur >= 0.5 else 0.0, "resolved")
                    continue
                pos.current_price = self._net_liquidation_price(pos, cur)
                pos.current_price_net_of_exit_fee = True
                pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                mtp = STRATEGIES.get("momentum", {}).get("take_profit_pct", 0.06)
                msl = STRATEGIES.get("momentum", {}).get("stop_loss_pct", -0.05)
                msl_abs = STRATEGIES.get("momentum", {}).get("stop_loss_abs", -0.03)
                # Phase CD: SL assoluto (cent) — robusto a prezzi estremi
                price_delta = cur - pos.entry_price
                # Phase CC: trailing stop disabilitato (config trailing_stop_enabled=False)
                if BUDGET.get("trailing_stop_enabled", False) and strat in BUDGET.get("trailing_apply_strategies", []):
                    trail_act = BUDGET.get("trailing_activate_pct", 0.03)
                    trail_pct = BUDGET.get("trailing_stop_pct", -0.03)
                    if pnl_pct >= trail_act:
                        peak = getattr(pos, '_peak_price', pos.entry_price)
                        if cur > peak:
                            pos._peak_price = cur
                            peak = cur
                        trail_pnl = (cur - peak) / peak if peak > 0 else 0
                        if trail_pnl <= trail_pct:
                            print(f"[MOMENTUM TRAIL] {pos.market_title[:40]} peak {peak:.3f} cur {cur:.3f}")
                            self._close_by_pid(pid, cur, "trailing_stop")
                            continue
                if pnl_pct >= mtp:
                    print(f"[MOMENTUM TP] {pos.market_title[:40]} P&L {pnl_pct:.1%} >= {mtp:.0%}")
                    self._close_by_pid(pid, cur, "take_profit")
                elif price_delta <= msl_abs:
                    # Phase CD: SL assoluto (cent) — triggera su move reale, non rumore
                    print(f"[MOMENTUM SL] {pos.market_title[:40]} delta {price_delta:+.3f} (abs SL {msl_abs:+.3f})")
                    self._close_by_pid(pid, cur, "stop_loss")
                elif pnl_pct <= msl and price_delta > msl_abs:
                    # SL % solo se NON gia coperto da SL assoluto (edge case prezzi alti)
                    print(f"[MOMENTUM SL] {pos.market_title[:40]} P&L {pnl_pct:.1%} <= {msl:.0%}")
                    self._close_by_pid(pid, cur, "stop_loss")
            elif strat == "whale":
                # Phase BB: gestione whale — TP/SL direzionale + resolution
                cur = self._best_bid(fetcher, pos.asset, pos.shares)
                if cur is None:
                    m = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                    if m is not None and m.get("closed"):
                        exit_price = 1.0 if (pos.outcome and pos.outcome.lower() in ("yes",)) else 0.0
                        self._close_by_pid(pid, exit_price, "resolved")
                    continue
                if cur <= 0.0 or cur >= 1.0:
                    self._close_by_pid(pid, 1.0 if cur >= 0.5 else 0.0, "resolved")
                    continue
                pos.current_price = self._net_liquidation_price(pos, cur)
                pos.current_price_net_of_exit_fee = True
                pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                wtp = STRATEGIES.get("whale", {}).get("take_profit_pct", 0.10)
                wsl = STRATEGIES.get("whale", {}).get("stop_loss_pct", -0.06)
                wsl_abs = STRATEGIES.get("whale", {}).get("stop_loss_abs", -0.03)
                price_delta = cur - pos.entry_price
                if pnl_pct >= wtp:
                    print(f"[WHALE TP] {pos.market_title[:40]} P&L {pnl_pct:.1%} >= {wtp:.0%}")
                    self._close_by_pid(pid, cur, "take_profit")
                elif price_delta <= wsl_abs:
                    # Phase CD: SL assoluto (cent) — robusto a prezzi estremi
                    print(f"[WHALE SL] {pos.market_title[:40]} delta {price_delta:+.3f} (abs SL {wsl_abs:+.3f})")
                    self._close_by_pid(pid, cur, "stop_loss")
                elif pnl_pct <= wsl and price_delta > wsl_abs:
                    print(f"[WHALE SL] {pos.market_title[:40]} P&L {pnl_pct:.1%} <= {wsl:.0%}")
                    self._close_by_pid(pid, cur, "stop_loss")
            elif strat in ("sniper", "theta", "contrarian"):
                # Phase DD/GG/II: gestione direzionale — TP/SL + resolution + trailing
                cur = self._best_bid(fetcher, pos.asset, pos.shares)
                if cur is None:
                    m = fetcher.get_market(pos.condition_id) if pos.condition_id else None
                    if m is not None and m.get("closed"):
                        exit_price = 1.0 if (pos.outcome and pos.outcome.lower() in ("yes",)) else 0.0
                        self._close_by_pid(pid, exit_price, "resolved")
                    continue
                if cur <= 0.0 or cur >= 1.0:
                    self._close_by_pid(pid, 1.0 if cur >= 0.5 else 0.0, "resolved")
                    continue
                pos.current_price = self._net_liquidation_price(pos, cur)
                pos.current_price_net_of_exit_fee = True
                pnl_pct = (cur - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                cfg_s = STRATEGIES.get(strat, {})
                tp = cfg_s.get("take_profit_pct", 0.08)
                sl = cfg_s.get("stop_loss_pct", -0.05)
                sl_abs = cfg_s.get("stop_loss_abs", -0.03)  # Phase CD: SL assoluto (cent)
                price_delta = cur - pos.entry_price
                # Phase CC: trailing stop disabilitato (config trailing_stop_enabled=False)
                if BUDGET.get("trailing_stop_enabled", False) and strat in BUDGET.get("trailing_apply_strategies", []):
                    trail_act = BUDGET.get("trailing_activate_pct", 0.03)
                    trail_pct = BUDGET.get("trailing_stop_pct", -0.03)
                    if pnl_pct >= trail_act:
                        # track peak price per posizione
                        peak = getattr(pos, '_peak_price', pos.entry_price)
                        if cur > peak:
                            pos._peak_price = cur
                            peak = cur
                        trail_pnl = (cur - peak) / peak if peak > 0 else 0
                        if trail_pnl <= trail_pct:
                            print(f"[{strat.upper()} TRAIL] {pos.market_title[:40]} peak {peak:.3f} cur {cur:.3f}")
                            self._close_by_pid(pid, cur, "trailing_stop")
                            continue
                if pnl_pct >= tp:
                    print(f"[{strat.upper()} TP] {pos.market_title[:40]} P&L {pnl_pct:.1%}")
                    self._close_by_pid(pid, cur, "take_profit")
                elif price_delta <= sl_abs:
                    # Phase CD: SL assoluto (cent) — triggera su move reale, non rumore
                    print(f"[{strat.upper()} SL] {pos.market_title[:40]} delta {price_delta:+.3f} (abs SL {sl_abs:+.3f})")
                    self._close_by_pid(pid, cur, "stop_loss")
                elif pnl_pct <= sl and price_delta > sl_abs:
                    print(f"[{strat.upper()} SL] {pos.market_title[:40]} P&L {pnl_pct:.1%}")
                    self._close_by_pid(pid, cur, "stop_loss")

    def _close_by_pid(self, pid: str, exit_price: float, reason: str) -> bool:
        if pid not in self.portfolio.positions:
            return False
        pos = self.portfolio.positions[pid]
        exit_price = max(0.0, min(1.0, exit_price))
        # Phase CI3: taker fee su USCITA per SL/TP/exit; no fee per resolved.
        exit_eff = self._exit_fee_adjusted(pos, exit_price, reason)
        pos.close_reason = reason
        pnl = (exit_eff - pos.entry_price) * pos.shares
        self.portfolio.close_position(pid, exit_eff, datetime.now())
        label = {"resolved": "RISOLTA", "stop_loss": "STOP LOSS",
                 "take_profit": "TAKE PROFIT", "exit": "CHIUSA"}.get(reason, reason)
        outcome_label = "PROFIT" if pnl > 0 else "LOSS"
        fee_note = "" if exit_eff == exit_price else f" (exit_fee -> {exit_eff:.3f})"
        print(f"\n[{pos.strategy.upper()} {label}] {outcome_label}")
        print(f"  Mercato: {pos.market_title[:50]} ({pos.outcome})")
        print(f"  Entry: ${pos.entry_price:.3f} -> Exit: ${exit_price:.3f}{fee_note}")
        print(f"  P&L: ${pnl:.2f} | Cash: ${self.portfolio.cash:.2f}")
        self._log_close_trade(pos, exit_eff, reason)
        self._record_close_risk(pos, pnl, reason)
        self._journal("closed", reason, strategy=pos.strategy or "copy",
                      wallet=pos.source_wallet, position=pos,
                      costs={
                          "exit_fee_price": exit_price - exit_eff,
                          "exit_fee_usdc": pos.shares * (exit_price - exit_eff),
                      })
        self._save_state()
        return True

    def _kelly_size(self, strategy_name: str, base_size: float) -> float:
        """Phase EE: Kelly fractional sizing — ottimizza size basato su WR e payoff."""
        if not BUDGET.get("kelly_enabled", False):
            return base_size
        # WR per strategia dai trade chiusi
        closed = [p for p in self.portfolio.closed_positions if (p.strategy or "copy") == strategy_name]
        if len(closed) < 5:
            return base_size  # sample insufficiente, usa base
        wins = sum(1 for p in closed if p.pnl > 0)
        p = wins / len(closed)
        q = 1 - p
        # payoff b: per harvest/sniper ~ (1-entry)/entry, per direzionale usa TP target
        avg_entry = sum(p.entry_price for p in closed) / len(closed)
        b = (1.0 - avg_entry) / avg_entry if avg_entry > 0 else 1.0
        # Kelly fraction: f = (b*p - q) / b
        if b <= 0:
            return base_size
        kelly_f = (b * p - q) / b
        if kelly_f <= 0:
            return base_size * 0.5  # edge negativo → dimezza
        # Fractional Kelly (1/4) per ridurre volatilità
        kelly_frac = BUDGET.get("kelly_fraction", 0.25) * kelly_f
        kelly_frac = max(BUDGET.get("kelly_min_size", 0.03),
                         min(kelly_frac, BUDGET.get("kelly_max_size", 0.20)))
        kelly_size = self.portfolio.total_value * kelly_frac
        return min(kelly_size, base_size * 1.5)  # max 1.5x base (anti over-bet)

    def _cluster_check(self, strategy_name: str, condition_id: str,
                       event_slug: str, proposed_size: float = 0.0) -> bool:
        """Phase FF: correlation-aware hedging — limita esposizione per evento cluster.
        Returns True se OK aprire, False se cluster saturato."""
        if not BUDGET.get("cluster_cap_pct", 0):
            return True
        cluster_key = event_slug or condition_id
        if not cluster_key:
            return True
        # conta posizioni aperte stesso evento
        same_cluster = [p for p in self.portfolio.positions.values()
                        if (p.event_slug or p.condition_id) == cluster_key]
        max_pos = BUDGET.get("cluster_max_positions", 5)
        if len(same_cluster) >= max_pos:
            print(f"[CLUSTER] {cluster_key[:20]}... saturato ({len(same_cluster)}/{max_pos})")
            return False
        # cap esposizione %
        cluster_deployed = sum(p.size_usdc for p in same_cluster)
        cluster_cap = self.portfolio.total_value * BUDGET.get("cluster_cap_pct", 0.03)
        projected = cluster_deployed + max(0.0, proposed_size)
        if projected > cluster_cap:
            print(f"[CLUSTER] {cluster_key[:20]}... projected ${projected:.0f} > ${cluster_cap:.0f}")
            return False
        return True

    # ------------------------------------------------------------------
    # Phase M: esecuzione opportunita arb/harvest/arb_cross
    # ------------------------------------------------------------------
    def execute_opportunity(self, opp, fetcher) -> bool:
        """Esegue un'opportunita di strategia arb/harvest/arb_cross."""
        strat = opp.strategy
        signal_id = uuid.uuid4().hex
        setattr(opp, "signal_id", signal_id)
        first_asset = (getattr(opp, "assets", None) or [""])[0]
        book = fetcher.get_book(first_asset) if first_asset and fetcher else None
        def reject(reason: str) -> bool:
            self._journal("rejected", reason, strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False

        halt = self._opening_halt_reason(strat)
        if halt:
            self._journal("rejected", halt, strategy=strat, signal_id=signal_id,
                          opp=opp, book=book)
            return False
        if self.portfolio.open_positions_count >= int(
            EXECUTION.get("max_open_positions", BUDGET["max_open_positions"])
        ):
            self._journal("rejected", "max_open_positions", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False
        active_assets = [asset for asset in (opp.assets or []) if asset]
        live_books = [fetcher.get_book(asset) for asset in active_assets]
        if not active_assets or any(
            not item or item.get("best_ask") is None or item.get("best_bid") is None
            for item in live_books
        ):
            return reject("no_executable_two_sided_book")
        live_asks = [float(item["best_ask"]) for item in live_books]
        opp.best_asks = live_asks
        opp.book_sizes = [float(item.get("ask_size", 0) or 0) for item in live_books]
        opp.spread_cents = [
            (float(item["best_ask"]) - float(item["best_bid"])) * 100
            for item in live_books
        ]
        if strat in ("arb_binary", "arb_cross"):
            opp.cost_per_share = sum(live_asks)
            opp.max_fill_size = min(
                ask * size for ask, size in zip(live_asks, opp.book_sizes)
            )
        else:
            opp.cost_per_share = live_asks[0]
            opp.max_fill_size = live_asks[0] * opp.book_sizes[0]
        if self._condition_is_open(opp.condition_id):
            self._journal("rejected", "duplicate_open_condition", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False
        if any(self.has_asset(asset) for asset in (opp.assets or []) if asset):
            self._journal("rejected", "duplicate_open_asset", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False
        if opp.condition_id in self.blocked_conditions:
            market = fetcher.get_market(opp.condition_id) if fetcher else None
            if market and market.get("closed"):
                self.blocked_conditions.pop(opp.condition_id, None)
                self._save_safety_state()
                reason = "condition_resolved"
            else:
                reason = "condition_blocked_after_stop_loss"
            self._journal("rejected", reason, strategy=strat, signal_id=signal_id,
                          opp=opp, book=book)
            return False
        if self._risk_factor() <= 0.0:
            self._journal("rejected", "legacy_equity_floor", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False
        # Phase CI2 (Guida 2: liquidity ≥$50K per uscite pulite). Controllo hard
        # anche su opp.market_volume (popolato in scan da gamma volumeNum).
        min_mv = float(STRATEGIES.get(strat, {}).get("min_market_volume_usdc", 0.0) or 0.0)
        if min_mv > 0 and float(getattr(opp, 'market_volume', 0.0) or 0.0) < min_mv:
            print(f"[SKIP] Liquidità mercato {getattr(opp,'market_volume',0):.0f} < "
                  f"{min_mv:.0f} per {strat}: {opp.market_title[:40]}")
            return reject("market_volume_below_minimum")
        # cap per strategia: max posizioni simultanee
        cfg = STRATEGIES.get(strat, {})
        max_pos = cfg.get("max_positions", 99)
        current_n = sum(1 for p in self.portfolio.positions.values()
                        if (p.strategy or "copy") == strat)
        if current_n >= max_pos:
            return reject("strategy_position_cap")
        # cap per strategia (soft): non superare cap_pct deployato
        avail = self._strategy_available(strat)
        max_single = self._max_single_for(strat)
        # size: min(max_single, avail, opportunity max_fill_size)
        # sizing compounding: usa il sizing base come upper bound
        size = self._sizing_compounding()
        # Kelly/compounding non vengono applicati durante la validazione.
        if self.execution_mode != "paper_validation":
            size = self._kelly_size(strat, size)
        size = min(size, max_single, avail, opp.max_fill_size)
        if size < BUDGET["min_position_size"]:
            return reject("sizing_below_minimum")
        # equity floor block
        if self._risk_factor() <= 0.0:
            return reject("legacy_equity_floor")
        # Phase FF: cluster hedging — limita esposizione per evento
        event_slug = getattr(opp, 'event_slug', '') or ''
        if not self._cluster_check(
            strat, opp.condition_id, event_slug, proposed_size=size
        ):
            self._journal("rejected", "event_exposure_limit", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False
        # dedup per condition_id / asset
        now = datetime.now()
        dedup = BUDGET.get("dedup_window_sec", 3600)
        last = self.recent_opens.get(opp.condition_id)
        if last and (now - last).total_seconds() < dedup:
            self._journal("rejected", "recent_condition_cooldown", strategy=strat,
                          signal_id=signal_id, opp=opp, book=book)
            return False

        if strat == "arb_binary":
            return self._open_arb_binary(opp, size, fetcher)
        if strat == "harvest":
            return self._open_harvest(opp, size, fetcher)
        if strat == "arb_cross":
            return self._open_arb_cross(opp, size, fetcher)
        if strat == "momentum":
            return self._open_momentum(opp, size, fetcher)
        if strat == "whale":
            return self._open_whale(opp, size, fetcher)
        if strat == "sniper":
            return self._open_directional(opp, size, fetcher, "sniper")
        if strat == "theta":
            return self._open_directional(opp, size, fetcher, "theta")
        if strat == "contrarian":
            return self._open_directional(opp, size, fetcher, "contrarian")
        return reject("unknown_strategy")

    def _open_arb_binary(self, opp, size: float, fetcher) -> bool:
        # compriamo YES+NO equal shares; position = bundle con entry_price=cost,
        # asset = token_yes (riferimento), pair_id = condition_id
        cost = opp.cost_per_share  # ask_yes + ask_no
        if cost <= 0 or cost >= 1:
            return False
        # slippage simulato su entrambi i leg
        eff_cost = cost
        shares = size / eff_cost
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,
            outcome="YES+NO (arb)",
            entry_price=eff_cost,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=cost,
        )
        position.strategy = "arb_binary"
        position.pair_id = opp.condition_id
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self._save_recent_opens()
        print(f"\n[ARB BINARY APERTO] {opp.market_title[:50]}")
        print(f"  Bundle YES+NO @ {cost:.4f} (eff {eff_cost:.4f}) | Size ${size:.2f} | "
              f"Shares {shares:.1f} | Profit/share ${opp.profit_per_share:.4f}")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _open_harvest(self, opp, size: float, fetcher) -> bool:
        ask = opp.cost_per_share  # ask favorito
        if ask <= 0 or ask >= 1:
            return False
        eff = ask
        fee_frac = taker_fee_fraction(opp.category, eff)
        eff_fee = min(0.999, eff * (1 + fee_frac))
        shares = size / eff_fee
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,
            outcome=opp.outcomes[0],
            entry_price=eff_fee,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=self._best_bid(fetcher, opp.assets[0]) or ask,
        )
        position.strategy = "harvest"
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self.recent_opens[opp.assets[0]] = datetime.now()
        self._save_recent_opens()
        print(f"\n[HARVEST APERTO] {opp.market_title[:50]} ({opp.outcomes[0]} @ {ask:.3f})")
        print(f"  Size ${size:.2f} | Shares {shares:.1f} | Payout target $1 | "
              f"APR {opp.score*100:.0f}%")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _open_arb_cross(self, opp, size: float, fetcher) -> bool:
        cost = opp.cost_per_share  # sum ask YES_i
        if cost <= 0 or cost >= 1:
            return False
        eff_cost = cost
        shares = size / eff_cost
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,  # event slug
            outcome=f"{len(opp.assets)}-way YES basket",
            entry_price=eff_cost,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=cost,
        )
        position.strategy = "arb_cross"
        position.pair_id = opp.condition_id
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self._save_recent_opens()
        print(f"\n[ARB CROSS APERTO] {opp.market_title[:50]}")
        print(f"  Bundle {len(opp.assets)} YES @ sum {cost:.4f} (eff {eff_cost:.4f}) | "
              f"Size ${size:.2f} | Profit/share ${opp.profit_per_share:.4f}")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _open_momentum(self, opp, size: float, fetcher) -> bool:
        ask = opp.cost_per_share
        if ask <= 0 or ask >= 1:
            return False
        eff = ask
        fee_frac = taker_fee_fraction(opp.category, eff)
        eff_fee = min(0.999, eff * (1 + fee_frac))
        shares = size / eff_fee
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,
            outcome=opp.outcomes[0],
            entry_price=eff_fee,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=self._best_bid(fetcher, opp.assets[0]) or ask,
        )
        position.strategy = "momentum"
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self.recent_opens[opp.assets[0]] = datetime.now()
        self._save_recent_opens()
        mtp = STRATEGIES.get("momentum", {}).get("take_profit_pct", 0.06)
        msl = STRATEGIES.get("momentum", {}).get("stop_loss_pct", -0.05)
        print(f"\n[MOMENTUM APERTO] {opp.market_title[:50]} ({opp.outcomes[0]} @ {ask:.3f})")
        print(f"  Size ${size:.2f} | Shares {shares:.1f} | TP {mtp:.0%} / SL {msl:.0%} | "
              f"Move score {opp.score:.3f}")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _open_whale(self, opp, size: float, fetcher) -> bool:
        # Phase BB: seguito ingresso whale — compra stesso outcome della whale
        ask = opp.cost_per_share
        if ask <= 0 or ask >= 1:
            return False
        eff = ask
        fee_frac = taker_fee_fraction(opp.category, eff)
        eff_fee = min(0.999, eff * (1 + fee_frac))
        shares = size / eff_fee
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,
            outcome=opp.outcomes[0],
            entry_price=eff_fee,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=self._best_bid(fetcher, opp.assets[0]) or ask,
        )
        position.strategy = "whale"
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self.recent_opens[opp.assets[0]] = datetime.now()
        self._save_recent_opens()
        wtp = STRATEGIES.get("whale", {}).get("take_profit_pct", 0.10)
        wsl = STRATEGIES.get("whale", {}).get("stop_loss_pct", -0.06)
        print(f"\n[WHALE APERTO] {opp.market_title[:50]} ({opp.outcomes[0]} @ {ask:.3f})")
        print(f"  Size ${size:.2f} | Shares {shares:.1f} | TP {wtp:.0%} / SL {wsl:.0%} | "
              f"Whale score {opp.score:.2f}")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _open_directional(self, opp, size: float, fetcher, strategy_name: str) -> bool:
        """Apre posizione direzionale generica per sniper/theta/contrarian."""
        ask = opp.cost_per_share
        if ask <= 0 or ask >= 1:
            return False
        eff = ask
        fee_frac = taker_fee_fraction(opp.category, eff)
        eff_fee = min(0.999, eff * (1 + fee_frac))
        shares = size / eff_fee
        cfg = STRATEGIES.get(strategy_name, {})
        tp = cfg.get("take_profit_pct", 0.08)
        sl = cfg.get("stop_loss_pct", -0.05)
        position = Position(
            position_id=str(uuid.uuid4()),
            market_title=opp.market_title,
            market_slug="",
            condition_id=opp.condition_id,
            outcome=opp.outcomes[0],
            entry_price=eff_fee,
            size_usdc=size,
            shares=shares,
            entry_time=datetime.now(),
            source_wallet="",
            asset=opp.assets[0],
            run_id=self.run_id,
            signal_id=getattr(opp, "signal_id", ""),
            event_id=getattr(opp, "event_id", ""),
            event_slug=getattr(opp, "event_slug", ""),
            event_title=getattr(opp, "event_title", ""),
            category=opp.category,
            current_price=self._best_bid(fetcher, opp.assets[0]) or ask,
        )
        position.strategy = strategy_name
        self.portfolio.add_position(position)
        self._log_strategy_trade(position, opp)
        self.recent_opens[opp.condition_id] = datetime.now()
        self.recent_opens[opp.assets[0]] = datetime.now()
        self._save_recent_opens()
        print(f"\n[{strategy_name.upper()} APERTO] {opp.market_title[:50]} ({opp.outcomes[0]} @ {ask:.3f})")
        print(f"  Size ${size:.2f} | Shares {shares:.1f} | TP {tp:.0%} / SL {sl:.0%} | Score {opp.score:.3f}")
        print(f"  Cash: ${self.portfolio.cash:.2f}")
        self._save_state()
        return True

    def _log_strategy_trade(self, position: Position, opp):
        trade_log = {
            "timestamp": utc_now_iso(),
            "run_id": self.run_id,
            "signal_id": position.signal_id,
            "strategy": position.strategy,
            "condition_id": opp.condition_id,
            "event_id": position.event_id,
            "event_slug": position.event_slug,
            "event_title": position.event_title,
            "position_id": position.position_id,
            "asset": position.asset,
            "market": position.market_title,
            "outcome": position.outcome,
            "side": "BUY",
            "entry_price": position.entry_price,
            "size": position.size_usdc,
            "profit_per_share": opp.profit_per_share,
            "n_legs": len(opp.assets),
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
        asks = getattr(opp, "best_asks", []) or []
        sizes = getattr(opp, "book_sizes", []) or []
        spreads = getattr(opp, "spread_cents", []) or []
        ask = asks[0] if asks else None
        spread = (spreads[0] / 100.0) if spreads else None
        self._journal(
            "opened", "paper_validation", strategy=position.strategy,
            signal_id=position.signal_id, opp=opp, position=position,
            book={
                "best_ask": ask,
                "best_bid": (ask - spread) if ask is not None and spread is not None else None,
                "ask_size": sizes[0] if sizes else None,
                "bid_size": None,
            },
        )

    def _log_close_trade(self, position: Position, exit_price: float, reason: str):
        """Phase AA: logga chiusura trade con P&L completo per dashboard."""
        pnl = (exit_price - position.entry_price) * position.shares
        pnl_pct = ((exit_price - position.entry_price) / position.entry_price * 100) if position.entry_price > 0 else 0
        hold_sec = (datetime.now() - position.entry_time).total_seconds() if position.entry_time else 0
        close_log = {
            "timestamp": utc_now_iso(),
            "run_id": position.run_id or self.run_id,
            "signal_id": position.signal_id,
            "strategy": position.strategy or "copy",
            "condition_id": position.condition_id,
            "event_id": position.event_id,
            "event_slug": position.event_slug,
            "event_title": position.event_title,
            "position_id": position.position_id,
            "asset": position.asset,
            "market": position.market_title,
            "outcome": position.outcome,
            "side": "SELL",
            "reason": reason,
            "entry_price": round(position.entry_price, 4),
            "exit_price": round(exit_price, 4),
            "size": round(position.size_usdc, 2),
            "shares": round(position.shares, 2),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
            "win": pnl > 0,
            "hold_sec": round(hold_sec, 0),
            "source_wallet": position.source_wallet or "",
            "category": position.category or "",
        }
        try:
            if self.trades_log.exists():
                with open(self.trades_log, 'r') as f:
                    logs = json.load(f)
            else:
                logs = []
            logs.append(close_log)
            logs = logs[-500:]  # cap 500 trade
            with open(self.trades_log, 'w') as f:
                json.dump(logs, f, indent=2)
        except Exception as e:
            print(f"[ERRORE] Salvataggio close trade log: {e}")

    # ------------------------------------------------------------------
    # Summary / metriche
    # ------------------------------------------------------------------
    def _cluster_exposure(self) -> Dict[str, float]:
        """Phase FF: ritorna event_slug -> total USDC deployato per cluster."""
        clusters: Dict[str, float] = {}
        for p in self.portfolio.positions.values():
            key = p.event_slug or p.condition_id or "unknown"
            clusters[key] = clusters.get(key, 0) + p.size_usdc
        return dict(sorted(clusters.items(), key=lambda kv: kv[1], reverse=True)[:10])

    def get_shadow_summary(self) -> Dict:
        open_positions = list(self.shadow_positions.values())
        closed_positions = list(self.shadow_closed_positions)
        realized = sum(pos.pnl for pos in closed_positions)
        unrealized = sum(pos.pnl for pos in open_positions)
        wins = sum(1 for pos in closed_positions if pos.pnl > 0)
        if not self.run_domains_frozen:
            self._load_run_domain_policy()
        domains = list(self.run_intended_domains)
        evaluation = evaluate_shadow_run(
            closed_positions,
            self.run_id,
            intended_domains=domains,
            bootstrap_iterations=2000,
            max_drawdown_override=self.shadow_max_drawdown,
        )
        metrics = evaluation.get("metrics", {})
        ci_lower = metrics.get("bootstrap_ci95_lower_ev")
        if ci_lower in (float("inf"), float("-inf")):
            ci_lower = None
        shadow_actions: Counter = Counter()
        shadow_rejection_reasons: Counter = Counter()
        if self.shadow_journal.exists():
            try:
                with open(self.shadow_journal, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if row.get("run_id") != self.run_id:
                            continue
                        action = str(row.get("action", "unknown"))
                        shadow_actions[action] += 1
                        if action == "rejected":
                            shadow_rejection_reasons[
                                str(row.get("reason") or "unknown")
                            ] += 1
            except OSError:
                pass
        return {
            "enabled": self._shadow_enabled(),
            "run_id": self.run_id,
            "state_saved_at": self.shadow_state_saved_at,
            "state_version": 2,
            "initial_capital": self.shadow_initial_capital,
            "cash": self.shadow_cash,
            "equity": self._shadow_total_value(),
            "deployed_usdc": sum(pos.size_usdc for pos in open_positions),
            "max_open_positions": int(
                EXECUTION.get("shadow_max_open_positions", 2)
            ),
            "peak_equity": self.shadow_peak_equity,
            "max_drawdown": self.shadow_max_drawdown,
            "halt_reason": self.shadow_halt_reason,
            "loss_streak": self.shadow_loss_streak,
            "blocked_conditions": sorted(self.shadow_blocked_conditions),
            "journal_actions": dict(shadow_actions),
            "rejected_candidates": shadow_actions.get("rejected", 0),
            "rejection_reasons": dict(
                shadow_rejection_reasons.most_common(10)
            ),
            "legacy_unconstrained": self.shadow_legacy_unconstrained,
            "domain_policy_frozen": self.run_domains_frozen,
            "open_positions": len(open_positions),
            "closed_positions": len(closed_positions),
            "winning_trades": wins,
            "losing_trades": len(closed_positions) - wins,
            "win_rate": (
                wins / len(closed_positions) * 100 if closed_positions else 0.0
            ),
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": realized + unrealized,
            "distinct_events": metrics.get("distinct_events", 0),
            "elapsed_days": metrics.get("elapsed_days", 0.0),
            "ev_per_trade": metrics.get("ev_per_trade", 0.0),
            "bootstrap_ci95_lower_ev": ci_lower,
            "intended_domains": domains,
            "checks": evaluation.get("checks", {}),
            "eligible_for_independent_paper": evaluation.get(
                "eligible_for_independent_paper", False
            ),
            "real_money_authorized": False,
            "validation_stage": "shadow",
        }

    def get_portfolio_summary(self) -> Dict:
        unrealized_pnl = sum(pos.pnl for pos in self.portfolio.positions.values())
        realized_pnl = sum(pos.pnl for pos in self.portfolio.closed_positions)

        winning_trades = sum(1 for pos in self.portfolio.closed_positions if pos.pnl > 0)
        losing_trades = sum(1 for pos in self.portfolio.closed_positions if pos.pnl <= 0)
        total_closed = len(self.portfolio.closed_positions)
        win_rate = (winning_trades / total_closed * 100) if total_closed > 0 else 0

        # Phase M: breakdown per strategia (aperte + chiuse + P&L realizzato)
        by_strategy = {}
        for strat in ("copy", "arb_binary", "harvest", "arb_cross", "momentum", "whale", "sniper", "theta", "contrarian", "other"):
            open_p = [p for p in self.portfolio.positions.values() if (p.strategy or "copy") == strat]
            closed_p = [p for p in self.portfolio.closed_positions if (p.strategy or "copy") == strat]
            if not open_p and not closed_p and strat != "copy":
                continue
            rl = sum(p.pnl for p in closed_p)
            ur = sum(p.pnl for p in open_p)
            wc = sum(1 for p in closed_p if p.pnl > 0)
            by_strategy[strat] = {
                "open": len(open_p), "closed": len(closed_p),
                "realized_pnl": rl, "unrealized_pnl": ur,
                "win_rate": (wc / len(closed_p) * 100) if closed_p else 0.0,
            }

        # Phase K: sizing tier corrente per monitoring
        sizing_frac, sizing_desc = self._sizing_tier()
        peak = getattr(self, "peak_equity", self.portfolio.total_value)
        dd_pct = ((peak - self.portfolio.total_value) / peak) if peak > 0 else 0.0

        circuit_reason = self._evaluate_equity_halts()
        effective_halt = circuit_reason or self.halt_reason
        if not effective_halt and self.execution_mode == "observe":
            effective_halt = "Quarantena: nuove aperture disabilitate"

        return {
            "strategy_mode": self.strategy_mode,
            "execution_mode": self.execution_mode,
            "halt_reason": effective_halt,
            "run_id": self.run_id,
            "state_saved_at": self.state_saved_at,
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
            "by_strategy": by_strategy,
            "sizing_tier": {"frac": sizing_frac, "desc": sizing_desc},
            "peak_equity": peak,
            "drawdown_pct": dd_pct,
            "best_trade": self._get_best_trade(),
            "worst_trade": self._get_worst_trade(),
            # Phase CC-II: metriche avanzate dashboard v2
            "max_open_positions": BUDGET["max_open_positions"],
            "reserve_ratio": BUDGET["reserve_ratio"],
            "available_cash": self._available_cash(),
            "reserve_cash": self.portfolio.initial_capital * BUDGET["reserve_ratio"],
            "risk_factor": self._risk_factor(),
            "trailing_stop_enabled": BUDGET.get("trailing_stop_enabled", False),
            "kelly_enabled": BUDGET.get("kelly_enabled", False),
            "cluster_cap_pct": BUDGET.get("cluster_cap_pct", 0),
            "active_strategies": [
                s for s in STRATEGIES
                if STRATEGIES[s].get("scan_enabled", False) and s != "value"
            ],
            "paper_enabled_strategies": [
                s for s in STRATEGIES
                if STRATEGIES[s].get("paper_enabled", False)
            ],
            "quarantined_strategies": sorted(self.quarantined_strategies),
            "blocked_conditions": sorted(self.blocked_conditions),
            "deployed_usdc": sum(p.size_usdc for p in self.portfolio.positions.values()),
            # cluster exposure: event_slug -> total deployed
            "cluster_exposure": self._cluster_exposure(),
            "cluster_labels": {
                (p.event_slug or p.condition_id or "unknown"):
                    (p.event_title or p.event_slug or p.market_title)
                for p in self.portfolio.positions.values()
            },
            "shadow_validation": self.get_shadow_summary(),
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
                "timestamp": utc_now_iso(),
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
            "timestamp": utc_now_iso(),
            "run_id": position.run_id or self.run_id,
            "signal_id": position.signal_id,
            "strategy": self.strategy_mode,
            "wallet_address": wallet_address,
            "num_holders": num_holders,
            "position_id": position.position_id,
            "asset": position.asset,
            "condition_id": position.condition_id,
            "event_id": position.event_id,
            "event_slug": position.event_slug,
            "event_title": position.event_title,
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
            saved_at = utc_now_iso()
            state = {
                "state_version": 3,
                "run_id": self.run_id,
                "execution_mode": self.execution_mode,
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
                "saved_at": saved_at,
            }
            self._atomic_write_json(self.state_file, state)
            self.state_saved_at = saved_at
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
            "run_id": pos.run_id,
            "signal_id": pos.signal_id,
            "event_id": pos.event_id,
            "event_slug": pos.event_slug,
            "event_title": pos.event_title,
            "asset": pos.asset,
            "category": pos.category,
            "fees_enabled": pos.fees_enabled,
            "fee_rate": pos.fee_rate,
            "fee_exponent": pos.fee_exponent,
            "fee_source": pos.fee_source,
            "entry_best_bid": pos.entry_best_bid,
            "entry_best_ask": pos.entry_best_ask,
            "source_trade_price": pos.source_trade_price,
            "source_trade_size": pos.source_trade_size,
            "last_mark_at": (
                pos.last_mark_at.isoformat() if pos.last_mark_at else None
            ),
            "outcome": pos.outcome,
            "entry_price": pos.entry_price,
            "size_usdc": pos.size_usdc,
            "shares": pos.shares,
            "entry_time": pos.entry_time.isoformat(),
            "source_wallet": pos.source_wallet,
            "strategy": getattr(pos, "strategy", "copy"),
            "pair_id": getattr(pos, "pair_id", ""),
            "current_price": pos.current_price,
            "current_price_net_of_exit_fee": pos.current_price_net_of_exit_fee,
            "exit_price": pos.exit_price,
            "exit_time": pos.exit_time.isoformat() if pos.exit_time else None,
            "is_closed": pos.is_closed,
            "close_reason": pos.close_reason,
        }

    def _deserialize_position(self, data: Dict) -> Position:
        pos = Position(
            position_id=data["position_id"],
            market_title=data["market_title"],
            market_slug=data.get("market_slug", ""),
            condition_id=data.get("condition_id", ""),
            outcome=data.get("outcome", ""),
            entry_price=data["entry_price"],
            size_usdc=data["size_usdc"],
            shares=data["shares"],
            entry_time=datetime.fromisoformat(data["entry_time"]),
            source_wallet=data["source_wallet"],
            asset=data.get("asset", ""),
            run_id=data.get("run_id", self.run_id),
            signal_id=data.get("signal_id", f"legacy-{data.get('position_id', '')}"),
            event_id=data.get("event_id", ""),
            event_slug=data.get("event_slug", ""),
            event_title=data.get("event_title", ""),
            category=data.get("category", ""),
            fees_enabled=data.get("fees_enabled"),
            fee_rate=data.get("fee_rate"),
            fee_exponent=float(data.get("fee_exponent", 1.0) or 1.0),
            fee_source=data.get("fee_source", "legacy_category_fallback"),
            entry_best_bid=data.get("entry_best_bid"),
            entry_best_ask=data.get("entry_best_ask"),
            source_trade_price=data.get("source_trade_price"),
            source_trade_size=data.get("source_trade_size"),
            last_mark_at=(
                datetime.fromisoformat(data["last_mark_at"])
                if data.get("last_mark_at") else None
            ),
            current_price=data.get("current_price", data["entry_price"]),
            current_price_net_of_exit_fee=bool(
                data.get("current_price_net_of_exit_fee", False)
            ),
            exit_price=data.get("exit_price"),
            exit_time=datetime.fromisoformat(data["exit_time"]) if data.get("exit_time") else None,
            is_closed=data.get("is_closed", False),
            close_reason=data.get("close_reason", ""),
        )
        try:
            pos.strategy = data.get("strategy", "copy")
            pos.pair_id = data.get("pair_id", "")
        except Exception:
            pass
        # State v2 salvava il best bid lordo. Se i metadati fee sono noti,
        # convertilo una sola volta nel ricavo netto liquidabile. Questo preserva
        # run e posizioni aperte durante il deploy della migrazione.
        if (
            not pos.is_closed
            and not pos.current_price_net_of_exit_fee
            and pos.fees_enabled is not None
        ):
            pos.current_price = self._net_liquidation_price(pos, pos.current_price)
            pos.current_price_net_of_exit_fee = True
        return pos

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
            self.state_saved_at = state.get("saved_at")
            stored_run = state.get("run_id")
            if stored_run:
                self.run_id = str(stored_run)
            elif self.state_saved_at:
                compact = "".join(ch for ch in self.state_saved_at if ch.isdigit())[:14]
                self.run_id = f"legacy-{compact or 'unknown'}"
            self.portfolio.cash = state["cash"]
            self.baseline_done = state.get("baseline_done", False)
            self.baseline_assets = set(state.get("baseline_assets", []))

            for pid, pos_data in state.get("positions", {}).items():
                self.portfolio.positions[pid] = self._deserialize_position(pos_data)

            for pos_data in state.get("closed_positions", []):
                self.portfolio.closed_positions.append(self._deserialize_position(pos_data))

            # La v2 calcolava correttamente exit_price/P&L netti, ma accreditava
            # al cash il current_price lordo. Ricostruire e' sicuro soltanto per
            # run interamente fee-v4 (metadati noti su ogni posizione) e con
            # almeno una chiusura; gli snapshot legacy misti restano invariati.
            all_positions = (
                list(self.portfolio.positions.values())
                + self.portfolio.closed_positions
            )
            state_version = int(state.get("state_version", 0) or 0)
            can_rebuild_v2_cash = bool(
                state_version == 2
                and self.portfolio.closed_positions
                and all_positions
                and all(pos.fees_enabled is not None for pos in all_positions)
                and all(
                    pos.exit_price is not None
                    for pos in self.portfolio.closed_positions
                )
            )
            if can_rebuild_v2_cash:
                initial = float(
                    state.get("initial_capital", self.portfolio.initial_capital)
                )
                expected_cash = initial
                expected_cash -= sum(
                    pos.size_usdc for pos in all_positions
                )
                expected_cash += sum(
                    pos.shares * pos.exit_price
                    for pos in self.portfolio.closed_positions
                )
                if abs(self.portfolio.cash - expected_cash) > 1e-9:
                    print(
                        "[MIGRATION] Cash v2 lordo corretto: "
                        f"${self.portfolio.cash:.6f} -> ${expected_cash:.6f}"
                    )
                    self.portfolio.cash = expected_cash

            print(f"[SIMULATOR] Stato ripristinato: ${self.portfolio.cash:.2f} cash, "
                  f"{len(self.portfolio.positions)} aperte, "
                  f"{len(self.portfolio.closed_positions)} chiuse")

        except Exception as e:
            print(f"[ERRORE] Caricamento stato: {e}")


if __name__ == "__main__":
    sim = PaperTradingSimulator(initial_capital=300.0)
    sim.print_portfolio_summary()
