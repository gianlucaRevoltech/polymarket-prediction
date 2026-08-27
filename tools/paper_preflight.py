"""Preflight tecnico/safety per il passaggio OBSERVE -> paper sperimentale."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"
sys.path.insert(0, str(ROOT / "src"))

from run_manifest import load_run_manifest, mode_drift, validate_cohort  # noqa: E402
from time_utils import age_seconds, utc_now_iso  # noqa: E402


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _journal_rows(path: Path, run_id: str) -> tuple[list[dict], int]:
    rows = []
    parse_errors = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    if line.strip():
                        parse_errors += 1
                    continue
                if isinstance(row, dict) and row.get("run_id") == run_id:
                    rows.append(row)
    except OSError:
        pass
    return rows, parse_errors


def _pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _check(checks: list[dict], key: str, severity: str, passed: bool, message: str) -> None:
    checks.append({
        "key": key, "severity": severity, "passed": bool(passed),
        "message": message,
    })


def _run_synthetic() -> dict:
    script = ROOT / "tools" / "paper_lifecycle_smoke.py"
    result = subprocess.run(
        [sys.executable, str(script)], cwd=ROOT, capture_output=True,
        text=True, timeout=30, check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    payload = {}
    for line in reversed(lines):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    if result.returncode != 0 or not payload.get("passed"):
        payload = {
            "passed": False,
            "error": payload.get("error") or result.stderr.strip() or "smoke fallito",
        }
    return payload


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=3, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def build_report(*, post_start: bool = False, run_synthetic: bool = True) -> dict:
    checks: list[dict] = []
    manifest = load_run_manifest(DATA)
    ledger = _read_json(DATA / "portfolio_state.json")
    runtime = _read_json(DATA / "runtime_status.json")
    monitored = _read_json(DATA / "monitored_wallets.json")
    shadow = _read_json(DATA / "shadow_state.json")
    run_id = str(manifest.get("run_id") or "")
    expected_mode = "paper_validation" if post_start else "observe"

    _check(checks, "manifest", "red", bool(manifest), "run_manifest schema v1 valido")
    current_commit = _git_commit()
    _check(
        checks, "deployed_commit", "red",
        bool(current_commit) and manifest.get("deployed_commit") == current_commit,
        f"manifest={str(manifest.get('deployed_commit') or '')[:12]}, "
        f"checkout={current_commit[:12]}",
    )
    _check(
        checks, "manifest_mode", "red",
        manifest.get("execution_mode") == expected_mode,
        f"modalita manifest richiesta: {expected_mode}",
    )
    cohort = validate_cohort(
        manifest.get("wallets", []), manifest.get("intended_domains", [])
    ) if manifest else {"validation_ready": False, "errors": ["manifest assente"]}
    _check(
        checks, "cohort", "red", bool(cohort.get("validation_ready")),
        "coorte congelata valida" if cohort.get("validation_ready")
        else "; ".join(cohort.get("errors", [])),
    )
    manifest_addresses = [
        str(w.get("address", "")).lower()
        for w in manifest.get("wallets", []) if isinstance(w, dict)
    ]
    monitored_addresses = [
        str(w.get("address", "")).lower()
        for w in monitored.get("wallets", []) if isinstance(w, dict)
    ]
    _check(
        checks, "cohort_identity", "red",
        bool(run_id) and monitored.get("run_id") == run_id
        and monitored_addresses == manifest_addresses,
        "coorte runtime identica al manifest",
    )

    ledger_age = age_seconds(ledger.get("saved_at"))
    runtime_age = age_seconds(runtime.get("updated_at"))
    _check(
        checks, "ledger_fresh", "red",
        ledger.get("run_id") == run_id and ledger_age is not None and ledger_age <= 60,
        f"ledger age={ledger_age:.1f}s" if ledger_age is not None else "ledger assente/stale",
    )
    _check(
        checks, "heartbeat_fresh", "red",
        runtime.get("run_id") == run_id and runtime_age is not None and runtime_age <= 60,
        f"heartbeat age={runtime_age:.1f}s" if runtime_age is not None else "heartbeat assente/stale",
    )
    if post_start:
        cycle = int(runtime.get("cycle", 0) or 0)
        _check(
            checks, "two_cycles", "red", cycle >= 2,
            f"cicli completati dal nuovo avvio: {cycle}/2",
        )
    _check(checks, "bot_process", "red", _pid_alive(DATA / "bot.pid"), "processo bot attivo")
    _check(checks, "dashboard_process", "red", _pid_alive(DATA / "dashboard.pid"), "processo dashboard attivo")
    _check(
        checks, "latency_arb_off", "red", not _pid_alive(DATA / "latency_arb.pid"),
        "latency-arb fermo",
    )
    runtime_mode = runtime.get("runtime_mode") or ledger.get("execution_mode")
    _check(
        checks, "runtime_mode", "red", runtime_mode == manifest.get("execution_mode"),
        f"runtime={runtime_mode}, manifest={manifest.get('execution_mode')}",
    )
    _check(
        checks, "runtime_error", "red",
        not runtime.get("error") and not runtime.get("run_integrity_error"),
        runtime.get("error") or runtime.get("run_integrity_error") or "nessun errore runtime",
    )
    drift = mode_drift(manifest)
    _check(
        checks, "environment_drift", "yellow", not drift,
        drift or "nessuna variabile ambiente discordante",
    )

    log_text = ""
    try:
        log_text = (LOGS / "bot.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    _check(checks, "traceback", "red", "Traceback" not in log_text, "nessun traceback nel log corrente")
    feed = runtime.get("feed_health", {}) if isinstance(runtime.get("feed_health"), dict) else {}
    feed_ok = (
        int(feed.get("consecutive_transient_errors", 0) or 0) < 3
        and int(feed.get("fully_failed_snapshot_cycles", 0) or 0) < 2
    )
    _check(
        checks, "feed_health", "red", feed_ok,
        "feed senza outage persistente" if feed_ok else "outage feed persistente",
    )

    rows, journal_parse_errors = _journal_rows(
        DATA / "candidate_journal.jsonl", run_id
    )
    candidate_rows = [
        row for row in rows if row.get("decision") in {"eligible", "rejected", "opened"}
    ]
    eligible = [
        row for row in candidate_rows
        if row.get("pretrade_eligible") is True or row.get("decision") in {"eligible", "opened"}
    ]
    invalid = []
    invalid_versions = [
        row.get("signal_id") or row.get("market") or "unknown"
        for row in candidate_rows
        if int(row.get("journal_version", 0) or 0) != 6
    ]
    for row in eligible:
        complete_source = (
            row.get("source_trade_status") == "ok" and row.get("signal_id")
            and row.get("source_trade_at")
        )
        complete_book = all(row.get(key) is not None for key in (
            "best_bid", "best_ask", "executable_ask_vwap", "executable_bid_vwap",
        ))
        complete_fee = (
            row.get("fees_enabled") is not None
            and (not row.get("fees_enabled") or row.get("fee_schedule"))
            and row.get("fee_source")
        )
        if not (complete_source and complete_book and complete_fee):
            invalid.append(row.get("signal_id") or row.get("market") or "unknown")
    _check(
        checks, "journal_v6", "red",
        not invalid and not invalid_versions and journal_parse_errors == 0,
        "journal v6 valido" if not invalid and not invalid_versions and journal_parse_errors == 0
        else (
            f"versioni invalide={len(invalid_versions)}, "
            f"eligible incompleti={len(invalid)}, parse_errors={journal_parse_errors}"
        ),
    )
    _check(
        checks, "candidate_volume", "yellow", bool(candidate_rows),
        f"{len(candidate_rows)} candidati nel run" if candidate_rows else "zero candidati: paper consentito",
    )

    shadow_halt = str(shadow.get("halt_reason") or "")
    safety_block = shadow_halt.startswith("run_loss") or "consecutive" in shadow_halt
    _check(
        checks, "shadow_breaker", "red", not safety_block,
        shadow_halt or "nessun halt shadow bloccante",
    )
    synthetic = _run_synthetic() if run_synthetic else {"passed": True, "skipped": True}
    _check(
        checks, "isolated_lifecycle", "red", bool(synthetic.get("passed")),
        "lifecycle isolato superato" if synthetic.get("passed") else synthetic.get("error", "smoke fallito"),
    )

    closed = shadow.get("closed_positions", []) if isinstance(shadow.get("closed_positions"), list) else []
    pnl = sum(float(item.get("pnl", 0) or 0) for item in closed if isinstance(item, dict))
    _check(
        checks, "economic_edge", "yellow", False,
        f"edge non dimostrato: {len(closed)} chiusure shadow, P&L netto ${pnl:.2f}",
    )
    blockers = [item for item in checks if item["severity"] == "red" and not item["passed"]]
    warnings = [item for item in checks if item["severity"] == "yellow" and not item["passed"]]
    return {
        "report_version": 1, "generated_at": utc_now_iso(), "run_id": run_id,
        "purpose": "post_start" if post_start else "paper_preflight",
        "ready": not blockers, "checks": checks, "blockers": blockers,
        "warnings": warnings, "synthetic_lifecycle": synthetic,
        "economic_status": {
            "edge_demonstrated": False, "paper_experimental": True,
            "closed_trades": len(closed), "net_pnl": pnl,
            "real_money_authorized": False,
        },
    }


def _atomic_write_report(report: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "preflight_report.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-start", action="store_true")
    parser.add_argument("--skip-synthetic", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    report = build_report(
        post_start=args.post_start, run_synthetic=not args.skip_synthetic
    )
    if not args.no_write:
        _atomic_write_report(report)
    print("\n=== PREFLIGHT PAPER ===")
    for item in report["checks"]:
        symbol = "OK" if item["passed"] else ("WARN" if item["severity"] == "yellow" else "BLOCK")
        print(f"[{symbol}] {item['key']}: {item['message']}")
    print(f"Risultato: {'READY' if report['ready'] else 'BLOCKED'}")
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
