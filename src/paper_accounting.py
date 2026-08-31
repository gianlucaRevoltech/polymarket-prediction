"""Read-only accounting for persisted paper/shadow ledgers (never places orders)."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from time_utils import parse_utc


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def journal_rows(path: Path, run_id: str) -> tuple[list[dict], list[str]]:
    rows, errors = [], []
    try:
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("record non-object")
                    if row.get("run_id") == run_id:
                        rows.append(row)
                except (ValueError, TypeError):
                    errors.append(f"journal riga {index} invalida")
    except FileNotFoundError:
        pass
    except OSError as exc:
        errors.append(str(exc))
    return rows, errors


def number(value, name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} mancante/non numerico")
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name} non numerico") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} non finito")
    return value


def position_pnl(position: dict, closed: bool = True) -> float:
    entry = number(position.get("entry_price"), "entry_price")
    shares = number(position.get("shares"), "shares")
    price = number(position.get("exit_price" if closed else "current_price"), "mark/exit")
    if shares <= 0 or entry <= 0 or price < 0:
        raise ValueError("prezzi/shares invalidi")
    if not closed and position.get("current_price_net_of_exit_fee") is not True:
        raise ValueError("mark non certificato netto fee")
    return (price - entry) * shares


def ledger_metrics(state: dict, run_id: str) -> dict:
    errors, values, trades = [], [], []
    groups = {key: Counter() for key in ("wallet", "event", "domain")}
    positives = {key: Counter() for key in groups}
    reasons = Counter()
    result = {"available": bool(state), "quality_errors": errors, "closed_trades": 0,
              "open_positions": 0, "realized_pnl": None, "unrealized_pnl": None,
              "net_pnl": None, "equity": None, "cash": None, "reconciled": False}
    if not state:
        errors.append("ledger assente/non valido")
        return result
    if state.get("run_id") != run_id:
        errors.append("ledger di un altro run")
    opened, closed = state.get("positions"), state.get("closed_positions")
    if not isinstance(opened, dict) or not isinstance(closed, list):
        errors.append("positions/closed_positions non validi")
        return result
    seen = set()
    for is_closed, positions in ((False, list(opened.values())), (True, closed)):
        for pos in positions:
            try:
                if not isinstance(pos, dict):
                    raise ValueError("posizione non-object")
                pid = pos.get("position_id")
                if not pid or pid in seen:
                    raise ValueError("position_id assente/duplicato")
                seen.add(pid)
                if pos.get("run_id", run_id) != run_id:
                    raise ValueError("posizione di altro run")
                pnl = position_pnl(pos, is_closed)
                size = number(pos.get("size_usdc"), "size_usdc")
                shares = number(pos.get("shares"), "shares")
                entry = number(pos.get("entry_price"), "entry_price")
                if size <= 0 or abs(size - shares * entry) > 1e-6:
                    raise ValueError("size/shares/entry non riconciliati")
                values.append((is_closed, size, pnl, shares * number(
                    pos.get("exit_price" if is_closed else "current_price"), "price")))
                if is_closed:
                    keys = {"wallet": str(pos.get("source_wallet") or "unknown"),
                            "event": str(pos.get("event_slug") or pos.get("condition_id") or "unknown"),
                            "domain": str(pos.get("category") or "other")}
                    for key, label in keys.items():
                        groups[key][label] += pnl
                        positives[key][label] += max(0, pnl)
                    reasons[str(pos.get("close_reason") or "unknown")] += 1
                    trades.append(dict(pos, pnl=pnl))
            except ValueError as exc:
                errors.append(str(exc))
    result.update(closed_trades=len(closed), open_positions=len(opened), close_reasons=dict(reasons))
    positive_total = sum(max(0, row["pnl"]) for row in trades)
    for key in groups:
        result[f"{key}_pnl"] = dict(groups[key])
        result[f"max_positive_{key}_share"] = (
            max(positives[key].values(), default=0) / positive_total if positive_total else None)
    try:
        initial = number(state.get("initial_capital"), "initial_capital")
        cash = number(state.get("cash"), "cash")
        realized = sum(pnl for closed_flag, _, pnl, _ in values if closed_flag)
        unrealized = sum(pnl for closed_flag, _, pnl, _ in values if not closed_flag)
        expected_cash = initial - sum(size for _, size, _, _ in values) + sum(
            proceeds for closed_flag, _, _, proceeds in values if closed_flag)
        equity = cash + sum(mark for closed_flag, _, _, mark in values if not closed_flag)
        if abs(expected_cash - cash) > 1e-6 or abs(equity - initial - realized - unrealized) > 1e-6:
            errors.append("cash/equity/ledger non riconciliati")
        result.update(cash=cash, expected_cash=expected_cash, initial_capital=initial)
        if not errors:
            result.update(realized_pnl=realized, unrealized_pnl=unrealized,
                          net_pnl=realized + unrealized, equity=equity, reconciled=True)
    except ValueError as exc:
        errors.append(str(exc))
    result["closed_records"] = trades
    return result


def execution_fees(rows: list[dict], state: dict) -> dict:
    """Only simulated executions count; estimates never become incurred fees."""
    positions = list((state.get("positions") or {}).values()) + (state.get("closed_positions") or [])
    by_id = {p.get("position_id"): p for p in positions if isinstance(p, dict)}
    by_signal = {p.get("signal_id"): p for p in positions if isinstance(p, dict) and p.get("signal_id")}
    seen, errors, total = {}, [], 0.0
    for row in rows:
        action = row.get("decision")
        if action not in {"opened", "closed"}:
            continue
        pos = by_id.get(row.get("position_id")) or by_signal.get(row.get("signal_id"))
        if pos is None:
            errors.append("esecuzione journal senza posizione ledger")
            continue
        key = (pos["position_id"], action)
        try:
            field = "fee_usdc" if action == "opened" else "exit_fee_usdc"
            fee = number((row.get("costs") or {}).get(field), field)
            if fee < -1e-9:
                raise ValueError("fee negativa")
            if key in seen:
                if abs(seen[key] - fee) > 1e-9:
                    raise ValueError("fee duplicata discordante")
                continue
            seen[key] = fee
            total += max(0, fee)
        except (ValueError, AttributeError) as exc:
            errors.append(str(exc))
    for pid, pos in by_id.items():
        required = ("opened", "closed") if pos.get("is_closed") else ("opened",)
        for action in required:
            if (pid, action) not in seen:
                errors.append(f"fee {action} assente per {pid}")
    return {"fees_usdc": total if not errors else None, "fee_quality_errors": errors}


def economic_report(state: dict, rows: list[dict], manifest: dict, max_drawdown=None) -> dict:
    # Keep pure accounting importable by standalone audits without importing
    # config.py (which creates runtime directories on import).
    from validation import evaluate_copy_run

    metrics = ledger_metrics(state, str(manifest.get("run_id") or ""))
    metrics.update(execution_fees(rows, state))
    metrics.update(paper_experimental=manifest.get("execution_mode") == "paper_validation",
                   edge_demonstrated=False, real_money_authorized=False)
    trades = []
    try:
        for row in metrics.pop("closed_records", []):
            entry_time, exit_time = parse_utc(row.get("entry_time")), parse_utc(row.get("exit_time"))
            if not entry_time or not exit_time:
                raise ValueError("timestamp trade mancante")
            trades.append(SimpleNamespace(**dict(row, entry_time=entry_time.replace(tzinfo=None),
                                                exit_time=exit_time.replace(tzinfo=None))))
        if metrics["reconciled"] and not metrics["fee_quality_errors"]:
            evaluation = evaluate_copy_run(trades, manifest["run_id"],
                                           intended_domains=manifest.get("intended_domains", []),
                                           max_drawdown_override=max_drawdown)
            # JSON cannot represent infinities (empty sample CI).
            def json_safe(value):
                if isinstance(value, float) and not math.isfinite(value):
                    return None
                if isinstance(value, dict):
                    return {k: json_safe(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [json_safe(v) for v in value]
                return value
            metrics["promotion"] = json_safe(evaluation)
    except (ValueError, TypeError) as exc:
        metrics["quality_errors"].append(str(exc))
    return metrics
