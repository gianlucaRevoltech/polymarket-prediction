"""Shared read-only technical checks for CLI and dashboard."""
from __future__ import annotations

import os
from pathlib import Path

from paper_accounting import journal_rows, ledger_metrics, read_json, number, execution_fees
from run_manifest import load_run_manifest, mode_drift, validate_cohort
from runtime_contract import RUNTIME_VERSION, activation, feed_readiness, git_commit
from time_utils import age_seconds, utc_now_iso


def pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        if pid <= 1:
            return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def cohort_identity(payload: dict) -> tuple:
    wallets = payload.get("wallets", [])
    return (
        sorted(str(d).lower() for d in payload.get("intended_domains", [])),
        sorted((str(w.get("address", "")).lower(),
                tuple(sorted(str(d).lower() for d in w.get("allowed_domains", []))))
               for w in wallets if isinstance(w, dict)),
        payload.get("cohort_source_sha256"),
    )


def safety_reasons(data: Path, run_id: str) -> list[str]:
    reasons = []
    for name in ("safety_state.json", "shadow_state.json", "daily_halt.json"):
        path = data / name
        state = read_json(path)
        if path.exists() and not state:
            reasons.append(f"{name}: file non valido")
            continue
        if state.get("run_id") not in (None, run_id):
            reasons.append(f"{name}: run discordante")
            continue
        if state.get("halt_reason"):
            reasons.append(str(state["halt_reason"]))
        if state.get("quarantined_strategies"):
            reasons.append("strategie in quarantena: " + str(state["quarantined_strategies"]))
        if state.get("active") or state.get("daily_halt_active") or state.get("halt"):
            reasons.append("daily halt persistito")
        streaks = state.get("strategy_loss_streaks") or {}
        if state.get("loss_streak", 0) >= 3 or any(v >= 3 for v in streaks.values()):
            reasons.append("tre perdite consecutive")
    return reasons


def _build_readiness(data: Path, logs: Path, root: Path, *, expected_mode=None,
                    synthetic=None, process_check=None, current_commit=None,
                    stopped=False) -> dict:
    process_check = process_check or pid_alive
    commit = git_commit(root) if current_commit is None else current_commit
    checks = []

    def check(key, passed, message, severity="red"):
        checks.append(dict(key=key, severity=severity, passed=bool(passed), message=message))

    manifest = load_run_manifest(data)
    ledger = read_json(data / "portfolio_state.json")
    runtime = read_json(data / "runtime_status.json")
    monitored = read_json(data / "monitored_wallets.json")
    shadow = read_json(data / "shadow_state.json")
    run_id = str(manifest.get("run_id") or "")
    mode = manifest.get("execution_mode")
    check("manifest", bool(manifest), "manifest schema v1 compatibile")
    check("ledger_schema", ledger.get("state_version") == 3, "ledger schema v3 richiesto")
    check("runtime_schema", runtime.get("runtime_version") == RUNTIME_VERSION,
          f"runtime schema v{RUNTIME_VERSION} richiesto")
    check("deployed_commit", bool(commit) and runtime.get("running_commit") == commit,
          f"origine={str(manifest.get('deployed_commit') or '')[:12]}, "
          f"runtime={str(runtime.get('running_commit') or '')[:12]}, checkout={commit[:12]}")
    check("manifest_mode", mode == expected_mode if expected_mode else mode in {"observe", "paper_validation"},
          f"modalita persistente: {mode}")
    health = validate_cohort(manifest.get("wallets", []), manifest.get("intended_domains", []))
    check("cohort", health.get("validation_ready"), "; ".join(health.get("errors", [])) or "coorte valida")
    check("cohort_identity", bool(run_id) and monitored.get("run_id") == run_id
          and cohort_identity(manifest) == cohort_identity(monitored)
          and runtime.get("cohort_identity") == [cohort_identity(manifest)[0],
              [[address, list(domains)] for address, domains in cohort_identity(manifest)[1]],
              cohort_identity(manifest)[2]], "wallet e domini runtime/manifest identici")
    for key, value, identity in (
        ("ledger_fresh", ledger.get("saved_at"), ledger.get("run_id")),
        ("heartbeat_fresh", runtime.get("updated_at"), runtime.get("run_id")),
        ("cycle_fresh", runtime.get("last_cycle_at"), runtime.get("run_id")),
    ):
        age = age_seconds(value)
        check(key, identity == run_id and age is not None and age <= 60,
              f"{key}: {age:.1f}s" if age is not None else f"{key}: assente")
    check("two_cycles", runtime.get("completed_cycles", 0) >= 2,
          f"cicli completati dal processo: {runtime.get('completed_cycles', 0)}/2")
    if not stopped:
        check("bot_phase", runtime.get("phase") not in {"stopped", "stopping", "error"},
              f"fase bot: {runtime.get('phase')}")
        check("bot_process", process_check(data / "bot.pid"), "processo bot attivo")
        check("dashboard_process", process_check(data / "dashboard.pid"), "processo dashboard attivo")
        try:
            pid_matches = int((data / "bot.pid").read_text()) == runtime.get("pid")
        except (OSError, ValueError):
            pid_matches = False
        check("process_identity", pid_matches and bool(runtime.get("process_instance_id")), "PID e identita runtime coerenti")
    check("latency_arb_off", not process_check(data / "latency_arb.pid"), "latency-arb fermo")
    check("runtime_mode", runtime.get("runtime_mode") == mode and ledger.get("execution_mode") == mode,
          f"runtime={runtime.get('runtime_mode')}, manifest={mode}")
    check("runtime_error", not runtime.get("error") and not runtime.get("run_integrity_error"),
          runtime.get("error") or runtime.get("run_integrity_error") or "nessun errore runtime")
    check("environment_drift", not mode_drift(manifest), mode_drift(manifest) or "nessun conflitto env", "yellow")
    try:
        log = (logs / "bot.log").read_text(encoding="utf-8", errors="replace")
        check("traceback", "Traceback" not in log, "nessun traceback nel log corrente")
    except OSError:
        check("traceback", False, "log bot non leggibile")
    feed = runtime.get("feed_health") or {}
    status, reason = feed_readiness(feed)
    check("feed_health", status == "healthy", f"{status}: {reason}")
    expected_addresses = {str(w.get("address", "")).lower() for w in manifest.get("wallets", [])}
    check("feed_coverage", bool(expected_addresses) and expected_addresses == {
        str(w).lower() for w in feed.get("last_snapshot_wallets_ok", [])}, "copertura dell'intera coorte richiesta")
    # Snapshot health alone is insufficient if reconcile/save failed afterwards.
    check("healthy_cycles", runtime.get("consecutive_healthy_cycles", 0) >= 2,
          f"cicli sani e salvati consecutivi: {runtime.get('consecutive_healthy_cycles', 0)}/2")
    rows, errors = journal_rows(data / "candidate_journal.jsonl", run_id)
    candidates = [r for r in rows if r.get("decision") in {"eligible", "opened", "rejected"}]
    for row in candidates:
        if row.get("journal_version") != 6:
            errors.append("journal versione diversa da 6")
        if row.get("pretrade_eligible") or row.get("decision") in {"opened", "eligible"}:
            try:
                from time_utils import parse_utc
                if row.get("source_trade_status") != "ok" or not row.get("signal_id") or not parse_utc(row.get("source_trade_at")):
                    raise ValueError("sorgente mancante")
                for key in ("best_bid", "best_ask", "executable_ask_vwap", "executable_bid_vwap"):
                    if not 0 < number(row.get(key), key) <= 1:
                        raise ValueError("book invalido")
                if row.get("fees_enabled") not in (True, False) or not row.get("fee_source"):
                    raise ValueError("fee sconosciuta")
                if row.get("fees_enabled"):
                    schedule = row.get("fee_schedule") or {}
                    if number(schedule.get("rate"), "fee rate") < 0 or number(schedule.get("exponent"), "fee exponent") < 0:
                        raise ValueError("fee invalida")
            except (ValueError, TypeError):
                errors.append(f"eligible incompleto: {row.get('signal_id')}")
    check("journal_v6", not errors, "; ".join(errors[:3]) or "journal v6 valido")
    check("candidate_volume", bool(candidates), f"{len(candidates)} candidati; zero non blocca il paper", "yellow")
    safety = safety_reasons(data, run_id)
    check("shadow_breaker", not safety, "; ".join(safety) or "nessun circuit breaker attivo")
    accounting = ledger_metrics(ledger, run_id)
    check("accounting", accounting.get("reconciled"), "; ".join(accounting["quality_errors"]) or "ledger riconciliato")
    transition = read_json(data / "paper_transition.json")
    if (mode == "paper_validation" and activation(data, run_id).get("status") != "active"
            and transition.get("status") == "pending"
            and transition.get("manifest", {}).get("run_id") == run_id):
        check("initial_paper_sample", ledger.get("initial_capital") == 300
              and ledger.get("cash") == 300 and ledger.get("positions") == {}
              and ledger.get("closed_positions") == [],
              "nuovo paper: capitale/cash $300 e zero esecuzioni prima dell'attivazione")
    fees = execution_fees(rows, ledger)
    check("execution_costs", not fees["fee_quality_errors"],
          "; ".join(fees["fee_quality_errors"][:3]) or "fee delle esecuzioni riconciliate")
    if synthetic is None:
        old = read_json(data / "preflight_report.json")
        synthetic = old.get("synthetic_lifecycle", {}) if old.get("run_id") == run_id and old.get("checked_commit") == commit else {}
    check("isolated_lifecycle", synthetic.get("passed"), synthetic.get("error") or "smoke isolato del commit corrente richiesto")
    shadow_metrics = ledger_metrics(shadow, run_id) if shadow else {"closed_trades": 0, "realized_pnl": None}
    if shadow:
        check("shadow_schema", shadow.get("shadow_version") == 3 and not shadow.get("legacy_unconstrained"),
              "shadow schema v3 vincolato richiesto")
        check("shadow_accounting", shadow_metrics.get("reconciled"), "; ".join(shadow_metrics["quality_errors"]) or "shadow riconciliato")
    pnl = shadow_metrics.get("realized_pnl")
    check("economic_edge", False,
          f"edge non dimostrato: {shadow_metrics['closed_trades']} chiusure shadow; P&L "
          + (f"${pnl:.6f}" if pnl is not None else "non disponibile"), "yellow")
    blockers = [c for c in checks if c["severity"] == "red" and not c["passed"]]
    return dict(report_version=2, generated_at=utc_now_iso(), run_id=run_id,
                checked_commit=commit, run_origin_commit=manifest.get("deployed_commit"),
                process_instance_id=runtime.get("process_instance_id"), ready=not blockers,
                checks=checks, blockers=blockers,
                warnings=[c for c in checks if c["severity"] == "yellow" and not c["passed"]],
                synthetic_lifecycle=synthetic, feed_status=status,
                activation=activation(data, run_id), economic_status={
                    "edge_demonstrated": False, "real_money_authorized": False,
                    "closed_trades": shadow_metrics["closed_trades"], "net_pnl": pnl})


def build_readiness(data: Path, logs: Path, root: Path, **kwargs) -> dict:
    try:
        return _build_readiness(data, logs, root, **kwargs)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        check = dict(key="invalid_state", severity="red", passed=False,
                     message=f"dati di controllo non validi: {exc}")
        return dict(report_version=2, generated_at=utc_now_iso(), ready=False,
                    run_id=read_json(data / "run_manifest.json").get("run_id"),
                    checks=[check], blockers=[check], warnings=[], feed_status="unknown",
                    synthetic_lifecycle=kwargs.get("synthetic") or {})
