#!/usr/bin/env python3
"""Isolated public wallet research. No scan, restart, or trading side effects."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paper_accounting import ledger_metrics, position_pnl
from wallet_audit_feed import PublicResearchClient, round_robin_candidates
from wallet_history import METHOD, exclusion_reasons, reconstruct, shortlist, window_metrics, number

NAMES = {"run_manifest.json", "monitored_wallets.json", "wallet_validation_registry.json",
         "portfolio_state.json", "candidate_journal.jsonl", "scan_results.json"}
MAX_BYTES = 256 * 1024 * 1024


def strict_json(text):
    def invalid(value):
        raise ValueError(f"nonfinite JSON {value}")
    return json.loads(text, parse_constant=invalid)


def read_snapshot(path):
    """Read allowlisted members, never extract or follow archive links."""
    path = Path(path).resolve()
    blobs, errors = {}, []
    if path.is_dir():
        base = path / "data" if (path / "data").is_dir() else path
        for name in NAMES:
            file = base / name
            if file.exists():
                if file.is_symlink() or not file.resolve().is_relative_to(path) or file.stat().st_size > MAX_BYTES:
                    raise ValueError("unsafe/oversize snapshot member")
                blobs[name] = file.read_bytes()
    else:
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if not parts or parts[-1] not in NAMES:
                    continue
                if ".." in parts or member.name.startswith("/") or not member.isfile() or member.size > MAX_BYTES:
                    raise ValueError("unsafe/oversize archive member")
                # Do not mix archived sub-runs with the exported current run.
                if "runs" in parts:
                    continue
                name = parts[-1]
                if name in blobs:
                    raise ValueError(f"ambiguous snapshot: duplicate {name}")
                blobs[name] = archive.extractfile(member).read()
    if sum(map(len, blobs.values())) > MAX_BYTES:
        raise ValueError("snapshot too large")
    data, hashes = {}, {}
    for name, blob in blobs.items():
        hashes[name] = hashlib.sha256(blob).hexdigest()
        try:
            if name.endswith("jsonl"):
                rows = []
                for i, line in enumerate(blob.decode("utf-8").splitlines(), 1):
                    if not line.strip():
                        continue
                    try:
                        row = strict_json(line)
                        if not isinstance(row, dict):
                            raise ValueError("journal non object")
                        rows.append(row)
                    except ValueError:
                        errors.append(f"invalid_journal_line:{i}")
                data[name] = rows
            else:
                value = strict_json(blob.decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError(f"invalid {name}")
                data[name] = value
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"invalid snapshot file {name}: {exc}") from exc
    manifest = data.get("run_manifest.json")
    monitored = data.get("monitored_wallets.json", {})
    ledger = data.get("portfolio_state.json", {})
    if manifest:
        if manifest.get("manifest_version") != 1:
            raise ValueError("unsupported manifest schema")
        cohort = manifest.get("wallets", [])
        run_id = manifest.get("run_id")
    else:
        cohort = monitored.get("wallets", [])
        run_id = monitored.get("run_id") or ledger.get("run_id")
        errors.append("legacy_snapshot_without_manifest")
    if not cohort or not run_id:
        raise ValueError("snapshot requires a frozen cohort and run_id")
    wallets = [dict(w) if isinstance(w, dict) else {"address": w} for w in cohort]
    addresses = [str(w.get("address", "")).lower() for w in wallets]
    if len(set(addresses)) != len(addresses) or any(not re.fullmatch(r"0x[0-9a-f]{40}", a) for a in addresses):
        raise ValueError("invalid/duplicate frozen addresses")
    for payload in (monitored, ledger):
        if payload and payload.get("run_id") != run_id:
            raise ValueError("snapshot contains different run identities")
    if manifest and monitored:
        def identity(rows):
            return sorted((str(w.get("address", "")).lower(),
                           tuple(sorted(w.get("allowed_domains") or w.get("categories") or [])))
                          for w in rows)
        if identity(wallets) != identity(monitored.get("wallets", [])):
            raise ValueError("snapshot cohorts disagree")
    return {"data": data, "hashes": hashes, "errors": errors,
            "wallets": wallets, "run_id": run_id, "source": str(path)}


def prepare_output(output, snapshot, as_of=None):
    output = Path(output).resolve()
    source = Path(snapshot["source"])
    source_base = source if source.is_dir() else source.parent
    if output == source_base or output.is_relative_to(source_base) or source_base.is_relative_to(output):
        raise ValueError("output must not overlap snapshot")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "research" / "wallet-audits"):
        raise ValueError("inside project use research/wallet-audits/<audit-name>, never runtime data")
    if output.exists() and any(p.is_symlink() for p in output.rglob("*")):
        raise ValueError("output contains symlinks")
    metadata_file = output / "audit_metadata.json"
    if metadata_file.exists():
        metadata = strict_json(metadata_file.read_text(encoding="utf-8"))
        if metadata.get("method") != METHOD or metadata.get("snapshot_hashes") != snapshot["hashes"]:
            raise ValueError("output belongs to a different audit/snapshot")
        if as_of is not None and as_of != metadata["as_of"]:
            raise ValueError("as_of differs from frozen audit")
        return output, metadata["as_of"]
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty or belong to this audit")
    stamp = int(time.time()) if as_of is None else int(as_of)
    if stamp <= 90 * 86400:
        raise ValueError("invalid audit timestamp")
    output.mkdir(parents=True, exist_ok=True)
    metadata_file.write_text(json.dumps({"method": METHOD, "as_of": stamp,
                                        "snapshot_hashes": snapshot["hashes"]}, indent=2), encoding="utf-8")
    return output, stamp


def paper_comparison(snapshot):
    data, run_id = snapshot["data"], snapshot["run_id"]
    state = data.get("portfolio_state.json", {})
    metrics = ledger_metrics(state, run_id)
    rows = [r for r in data.get("candidate_journal.jsonl", []) if r.get("run_id") == run_id]
    result = {}
    for wallet in snapshot["wallets"]:
        addr = wallet["address"].lower()
        candidates = {r.get("signal_id") for r in rows if str(r.get("wallet", "")).lower() == addr
                      and r.get("signal_id") and r.get("decision") != "closed"}
        positions = list((state.get("positions") or {}).values()) + state.get("closed_positions", [])
        positions = [p for p in positions if str(p.get("source_wallet", "")).lower() == addr]
        closed = [p for p in state.get("closed_positions", [])
                  if str(p.get("source_wallet", "")).lower() == addr]
        try:
            realized = sum(position_pnl(p) for p in closed) if metrics["reconciled"] else None
            open_rows = [p for p in (state.get("positions") or {}).values()
                         if str(p.get("source_wallet", "")).lower() == addr]
            unrealized = sum(position_pnl(p, closed=False) for p in open_rows) if metrics["reconciled"] else None
        except ValueError:
            realized = unrealized = None
        result[addr] = {"run_id": run_id, "snapshot_saved_at": state.get("saved_at"),
                        "candidates": len(candidates), "openings": len(positions), "closures": len(closed),
                        "realized_net_pnl": realized,
                        "unrealized_net_pnl": unrealized,
                        "total_net_pnl": realized + unrealized if realized is not None and unrealized is not None else None,
                        "quality_errors": metrics["quality_errors"] + snapshot["errors"]}
    return result


def audit_wallet(client, wallet, as_of, quarantined, *, external_errors=()):
    start = as_of - 90 * 86400
    addr = wallet["address"].lower()
    activity = client.activity(addr, start, as_of)
    closed = client.positions(addr, closed=True, start=start)
    opened = client.positions(addr)
    posmap = {}
    quality = list(external_errors)
    for row in opened["rows"]:
        asset = row.get("asset")
        if not asset or (asset in posmap and posmap[asset] != row):
            quality.append("current_position_missing_asset_or_conflict")
        else:
            posmap[asset] = row
    # Full official open inventory, including positions predating activity.
    # This is an API mark, never a claim about liquidation at executable bids.
    unrealized = 0.0 if opened["coverage"] == "complete" else None
    for row in posmap.values():
        try:
            size, mark, entry = number(row.get("size")), number(row.get("curPrice")), number(row.get("avgPrice"))
            if size < 0 or not (0 <= mark <= 1) or entry < 0:
                raise ValueError("invalid open position")
            if unrealized is not None:
                unrealized += size * (mark - entry)
        except (ValueError, TypeError):
            quality.append("official_open_mark_unknown")
            unrealized = None
    report = reconstruct(activity["rows"], posmap, closed["rows"], reconcile=True)
    for source in (activity, closed, opened):
        quality.extend(source["errors"])
    quality.extend(report["quality_errors"])
    windows = {str(d): window_metrics(report, as_of - d * 86400, as_of) for d in (7, 30, 90)}
    if quality:
        for window in windows.values():
            # Keep the usable subset for forensic inspection, but do not display
            # its P&L/WR as the performance of the whole wallet.
            window["diagnostic_subset"] = {k: v for k, v in window.items()}
            for field in ("realized_pnl", "win_rate", "win_rate_ci95", "average_win",
                          "average_loss", "profit_factor", "max_positive_event_share"):
                window[field] = None
            window["profit_factor_unbounded"] = False
    profile = dict(wallet, windows=windows, method=METHOD,
                   coverage="complete" if all(s["coverage"] == "complete" for s in (activity, closed, opened)) else "unknown",
                   quality_errors=sorted(set(quality)), quarantined=addr in quarantined,
                   incentives_usdc=report["incentives_usdc"], fee_coverage=report["fee_coverage"],
                   original_scan={k: wallet.get(k) for k in ("roi", "profit", "win_rate", "num_trades", "decided_positions")},
                   open_positions=[{k: s[k] for k in ("asset", "shares", "cost", "category", "quality_errors")}
                                   for s in report["states"].values() if s["shares"] > 1e-6],
                   official_open_positions=opened["rows"],
                   unrealized_api_mark_pnl=unrealized,
                   unrealized_price_basis="current_api_mark_not_executable_bid",
                   undated_settlements=sum(c["closed_at"] is None for c in report["cycles"]),
                   copy_net_pnl=None)
    profile["exclusion_reasons"] = exclusion_reasons(profile, as_of)
    return profile


def markdown_report(report):
    def esc(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")
    def amount(value):
        return f"{value:.4f}" if isinstance(value, (float, int)) else "n/d"
    lines = ["# Wallet audit — ricerca, non previsione di rendimento", "",
             f"Snapshot run: {report['snapshot_run_id']}; audit UTC: {report['as_of_utc']}.",
             "Paper/coorte invariati. Dati storici selezionati sullo stesso campione: nessuna prova di edge.",
             "Fee storiche: copertura non garantita. P&L della copia disponibile soltanto dal ledger paper.",
             f"Wallet correnti: {report['current_wallet_count']}; alternative: {report['alternative_count']}; shortlist: {len(report['shortlist'])}.",
             "", "## Copertura e limitazioni", "",
             *[f"- {esc(e)}" for e in report["warnings"]],
             "- P&L/WR sono delle sole chiusure ricostruite e riconciliate; con errori sono sottoinsiemi diagnostici, non statistiche complete del wallet.",
             "- 90 giorni di activity non garantiscono costo noto per posizioni precedenti: mismatch esclusi, mai colmati con prezzi inventati.",
             "- Chiusure sono cicli flat-to-flat, non transazioni; riscatti non databili esclusi dalle finestre.",
             "", "## Confronto", "",
             "| Wallet | Attuale | WR90 (n; CI95) | P&L30 / 90 cashflow | PF30 | Asset BUY≥$5 / giorni7 | Nuovi verificati7 / incrementi | Esito |",
             "|---|---|---|---|---|---|---|---|"]
    for p in report["profiles"]:
        a, b, c = (p["windows"][str(d)] for d in (7, 30, 90))
        ci = c["win_rate_ci95"]
        wr = f"{c['win_rate']:.1%} ({c['closed_positions']}; {ci[0]:.1%}–{ci[1]:.1%})" if ci else f"n/d ({c['closed_positions']} chiusure nel sottoinsieme)"
        pf = "senza perdite osservate" if b["profit_factor_unbounded"] else b["profit_factor"]
        lines.append(f"| {esc(p.get('name') or p['address'])} | {p['current']} | {wr} | {amount(b['realized_pnl'])} / {amount(c['realized_pnl'])} | {pf if pf is not None else 'n/d'} | {a['buy_assets_ge_5']} / {a['buy_days_ge_5']} | {a['verified_new_entries']} / {a['verified_increments']} | {esc(', '.join(p['exclusion_reasons']) or 'shortlist research')} |")
    lines.extend(["", "## Shortlist (nessuna sostituzione automatica)", ""])
    lines.extend(f"- {esc(p['name'])}: `{p['address']}`" for p in report["shortlist"])
    if not report["shortlist"]:
        lines.append("Nessun candidato qualificato; soglie non allentate.")
    for p in report["profiles"]:
        lines.extend(["", f"## {esc(p.get('name') or p['address'])}", "",
                      f"Indirizzo: `{p['address']}`; fonti: {esc(', '.join(p['discovery_sources']))}.",
                      f"Scan originale: {esc(json.dumps(p['original_scan']))}",
                      f"Qualita: {esc(', '.join(p['quality_errors']) or 'riconciliazione completata')}; copertura fee: {p['fee_coverage']}.",
                      f"Domini90: {esc(json.dumps(p['windows']['90']['by_domain']))}",
                      f"Ultimo BUY: {p['windows']['7']['last_buy_at']}; posizioni aperte ufficiali: {len(p['official_open_positions'])}; non realizzato al mark API (non bid eseguibile): {amount(p['unrealized_api_mark_pnl'])}; incentivi separati: {p['incentives_usdc']}.",
                      f"Paper effettivo: {esc(json.dumps(p.get('paper', {'available': False})))}"])
    return "\n".join(lines) + "\n"


def run_audit(snapshot, output, as_of, *, client=None, max_new=300, discover=True):
    client = client or PublicResearchClient(output)
    registry = snapshot["data"].get("wallet_validation_registry.json")
    registry_errors = []
    if not registry or registry.get("registry_version") != 1 or not isinstance(registry.get("wallets"), dict):
        registry_errors.append("quarantine_registry_unknown")
        registry = {"wallets": {}}
    if any(not re.fullmatch(r"0x[0-9a-f]{40}", str(a).lower()) or not isinstance(r, dict)
           or r.get("status") not in {"quarantined", "active", "cleared"}
           for a, r in registry["wallets"].items()):
        registry_errors.append("quarantine_registry_invalid_record")
    quarantined = {a.lower() for a, r in registry["wallets"].items()
                   if isinstance(r, dict) and r.get("status") == "quarantined"}
    sources, discovery_errors = client.discover() if discover and max_new else ({}, [])
    sources["snapshot_scan"] = snapshot["data"].get("scan_results.json", {}).get("wallets", [])
    candidates = round_robin_candidates(snapshot["wallets"], sources, min(300, max_new))
    paper = paper_comparison(snapshot)
    profiles = []
    for i, wallet in enumerate(candidates, 1):
        print(f"[AUDIT] {i}/{len(candidates)} {wallet['address']}", flush=True)
        profile = audit_wallet(client, wallet, as_of, quarantined, external_errors=registry_errors)
        profile["paper"] = paper.get(wallet["address"], {"available": False})
        profiles.append(profile)
        # Recoverable progress in OUTPUT only; no input/runtime files touched.
        (Path(output) / "profiles.partial.json").write_text(json.dumps(profiles, indent=2, allow_nan=False), encoding="utf-8")
    selected = shortlist(profiles)
    report = {"audit_version": 1, "method": METHOD, "snapshot_run_id": snapshot["run_id"],
              "snapshot_hashes": snapshot["hashes"], "as_of": as_of,
              "as_of_utc": datetime.fromtimestamp(as_of, timezone.utc).isoformat(),
              "current_wallet_count": len(snapshot["wallets"]),
              "alternative_count": sum(not p["current"] for p in profiles),
              "profiles": profiles, "shortlist": selected,
              "warnings": snapshot["errors"] + registry_errors + discovery_errors,
              "criteria": {"closed90": 50, "events90": 20, "positive_pnl_days": [30, 90],
                           "buy_assets7_ge_5": 10, "buy_days7": 3, "last_buy_max_hours": 48,
                           "min_win_rate": None, "maximum_shortlist": 20},
              "requests": client.requests, "cache_hits": client.cache_hits,
              "edge_demonstrated": False, "real_money_authorized": False,
              "run_mutated": False}
    (Path(output) / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    (Path(output) / "report.md").write_text(markdown_report(report), encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--as-of", type=int, help="UTC epoch; freezes windows, default audit start")
    parser.add_argument("--max-new", type=int, default=300, choices=range(301), metavar="0..300")
    parser.add_argument("--offline", action="store_true", help="use only this audit's existing cache")
    args = parser.parse_args(argv)
    try:
        snapshot = read_snapshot(args.snapshot)
        output, as_of = prepare_output(args.output, snapshot, args.as_of)
        client = PublicResearchClient(output, offline=args.offline)
        report = run_audit(snapshot, output, as_of, client=client, max_new=args.max_new)
        print(f"Report: {output / 'report.md'}; shortlist={len(report['shortlist'])}; nessuna modifica al paper")
        return 0 if all(p["coverage"] == "complete" for p in report["profiles"]) else 2
    except (ValueError, OSError, tarfile.TarError) as exc:
        print(f"[AUDIT BLOCKED] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
