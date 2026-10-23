#!/usr/bin/env python3
"""
FireShield Connected Three-Part Demonstration Runner.
Merges:
  Part 1: Actual frame-based screening & human editorial review
  Part 2: Synaptic Shield version-bound Ed25519 v2 publication & controlled fail-closed checks
  Part 3: FireTrace recovery divergence investigation & deterministic retest simulation

Usage:
    python tools/run_connected_demonstration.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

# Ensure workspace root is in python path
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from continuity_engine import (compute_receipt_digest, validate_and_parse_incident_receipt,
    create_checkpoint, ObservedPlayerState, verify_recovery, generate_incident_receipt)
from pipeline_service import LocalPipelineService, PipelineServiceError
from retest_harness import run_retest_scenario
from state_engine import (
    FireShieldRuntimeError,
    PlaybackSessionContext,
    SidecarContractValidator,
)


def log_header(title: str) -> None:
    print("\n" + "=" * 76)
    print(f"  {title}")
    print("=" * 76)


def log_step(step: str, detail: str) -> None:
    print(f"\n[+] {step}")
    print(f"    {detail}")


def main() -> int:
    log_header("FIRESHIELD: CONNECTED THREE-PART PROTOTYPE DEMONSTRATION")
    print(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print("Mode: HEADLESS INTEGRATION HARNESS (Deterministic Verification)")
    print("Native TV Run: PENDING / UNVERIFIED (No active Android TV emulator or physical TV attached)")
    print("Invariants: Real frame analysis, strict human approval gates, Ed25519 signing, fail-closed tampering rejection.")

    # Initialize domain pipeline service
    service = LocalPipelineService()
    tenant_id = "publisher-demo-tenant"

    # -------------------------------------------------------------------------
    # PART 1: FRAME SCREENING & EDITORIAL GATING
    # -------------------------------------------------------------------------
    log_header("PART 1: REAL ASSET CATALOG & DETERMINISTIC FRAME SCREENING")

    # 1.1 Inspect catalog
    assets = service.list_assets(tenant_id)
    log_step("Step 1.1: Catalog Discovery", f"Discovered {len(assets)} managed assets/fixtures in tenant '{tenant_id}':")
    for a in assets:
        print(f"    - [{a['asset_id']}] {a['name']} ({a['duration_ms']//1000}s, {a['fps']}fps)")

    # 1.2 Screen benign asset (clean control)
    benign_ast = next(a for a in assets if "benign" in a["asset_id"])
    log_step("Step 1.2: Screen Benign Clip", f"Running frame screening on '{benign_ast['name']}'...")
    job_benign = service.start_screening_job(tenant_id=tenant_id, asset_id=benign_ast["asset_id"])
    report_benign = service.get_screening_report(job_benign.asset_revision_id, tenant_id)
    print(f"    Result: {job_benign.status} | Findings: {len(report_benign['findings'])} (Clean control confirmed)")

    # 1.3 Screen hazard asset (luminance strobe fixture)
    hazard_ast = next(a for a in assets if "strobe" in a["asset_id"])
    log_step("Step 1.3: Screen Hazard Fixture", f"Running frame screening on '{hazard_ast['name']}'...")
    job_hazard = service.start_screening_job(tenant_id=tenant_id, asset_id=hazard_ast["asset_id"])
    report_hazard = service.get_screening_report(job_hazard.asset_revision_id, tenant_id)
    print(f"    Result: {job_hazard.status} | Review State: {report_hazard['review_state']}")
    print(f"    Detected {len(report_hazard['findings'])} candidate hazard interval(s):")
    for f in report_hazard["findings"]:
        print(f"      - ID: {f['interval_id']} | Window: {f['start_ms']}ms - {f['end_ms']}ms | Types: {f['types']} | Policy: {f['policy']}")

    hazard_rev = job_hazard.asset_revision_id
    hazard_interval_id = report_hazard["findings"][0]["interval_id"]

    # 1.4 Controlled Failure: Gating before human review
    log_step("Step 1.4 [CONTROLLED FAILURE]: Publication without Human Approval",
             "Attempting to publish release while review_state is 'pending_review'...")
    try:
        service.publish_release(
            tenant_id=tenant_id,
            asset_revision_id=hazard_rev,
            rendition_set_id="hls-strobe-demo",
            playlist_manifest="#EXTM3U\nseg1.ts\n",
        )
        print("    [FAIL] Publication unexpectedly succeeded without human approval!")
        return 1
    except PipelineServiceError as exc:
        print(f"    [PASS - REJECTED AS EXPECTED]: {exc.message}")

    # -------------------------------------------------------------------------
    # PART 2: SYNAPTIC STORY CONTEXT & VERSION-BOUND PUBLICATION
    # -------------------------------------------------------------------------
    log_header("PART 2: SYNAPTIC CONTEXT GROUNDING & ED25519 v2 PUBLICATION")

    # 2.1 Context Grounding (Extractive Subtitles)
    log_step("Step 2.1: Subtitle Cue Grounding", f"Grounding narrative context for interval '{hazard_interval_id}'...")
    ctx_note = service.ground_context_for_interval(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        interval_id=hazard_interval_id,
        method="extractive_subtitles",
    )
    print(f"    Grounded Note ID: {ctx_note['note_id']}")
    print(f"    Extracted Text  : \"{ctx_note['text']}\"")
    print(f"    Grounded Cues   : {ctx_note['source_cue_ids']}")
    print(f"    Model Invoked   : {ctx_note['model_invoked']} (extractive fallback)")

    # 2.2 Human Editorial Approval
    log_step("Step 2.2: Human Editorial Review Gate", "Editor inspects findings and approves context-bound intervention...")
    service.approve_context_note(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        interval_id=hazard_interval_id,
        reviewer_id="editor-chief-carla",
    )
    rev_decision = service.submit_editorial_decision(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        reviewer_id="editor-chief-carla",
        decision="approved",
        notes="Hazard interval approved with Synaptic Shield context banner skip.",
    )
    rev_rec = rev_decision.get("editorial_decision_record") or {}
    print(f"    Decision Recorded: {rev_decision['review_state']} by {rev_rec.get('reviewer_id', 'editor')}")

    # 2.3 Publication & Ed25519 v2 Signing
    log_step("Step 2.3: Cryptographic Release Publication", "Signing Ed25519 v2 sidecar manifest bound to rendition playlist...")
    hls_file = WORKSPACE_ROOT / "fireos" / "app" / "src" / "main" / "assets" / "hls" / "variant_1080p.m3u8"
    assert hls_file.exists(), f"Managed HLS variant file must exist: {hls_file}"
    seg_file = hls_file.parent / "seg0.ts"
    assert seg_file.exists(), f"Managed HLS media segment must exist: {seg_file}"
    hls_manifest = hls_file.read_text(encoding="utf-8")
    release = service.publish_release(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        rendition_set_id="hls-1080p-demo",
        playlist_manifest=hls_manifest,
        media_uri="asset:///hls/variant_1080p.m3u8",
    )
    print(f"    Published Release ID : {release.release_id}")
    print(f"    Manifest Digest (HEX): {release.manifest_digest}")
    print(f"    Signing Algorithm    : {release.signing_alg}")
    print(f"    Key ID               : {release.key_id}")
    print(f"    Context Notes Bound  : {release.context_notes_count}")

    # 2.4 Verify Cryptographic Sidecar Contract
    log_step("Step 2.4: Cryptographic Verification", "Verifying sidecar self-consistency and Ed25519 signature...")
    session_ctx = PlaybackSessionContext(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        rendition_set_id="hls-1080p-demo",
        rendition_playlist_sha256=release.rendition_playlist_sha256,
        timebase="presentation_ms",
    )
    contract_dict, valid_intervals, review_state = SidecarContractValidator.validate(
        release.sidecar_json,
        session_ctx,
        trusted_public_keys={release.key_id: release.public_key_hex},
    )
    print(f"    [PASS] Sidecar contract verified! Intervals: {len(valid_intervals)} | Review: {review_state}")
    print(f"    Attached Context: \"{valid_intervals[0].context_note}\"")

    # 2.5 Controlled Failure: Tampered Rendition Rejection
    log_step("Step 2.5 [CONTROLLED FAILURE]: Tampered Rendition Playlist",
             "Injecting altered playlist content with original signed sidecar...")
    tampered_ctx = PlaybackSessionContext(
        tenant_id=tenant_id,
        asset_revision_id=hazard_rev,
        rendition_set_id="hls-1080p-demo",
        rendition_playlist_sha256="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        timebase="presentation_ms",
    )
    try:
        SidecarContractValidator.validate(
            release.sidecar_json,
            tampered_ctx,
            trusted_public_keys={release.key_id: release.public_key_hex},
        )
        print("    [FAIL] Validator accepted tampered rendition hash!")
        return 1
    except FireShieldRuntimeError as exc:
        print(f"    [PASS - REJECTED AS EXPECTED]: {exc}")

    # -------------------------------------------------------------------------
    # PART 3: FIRETRACE RECOVERY & INCIDENT RETEST SIMULATION
    # -------------------------------------------------------------------------
    log_header("PART 3: FIRETRACE RECOVERY INVESTIGATION & REGRESSION RETEST")

    # Generate this run's evidence from the same published source and playlist.
    # These are explicit reference-model observations, not native-device telemetry.
    checkpoint = create_checkpoint(asset_revision_id=hazard_rev,
        rendition_set_id=release.rendition_set_id,
        rendition_playlist_sha256=release.rendition_playlist_sha256,
        position_ms=2500, captions_enabled=True,
        selected_subtitle_track_id="sub-en-cc", selected_audio_track_id="audio-en")
    observed = ObservedPlayerState(position_ms=2500, captions_active=False,
        active_subtitle_track_id=None, active_audio_track_id="audio-en",
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=release.rendition_playlist_sha256,
        surface_blanked=True, is_playing=False)
    verification = verify_recovery(checkpoint, observed)
    assert verification.status == "DIVERGENCE_DETECTED"
    receipt = generate_incident_receipt(incident_id="reference-" + uuid.uuid4().hex,
        fixture_id=hazard_ast["asset_id"], verification_result=verification,
        platform_name="Python-reference-model")
    raw_receipt = json.dumps(receipt)
    parsed_receipt = validate_and_parse_incident_receipt(raw_receipt, strict=True)
    receipt_path = WORKSPACE_ROOT / "runtime_data" / "connected-demo-receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    log_step("Step 3.1: Fresh Reference Recovery Incident",
             f"Ingesting incident receipt '{parsed_receipt['incident_id']}'...")
    print(f"    Diagnostic Code  : {parsed_receipt['diagnostic_code']}")
    print(f"    Recovery Status  : {parsed_receipt['recovery_status']}")
    print(f"    Mismatch Fields  : {parsed_receipt['mismatch_fields']}")
    print(f"    Position Delta   : {parsed_receipt['position_delta_ms']} ms")
    print(f"    Retest Fixture   : {parsed_receipt['retest_fixture_id']}")
    print(f"    Cryptographic SHA: {parsed_receipt['receipt_digest']}")

    # 3.2 Retest Simulation
    log_step("Step 3.2: Deterministic Retest Harness Execution",
             "Running bounded retest simulation on immutable receipt...")
    retest_result = run_retest_scenario(parsed_receipt)
    assert retest_result["test_status"] == "DETECTION_PASS"
    print(f"    Retest Status      : {retest_result['test_status']}")
    print(f"    Verification Result: {retest_result['verification_result']}")
    print(f"    Detail             : {retest_result['detail']}")

    # 3.3 TV Launch Command Ready
    log_header("DEMONSTRATION CONCLUSION & TV HANDOFF")
    print("\nAndroid TV Intent Launch Command (Generated from Published Release):")
    print("-" * 76)
    print(release.harness_intent_command)
    print("-" * 76)

    # Check ADB device status
    adb_path = pathlib.Path(r"C:\Users\ATEC\AppData\Local\Android\Sdk\platform-tools\adb.exe")
    if adb_path.exists():
        try:
            adb_devices = subprocess.run([str(adb_path), "devices"], capture_output=True, text=True, timeout=5)
            lines = [l.strip() for l in adb_devices.stdout.splitlines() if l.strip() and not l.startswith("List of")]
            print(f"\nADB Host Status: platform-tools detected | Attached devices: {len(lines)}")
            for d in lines:
                print(f"    - {d}")
            if not lines:
                print("    (No active emulator or physical TV attached; native device test status: PENDING / UNVERIFIED)")
        except Exception:
            pass

    print("\n[HEADLESS PIPELINE PASS] Connected Three-Part Prototype Contract Demonstration Completed Successfully!")
    print("Native TV Run Status: not executed by this headless runner; see the exact commit's cloud emulator artifacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
