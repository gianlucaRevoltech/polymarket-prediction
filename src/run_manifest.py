"""Persistent, immutable run identity and execution-mode contract."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from config import DATA_DIR, EXECUTION, STRATEGY


RUN_MANIFEST_VERSION = 1
VALID_EXECUTION_MODES = {"observe", "paper_validation"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _git_commit(root: Optional[Path] = None) -> str:
    base = root or Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=base, capture_output=True,
            text=True, timeout=3, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return ""


def new_run_id() -> str:
    return (
        f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-"
        f"{uuid.uuid4().hex[:8]}"
    )


def normalize_mode(value: Any, default: str = "observe") -> str:
    mode = str(value or default).strip().lower()
    if mode not in VALID_EXECUTION_MODES:
        raise ValueError(f"execution_mode non valida: {mode}")
    return mode


def load_run_manifest(data_dir: Optional[Path] = None) -> Dict[str, Any]:
    base = Path(data_dir or DATA_DIR)
    payload = _read_json(base / "run_manifest.json")
    if not payload:
        return {}
    try:
        version = int(payload.get("manifest_version", 0) or 0)
        payload["execution_mode"] = normalize_mode(payload.get("execution_mode"))
    except (TypeError, ValueError):
        return {}
    if version != RUN_MANIFEST_VERSION or not payload.get("run_id"):
        return {}
    return payload


def _wallet_domains(wallet: Dict[str, Any]) -> list[str]:
    raw = wallet.get("allowed_domains") or wallet.get("categories") or []
    if not raw and wallet.get("category"):
        raw = [wallet.get("category")]
    return sorted({
        str(domain).strip().lower() for domain in raw
        if str(domain or "").strip()
    })


def normalize_wallets(rows: Iterable[Any]) -> list[Dict[str, Any]]:
    wallets = []
    seen = set()
    for raw in rows or []:
        wallet = dict(raw) if isinstance(raw, dict) else {"address": raw}
        address = str(wallet.get("address") or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        wallet["address"] = address
        wallet["allowed_domains"] = _wallet_domains(wallet)
        wallet["categories"] = list(wallet["allowed_domains"])
        if wallet["allowed_domains"]:
            wallet["category"] = wallet["allowed_domains"][0]
        wallets.append(wallet)
    return wallets


def cohort_from_payload(payload: Dict[str, Any]) -> Tuple[list[Dict[str, Any]], list[str]]:
    wallets = normalize_wallets(payload.get("wallets", []))[
        :max(1, int(STRATEGY.get("top_wallets", 12)))
    ]
    diagnostics = payload.get("scan_diagnostics", {})
    intended = payload.get("intended_domains") or (
        diagnostics.get("validation_domains", {})
        if isinstance(diagnostics, dict) else []
    )
    intended_domains = sorted({
        str(domain).strip().lower() for domain in intended or []
        if str(domain or "").strip()
    })
    if not intended_domains:
        intended_domains = sorted({
            domain for wallet in wallets for domain in wallet["allowed_domains"]
        })
    return wallets, intended_domains


def validate_cohort(wallets: list[Dict[str, Any]], intended_domains: list[str]) -> Dict[str, Any]:
    minimum = int(EXECUTION.get("minimum_monitored_wallets", 5))
    per_wallet_cap = max(1, int(EXECUTION.get("shadow_max_trades_per_wallet", 20)))
    minimum_domain_trades = max(
        1, int(EXECUTION.get("promotion_min_trades_per_domain", 30))
    )
    minimum_per_domain = (
        minimum_domain_trades + per_wallet_cap - 1
    ) // per_wallet_cap
    counts = {
        domain: sum(
            1 for wallet in wallets if domain in wallet.get("allowed_domains", [])
        )
        for domain in intended_domains
    }
    errors = []
    addresses = [str(w.get("address") or "").strip().lower() for w in wallets]
    if any(not address for address in addresses) or len(set(addresses)) != len(addresses):
        errors.append("wallet assenti o duplicati nella coorte")
    if len(wallets) < minimum:
        errors.append(f"coorte insufficiente: {len(wallets)}/{minimum} wallet")
    if not intended_domains:
        errors.append("nessun dominio di validazione congelato")
    for domain, count in counts.items():
        if count < minimum_per_domain:
            errors.append(
                f"dominio {domain} insufficiente: {count}/{minimum_per_domain} wallet"
            )
    return {
        "validation_ready": not errors,
        "wallet_count": len(wallets),
        "minimum_required": minimum,
        "intended_domains": intended_domains,
        "domain_wallet_counts": counts,
        "minimum_wallets_per_domain": minimum_per_domain,
        "errors": errors,
    }


def create_run_manifest(
    mode: str,
    cohort_payload: Dict[str, Any],
    *,
    data_dir: Optional[Path] = None,
    source_path: Optional[Path] = None,
    run_id: Optional[str] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    base = Path(data_dir or DATA_DIR)
    mode = normalize_mode(mode)
    wallets, intended_domains = cohort_from_payload(cohort_payload)
    cohort_health = validate_cohort(wallets, intended_domains)
    if not cohort_health["validation_ready"]:
        raise ValueError("; ".join(cohort_health["errors"]))
    current_run_id = str(run_id or new_run_id())
    source_hash = str(cohort_payload.get("cohort_source_sha256") or "")
    if not source_hash and source_path:
        source_hash = _sha256(Path(source_path))
    manifest = {
        "manifest_version": RUN_MANIFEST_VERSION,
        "run_id": current_run_id,
        "execution_mode": mode,
        "created_at": _utc_now_iso(),
        "deployed_commit": _git_commit(root),
        "cohort_source_sha256": source_hash,
        "wallet_count": len(wallets),
        "wallets": wallets,
        "intended_domains": intended_domains,
        "cohort_health": cohort_health,
        "experimental_paper": mode == "paper_validation",
        "real_money_authorized": False,
    }
    monitored = {
        "run_id": current_run_id,
        "execution_mode": mode,
        "frozen": True,
        "domain_policy_version": 1,
        "intended_domains": intended_domains,
        "wallet_count": len(wallets),
        "minimum_required": cohort_health["minimum_required"],
        "validation_ready": True,
        "updated_at": manifest["created_at"],
        "cohort_source_sha256": source_hash,
        "wallets": wallets,
    }
    _atomic_write_json(base / "run_manifest.json", manifest)
    _atomic_write_json(base / "monitored_wallets.json", monitored)
    return manifest


def mode_drift(manifest: Dict[str, Any]) -> str:
    env_value = os.environ.get("POLYMARKET_EXECUTION_MODE")
    if not manifest or env_value is None:
        return ""
    try:
        env_mode = normalize_mode(env_value)
    except ValueError:
        return f"env execution_mode non valida ignorata: {env_value}"
    manifest_mode = manifest.get("execution_mode")
    if env_mode != manifest_mode:
        return (
            f"env {env_mode} ignorata: il run manifest resta {manifest_mode}"
        )
    return ""


def legacy_identity(
    data_dir: Optional[Path] = None,
    *,
    configured_mode: Optional[str] = None,
) -> Dict[str, str]:
    base = Path(data_dir or DATA_DIR)
    ledger = _read_json(base / "portfolio_state.json")
    mode = ledger.get("execution_mode")
    source = "ledger"
    if not mode:
        mode = os.environ.get("POLYMARKET_EXECUTION_MODE")
        if mode:
            source = "environment"
        else:
            mode = configured_mode or "observe"
            source = "configured" if configured_mode else "default"
    try:
        mode = normalize_mode(mode)
    except ValueError:
        mode, source = "observe", "default"
    return {
        "run_id": str(ledger.get("run_id") or new_run_id()),
        "execution_mode": mode,
        "source": source,
    }
