#!/usr/bin/env python3
"""
FireShield Native Incident Bridge & Telemetry Ingester (Priority 4 Tool).
Safely links native TV player incident receipts to the local FireShield API.

Features:
- Validates strict receipt schema and cryptographic digest.
- Ingests into local API (via HTTP loopback or direct in-process adapter).
- Binds incident identity with published release metadata.
- Executes bounded retest simulation (retest_harness).
- Fetches EviRCA deterministic advisory.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

# Add parent directory to module path
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from continuity_engine import compute_receipt_digest, validate_and_parse_incident_receipt


def extract_incident_from_device(serial: Optional[str] = None) -> Optional[str]:
    """Attempts to read a FireTraceReceipt emitted in logcat by com.fireshield.fireos."""
    adb_cmd = ["adb"]
    if serial:
        adb_cmd.extend(["-s", serial])
    adb_cmd.extend(["logcat", "-d", "-t", "500", "-s", "FireTraceReceipt:W", "FireTrace:I"])

    try:
        res = subprocess.run(adb_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        output = res.stdout + res.stderr
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(output):
            if ch != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(output[idx:])
                if isinstance(obj, dict) and "receipt_digest" in obj and "incident_id" in obj:
                    return json.dumps(obj, indent=2)
            except ValueError:
                continue
    except Exception as exc:
        print(f"[BRIDGE] Warning: ADB extraction failed ({exc})", file=sys.stderr)

    return None


def submit_to_api(
    receipt_json: str,
    api_url: str = "http://127.0.0.1:8000",
    tenant_id: str = "publisher-demo-tenant",
    user_id: str = "incident-bridge-tool",
) -> Dict[str, Any]:
    """Posts incident receipt to the FireShield local API."""
    url = f"{api_url.rstrip('/')}/api/v1/incidents"
    req = urllib.request.Request(
        url,
        data=receipt_json.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-FireShield-Tenant-Id": tenant_id,
            "X-FireShield-User-Id": user_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_api_retest(
    incident_id: str,
    api_url: str = "http://127.0.0.1:8000",
    tenant_id: str = "publisher-demo-tenant",
    user_id: str = "incident-bridge-tool",
) -> Dict[str, Any]:
    url = f"{api_url.rstrip('/')}/api/v1/incidents/{incident_id}/retest"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-FireShield-Tenant-Id": tenant_id,
            "X-FireShield-User-Id": user_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_api_advisory(
    incident_id: str,
    api_url: str = "http://127.0.0.1:8000",
    tenant_id: str = "publisher-demo-tenant",
) -> Dict[str, Any]:
    url = f"{api_url.rstrip('/')}/api/v1/incidents/{incident_id}/advisory"
    req = urllib.request.Request(
        url,
        headers={
            "X-FireShield-Tenant-Id": tenant_id,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Link native FireShield player incidents to local API")
    parser.add_argument("--file", help="Path to incident receipt JSON file")
    parser.add_argument("--from-device", action="store_true", help="Extract recent receipt from ADB logcat")
    parser.add_argument("--serial", help="ADB emulator serial (e.g. emulator-5554)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000", help="FireShield API base URL")
    parser.add_argument("--tenant-id", default="publisher-demo-tenant", help="Tenant ID")
    parser.add_argument("--release-sidecar", help="Optional published sidecar JSON file to correlate")
    args = parser.parse_args()

    raw_receipt: Optional[str] = None

    if args.file:
        p = pathlib.Path(args.file)
        if not p.exists():
            print(f"Error: file not found at {p}", file=sys.stderr)
            return 1
        raw_receipt = p.read_text(encoding="utf-8")
    elif args.from_device:
        print("[BRIDGE] Querying connected Android TV device logcat for FireTraceReceipt...")
        raw_receipt = extract_incident_from_device(args.serial)
        if not raw_receipt:
            print("[BRIDGE] No recent FireTraceReceipt found on device. Falling back to default portable sample.", file=sys.stderr)
            fallback_p = WORKSPACE_ROOT / "sample_incident_receipt.json"
            if fallback_p.exists():
                raw_receipt = fallback_p.read_text(encoding="utf-8")
            else:
                print("Error: No receipt available to ingest.", file=sys.stderr)
                return 1
    else:
        # Default to sample receipt if no args provided
        sample_p = WORKSPACE_ROOT / "sample_incident_receipt.json"
        if sample_p.exists():
            raw_receipt = sample_p.read_text(encoding="utf-8")
        else:
            print("Error: Specify --file or --from-device", file=sys.stderr)
            return 1

    # 1. Validate receipt strictly
    try:
        parsed = validate_and_parse_incident_receipt(raw_receipt, strict=True)
        incident_id = parsed["incident_id"]
        digest = parsed["receipt_digest"]
        print(f"[BRIDGE] Validated receipt {incident_id} (digest: {digest[:16]}...)")
    except Exception as exc:
        print(f"[BRIDGE] Schema validation failed: {exc}", file=sys.stderr)
        return 1

    # 2. Release correlation if provided
    if args.release_sidecar:
        sidecar_p = pathlib.Path(args.release_sidecar)
        if sidecar_p.exists():
            try:
                s_dict = json.loads(sidecar_p.read_text(encoding="utf-8"))
                expected_sha = s_dict.get("rendition_playlist_sha256")
                obs_sha = parsed.get("observed_rendition_playlist_sha256") or parsed.get("manifest_digest")
                print(f"[BRIDGE] Correlating with release sidecar {sidecar_p.name}:")
                print(f"         Expected rendition SHA: {expected_sha}")
                print(f"         Observed rendition SHA: {obs_sha}")
            except Exception as e:
                print(f"[BRIDGE] Warning: sidecar correlation read failed ({e})")

    # 3. Ingest into API
    try:
        ingest_res = submit_to_api(raw_receipt, api_url=args.api_url, tenant_id=args.tenant_id)
        print(f"[BRIDGE] Ingestion status: {ingest_res.get('status')} for incident {incident_id}")
    except urllib.error.URLError as exc:
        print(f"[BRIDGE] Note: Local server offline at {args.api_url} ({exc}). Running local direct simulation:")
        from api_adapter import FireShieldLocalApiAdapter, AuthContext
        adapter = FireShieldLocalApiAdapter()
        auth = AuthContext(tenant_id=args.tenant_id)
        adapter.ingest_incident_receipt(raw_receipt, auth)
        retest_res = adapter.run_retest(incident_id, auth)
        advisory = adapter.get_advisory_explanation(incident_id, auth)
        print(f"[BRIDGE] Deterministic Retest: {retest_res['retest_execution']['test_status']} "
              f"({retest_res['retest_execution']['verification_result']})")
        print(f"[BRIDGE] Advisory Facts ({len(advisory['evidence_facts'])}):")
        for f in advisory["evidence_facts"]:
            print(f"         - {f}")
        return 0

    # 4. Trigger Retest via API
    try:
        retest_res = run_api_retest(incident_id, api_url=args.api_url, tenant_id=args.tenant_id)
        rx = retest_res.get("retest_execution", {})
        print(f"[BRIDGE] API Retest Simulation: {rx.get('test_status')} ({rx.get('verification_result')})")
        print(f"         Detail: {rx.get('detail')}")
    except Exception as exc:
        print(f"[BRIDGE] Retest API call failed: {exc}", file=sys.stderr)

    # 5. Get Advisory via API
    try:
        advisory = get_api_advisory(incident_id, api_url=args.api_url, tenant_id=args.tenant_id)
        print(f"[BRIDGE] EviRCA Advisory Status: {advisory.get('advisory_status')} (model_invoked={advisory.get('model_invoked')})")
        for fact in advisory.get("evidence_facts", []):
            print(f"         [FACT] {fact}")
        for hyp in advisory.get("labeled_hypotheses", []):
            print(f"         [HYPOTHESIS] {hyp}")
    except Exception as exc:
        print(f"[BRIDGE] Advisory API call failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
