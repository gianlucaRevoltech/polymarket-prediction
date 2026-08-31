"""Gestione transazionale di manifest, archivio e reset dei run paper."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = ROOT / "logs"
sys.path.insert(0, str(ROOT / "src"))

from run_manifest import (  # noqa: E402
    create_run_manifest,
    legacy_identity,
    load_run_manifest,
    validate_cohort,
)

RUN_FILES = (
    "run_manifest.json", "preflight_report.json", "portfolio_state.json",
    "portfolio_state.json.bak", "trades_log.json", "equity_curve.json",
    "peak_equity.json", "recent_opens.json", "daily_halt.json",
    "safety_state.json", "candidate_journal.jsonl", "shadow_state.json",
    "shadow_state.json.bak", "shadow_journal.jsonl",
    "shadow_equity_curve.json", "shadow_equity_curve.json.bak",
    "monitored_wallets.json", "wallet_quality.json", "runtime_status.json",
    "paper_activation.json", "deployment_history.jsonl", "paper_report.json",
)
PRESERVED_EVIDENCE_FILES = ("wallet_validation_registry.json", "scan_results.json", "paper_transition.json")
CLEAR_FILES = RUN_FILES + (
    "price_history.json", "whale_wallets.json", "latency_arb_signals.jsonl",
    "latency_arb_stats.json",
)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _safe_run_id(raw: str) -> str:
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "._-").strip("._-")
    return cleaned or f"legacy-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"


def current_run_id() -> str:
    manifest = load_run_manifest(DATA)
    if manifest.get("run_id"):
        return _safe_run_id(str(manifest["run_id"]))
    state = _read_json(DATA / "portfolio_state.json")
    return _safe_run_id(str(state.get("run_id") or ""))


def archive() -> Path:
    import hashlib
    run_id = current_run_id()
    target = DATA / "runs" / run_id
    if target.exists():
        target = DATA / "runs" / f"{run_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    target.mkdir(parents=True, exist_ok=False)
    for name in RUN_FILES + PRESERVED_EVIDENCE_FILES:
        source = DATA / name
        if source.is_file():
            shutil.copy2(source, target / name)
    config = ROOT / "src" / "config.py"
    if config.exists():
        shutil.copy2(config, target / "config.py")
    hashes = {}
    for name in RUN_FILES + PRESERVED_EVIDENCE_FILES:
        source = DATA / name
        if source.is_file():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if hashlib.sha256((target / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"archivio non verificato: {name}")
            hashes[name] = digest
    for name in ("bot.log", "dashboard.log"):
        if (LOGS / name).is_file():
            shutil.copy2(LOGS / name, target / name)
    (target / "archive_manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashes,
    }, indent=2), encoding="utf-8")
    print(f"[ARCHIVE] Run preservato in {target}")
    return target


def clear(force: bool) -> None:
    if not force:
        raise SystemExit("clear richiede --force")
    for name in CLEAR_FILES:
        path = DATA / name
        if path.is_file():
            path.unlink()
    alerts = LOGS / "alerts.log"
    if alerts.is_file():
        alerts.unlink()
    print("[RESET] Stato del run corrente azzerato; archivi e scan_results preservati.")


def _load_cohort(path: Path) -> dict:
    payload = _read_json(path)
    if not payload:
        raise SystemExit(f"coorte assente o non valida: {path}")
    return payload


def create(mode: str, cohort_file: Path, run_id: str | None = None) -> dict:
    manifest = create_run_manifest(
        mode, _load_cohort(cohort_file), data_dir=DATA,
        source_path=cohort_file, run_id=run_id, root=ROOT,
    )
    print(
        f"[RUN] Creato {manifest['run_id']} | mode={manifest['execution_mode']} "
        f"| wallet={manifest['wallet_count']} | domini={','.join(manifest['intended_domains'])}"
    )
    return manifest


def ensure_current() -> dict:
    manifest = load_run_manifest(DATA)
    if manifest:
        validate_current()
        return manifest
    if (DATA / "run_manifest.json").exists():
        raise SystemExit(
            "run_manifest.json presente ma non valido: avvio rifiutato"
        )
    identity = legacy_identity(DATA)
    cohort_file = DATA / "monitored_wallets.json"
    if not cohort_file.exists():
        cohort_file = DATA / "scan_results.json"
    print(
        "[MIGRATION] run_manifest.json assente: creo il contratto persistente "
        f"dal run legacy ({identity['source']})."
    )
    return create(identity["execution_mode"], cohort_file, identity["run_id"])


def validate_current() -> dict:
    manifest = load_run_manifest(DATA)
    if not manifest:
        raise SystemExit("run_manifest.json assente o incompatibile")
    health = validate_cohort(manifest.get("wallets", []), manifest.get("intended_domains", []))
    errors = list(health["errors"])
    ledger = _read_json(DATA / "portfolio_state.json")
    monitored = _read_json(DATA / "monitored_wallets.json")
    if (DATA / "portfolio_state.json").exists() and not ledger:
        errors.append("ledger presente ma non leggibile: vietato ripartire da zero")
    if ledger and ledger.get("run_id") != manifest["run_id"]:
        errors.append("run_id del ledger diverso dal manifest")
    if ledger and ledger.get("execution_mode") != manifest["execution_mode"]:
        errors.append("execution_mode del ledger diversa dal manifest")
    if monitored.get("run_id") != manifest["run_id"]:
        errors.append("monitored_wallets non appartiene al run corrente")
    frozen = [str(w.get("address", "")).lower() for w in manifest.get("wallets", []) if isinstance(w, dict)]
    actual = [str(w.get("address", "")).lower() for w in monitored.get("wallets", []) if isinstance(w, dict)]
    if frozen != actual:
        errors.append("coorte monitored_wallets diversa dal manifest")
    from paper_readiness import cohort_identity
    if cohort_identity(manifest) != cohort_identity(monitored):
        errors.append("domini/hash coorte monitored_wallets diversi dal manifest")
    if errors:
        raise SystemExit("; ".join(errors))
    print(
        f"[RUN] Manifest valido: {manifest['run_id']} | "
        f"mode={manifest['execution_mode']} | wallet={len(frozen)}"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("archive")
    clear_parser = sub.add_parser("clear")
    clear_parser.add_argument("--force", action="store_true")
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--mode", required=True, choices=("observe", "paper_validation"))
    create_parser.add_argument("--cohort-file", type=Path, required=True)
    create_parser.add_argument("--run-id")
    sub.add_parser("ensure-current")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.command == "archive":
        archive()
    elif args.command == "clear":
        clear(args.force)
    elif args.command == "create":
        create(args.mode, args.cohort_file, args.run_id)
    elif args.command == "ensure-current":
        ensure_current()
    else:
        validate_current()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
