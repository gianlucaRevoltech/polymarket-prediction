"""Transactional OBSERVE -> paper activation, under start_all's flock."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import paper_preflight as preflight
import run_state
from paper_accounting import read_json
from paper_readiness import cohort_identity, safety_reasons
from run_manifest import load_run_manifest, create_run_manifest
from runtime_contract import activation, atomic_json
from time_utils import utc_now_iso


def services(action):
    fd = int(os.environ.get("POLYMARKET_LOCK_FD", "-1"))
    kwargs = {"pass_fds": (fd,)} if os.name != "nt" and fd >= 0 else {}
    subprocess.run(["bash", str(ROOT / "start_all.sh"), action], cwd=ROOT,
                   check=True, **kwargs)


def set_activation(data, manifest, status, reason="", **extra):
    atomic_json(data / "paper_activation.json", dict(
        run_id=manifest["run_id"], status=status, reason=reason,
        updated_at=utc_now_iso(), **extra))


def prepare_transition(data, original, archive_path):
    """Prepare the entire target BEFORE clearing source files; durable recovery intent."""
    cohort = read_json(archive_path / "monitored_wallets.json")
    with tempfile.TemporaryDirectory(prefix="paper-prepare-") as tmp:
        staging = Path(tmp)
        target = create_run_manifest("paper_validation", cohort, data_dir=staging, root=ROOT)
        intent = dict(status="pending", source_run_id=original["run_id"],
                      archive_path=str(archive_path), manifest=target,
                      monitored=read_json(staging / "monitored_wallets.json"))
    atomic_json(data / "paper_transition.json", intent)
    return intent


def install_transition(data, intent):
    target = intent["manifest"]
    archive_path = Path(intent["archive_path"]).resolve()
    if not archive_path.is_relative_to((data / "runs").resolve()):
        raise RuntimeError("archivio transizione fuori data/runs")
    archived = read_json(archive_path / "archive_manifest.json")
    if archived.get("run_id") != intent["source_run_id"] or not archived.get("sha256"):
        raise RuntimeError("archivio transizione non verificato")
    import hashlib
    for name, digest in archived["sha256"].items():
        source = (archive_path / name).resolve()
        if source.parent != archive_path or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("archivio transizione modificato")
    current = load_run_manifest(data)
    ledger = read_json(data / "portfolio_state.json")
    allowed = (None, intent["source_run_id"], target["run_id"])
    if current.get("run_id") not in allowed or ledger.get("run_id") not in allowed:
        raise RuntimeError("transizione incompatibile con run corrente")
    if ledger.get("run_id") != target["run_id"] and current.get("run_id") != target["run_id"]:
        run_state.clear(True)
    # Existing target ledger is NEVER cleared when resuming interrupted creation.
    atomic_json(data / "run_manifest.json", target)
    atomic_json(data / "monitored_wallets.json", intent["monitored"])
    return target


def paper_start() -> int:
    data = run_state.DATA
    manifest = load_run_manifest(data)
    touched, archive_path = False, None
    original = manifest
    intent = read_json(data / "paper_transition.json")
    if (manifest and intent.get("status") == "pending"
            and manifest.get("run_id") == intent.get("manifest", {}).get("run_id")
            and activation(data, manifest["run_id"]).get("status") == "active"):
        # Crash after activation but before writing the commit marker: no restart/reset.
        intent = dict(intent, status="committed")
        atomic_json(data / "paper_transition.json", intent)
    if intent.get("status") == "pending" and (
        not manifest or manifest.get("run_id") in (intent.get("source_run_id"),
                                                   intent.get("manifest", {}).get("run_id"))):
        try:
            services("stop")
            manifest = install_transition(data, intent)
            original = {"run_id": intent["source_run_id"]}
            archive_path = Path(intent["archive_path"])
        except (Exception, KeyboardInterrupt) as exc:
            print(f"[BLOCK] Ripresa creazione: {exc}")
            return 2
    if not manifest:
        print("[BLOCK] Manifest assente/incompatibile; nessuno stato modificato.")
        return 2
    current_activation = activation(data, manifest["run_id"])
    if manifest["execution_mode"] == "paper_validation" and current_activation.get("status") == "active":
        report = preflight.wait_report(post_start=True, wait_seconds=120)
        preflight._atomic_write_report(report)
        preflight.print_report(report)
        print("[RUN] Paper esistente preservato; nessun nuovo campione.")
        return 0 if report["ready"] else 2
    try:
        synthetic = preflight._run_synthetic()
        if manifest["execution_mode"] == "observe":
            initial = preflight.wait_report(wait_seconds=120, synthetic=synthetic)
            preflight._atomic_write_report(initial)
            preflight.print_report(initial)
            if not initial["ready"]:
                print("[BLOCK] OBSERVE invariato: nessun archivio o reset.")
                return 2
            touched = True
            services("stop")
            final = preflight.build_report(run_synthetic=False, synthetic=synthetic, stopped=True)
            if not final["ready"] or cohort_identity(load_run_manifest(data)) != cohort_identity(original):
                raise RuntimeError("controllo finale dopo stop fallito: " + "; ".join(c["message"] for c in final["blockers"]))
            preflight._atomic_write_report(final)
            archive_path = run_state.archive()
            intent = prepare_transition(data, original, archive_path)
            manifest = install_transition(data, intent)
        elif manifest["execution_mode"] == "paper_validation":
            reasons = safety_reasons(data, manifest["run_id"])
            if reasons or not synthetic.get("passed"):
                print("[BLOCK] " + "; ".join(reasons or ["smoke isolato fallito"]))
                return 2
            touched = True
            services("stop")
        else:
            raise RuntimeError("modalita non supportata")
        previous_instance = read_json(data / "runtime_status.json").get("process_instance_id")
        set_activation(data, manifest, "pending", "verifica di due cicli completi",
                       source_run_id=original["run_id"], archive_path=str(archive_path or ""))
        services("start")
        report = preflight.wait_report(post_start=True, wait_seconds=120, synthetic=synthetic,
                                       process_instance_id=previous_instance)
        preflight._atomic_write_report(report)
        preflight.print_report(report)
        if not report["ready"]:
            raise RuntimeError("verifica post-start fallita: " + "; ".join(c["message"] for c in report["blockers"]))
        set_activation(data, manifest, "active", "preflight e due cicli verificati",
                       verified_process_instance_id=report["process_instance_id"],
                       source_run_id=original["run_id"], archive_path=str(archive_path or ""))
        if intent and intent.get("manifest", {}).get("run_id") == manifest["run_id"]:
            atomic_json(data / "paper_transition.json", dict(intent, status="committed"))
        print("[OK] PAPER EXPERIMENTAL attivo; edge non dimostrato, denaro reale disabilitato.")
        return 0
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        if touched:
            try:
                services("stop")
            except Exception as stop_error:
                print(f"[BLOCK] Arresto servizi da verificare: {stop_error}")
            current = load_run_manifest(data)
            set_activation(data, current or original, "failed", str(exc) or "interrotto",
                           source_run_id=original["run_id"], archive_path=str(archive_path or ""))
        print(f"[BLOCK] Stato diagnostico preservato: {exc}")
        return 2


if __name__ == "__main__":
    import signal
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    if not os.environ.get("POLYMARKET_LOCK_FD"):
        raise SystemExit("Usare ./start_all.sh paper-start (lock obbligatorio)")
    raise SystemExit(paper_start())
