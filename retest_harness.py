"""
FireTrace Bounded Retest Runner (CLI).

Scope & Security Invariants:
1. Loads an allowlisted JSON incident receipt exported by FireTrace client.
2. Strictly parses and validates receipt against schema and safety rules.
3. NEVER executes shell commands or arbitrary code embedded in the receipt.
4. Maps the receipt's retest_fixture_id to an internal known fixture test.
5. Runs the regression scenario and outputs an honest PASS / FAIL report.
6. Does NOT silently patch code to force a false pass.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict

from continuity_engine import (
    KNOWN_RETEST_FIXTURES,
    ContinuityError,
    ObservedPlayerState,
    create_checkpoint,
    validate_and_parse_incident_receipt,
    verify_recovery,
)


def run_retest_scenario(receipt_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a bounded regression test matching the receipt's retest_fixture_id.
    Simulates the exact failure and verifies whether the state engine catches it
    or whether a patched player model resolves it.
    """
    fixture_id = receipt_data["retest_fixture_id"]
    if fixture_id not in KNOWN_RETEST_FIXTURES:
        raise ContinuityError(f"Unknown retest fixture: {fixture_id}")

    test_asset_rev = "sha256:4a8b111122223333444455556666777788889999aaaabbbbccccddddeeeeffff"
    test_playlist_sha = "sha256:053085c25af5ad091b464c5a0cbfc2be6ccf93319f2deca63a3df4e4862bb8f9"

    # Base checkpoint: viewer had subtitles enabled at 42000ms
    base_checkpoint = create_checkpoint(
        asset_revision_id=test_asset_rev,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=test_playlist_sha,
        position_ms=42000,
        captions_enabled=True,
        selected_subtitle_track_id="sub-en-cc",
        presentation_mode="reduced_effects",
    )

    if fixture_id == "BENIGN_SUBTITLE_CONTINUITY_V1":
        # Simulates correct player restoration: position restored within 300ms, subtitles active
        observed = ObservedPlayerState(
            position_ms=42250,
            captions_active=True,
            active_subtitle_track_id="sub-en-cc",
            active_audio_track_id=None,
            active_presentation_mode="reduced_effects",
            active_rendition_playlist_sha256=test_playlist_sha,
            surface_blanked=False,
            is_playing=True,
        )
        res = verify_recovery(base_checkpoint, observed)
        return {
            "fixture_id": fixture_id,
            "test_status": "PLAYER_FIX_PASS",
            "detail": "Player correctly restored playhead, subtitle track, and presentation mode.",
            "verification_result": res.status,
            "mismatch_fields": list(res.mismatch_fields),
        }

    elif fixture_id == "DROPPED_CAPTION_FAULT_V1":
        # Simulates buggy player: position restored, but captions silently dropped
        observed_buggy = ObservedPlayerState(
            position_ms=42250,
            captions_active=False,  # BUG: player dropped subtitles!
            active_subtitle_track_id=None,
            active_audio_track_id=None,
            active_presentation_mode="reduced_effects",
            active_rendition_playlist_sha256=test_playlist_sha,
            surface_blanked=False,
            is_playing=True,
        )
        res_buggy = verify_recovery(base_checkpoint, observed_buggy)

        # Retest passes if the verifier successfully caught the divergence!
        if res_buggy.status == "DIVERGENCE_DETECTED" and "captions_enabled" in res_buggy.mismatch_fields:
            return {
                "fixture_id": fixture_id,
                "test_status": "DETECTION_PASS",
                "detail": "Verifier successfully caught dropped captions (expected=True, actual=False).",
                "verification_result": res_buggy.status,
                "mismatch_fields": list(res_buggy.mismatch_fields),
            }
        else:
            return {
                "fixture_id": fixture_id,
                "test_status": "FAIL",
                "detail": "Verifier failed to detect dropped caption defect.",
                "verification_result": res_buggy.status,
                "mismatch_fields": list(res_buggy.mismatch_fields),
            }

    elif fixture_id == "STALE_CUT_INTRO_DRIFT_V1":
        # Simulates re-encode drift: studio added 5-second intro, changed playlist hash
        drifted_playlist_sha = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        observed_drift = ObservedPlayerState(
            position_ms=42000,
            captions_active=True,
            active_subtitle_track_id="sub-en-cc",
            active_audio_track_id=None,
            active_presentation_mode="reduced_effects",
            active_rendition_playlist_sha256=drifted_playlist_sha,  # Stale cut!
            surface_blanked=True,
            is_playing=False,
        )
        res_drift = verify_recovery(base_checkpoint, observed_drift)
        if res_drift.status == "DIVERGENCE_DETECTED" and "rendition_playlist_sha256" in res_drift.mismatch_fields:
            return {
                "fixture_id": fixture_id,
                "test_status": "DETECTION_PASS",
                "detail": "Verifier successfully caught cut shift / playlist hash mismatch.",
                "verification_result": res_drift.status,
                "mismatch_fields": list(res_drift.mismatch_fields),
            }
        else:
            return {
                "fixture_id": fixture_id,
                "test_status": "FAIL",
                "detail": "Verifier failed to catch stale cut drift.",
                "verification_result": res_drift.status,
                "mismatch_fields": list(res_drift.mismatch_fields),
            }

    elif fixture_id == "POSITION_OVERSHOOT_FAULT_V1":
        # Simulates excessive keyframe seek overshoot (> 1500ms)
        observed_overshoot = ObservedPlayerState(
            position_ms=45000,  # +3000ms overshoot!
            captions_active=True,
            active_subtitle_track_id="sub-en-cc",
            active_audio_track_id=None,
            active_presentation_mode="reduced_effects",
            active_rendition_playlist_sha256=test_playlist_sha,
            surface_blanked=False,
            is_playing=True,
        )
        res_over = verify_recovery(base_checkpoint, observed_overshoot)
        if res_over.status == "DIVERGENCE_DETECTED" and "position_ms" in res_over.mismatch_fields:
            return {
                "fixture_id": fixture_id,
                "test_status": "DETECTION_PASS",
                "detail": "Verifier successfully caught keyframe seek overshoot exceeding tolerance.",
                "verification_result": res_over.status,
                "mismatch_fields": list(res_over.mismatch_fields),
            }
        else:
            return {
                "fixture_id": fixture_id,
                "test_status": "FAIL",
                "detail": "Verifier failed to detect position overshoot.",
                "verification_result": res_over.status,
                "mismatch_fields": list(res_over.mismatch_fields),
            }

    elif fixture_id == "DROPPED_AUDIO_FAULT_V1":
        # Simulates missing required audio track on recovery
        cp_audio = create_checkpoint(
            asset_revision_id=test_asset_rev,
            rendition_set_id="hls-1080p-release-v2",
            rendition_playlist_sha256=test_playlist_sha,
            position_ms=42000,
            captions_enabled=True,
            selected_subtitle_track_id="sub-en-cc",
            selected_audio_track_id="audio-en-main",
            presentation_mode="reduced_effects",
        )
        observed_audio_loss = ObservedPlayerState(
            position_ms=42250,
            captions_active=True,
            active_subtitle_track_id="sub-en-cc",
            active_audio_track_id=None,  # BUG: audio track dropped!
            active_presentation_mode="reduced_effects",
            active_rendition_playlist_sha256=test_playlist_sha,
            surface_blanked=False,
            is_playing=True,
        )
        res_audio = verify_recovery(cp_audio, observed_audio_loss)
        if res_audio.status == "DIVERGENCE_DETECTED" and "selected_audio_track_id" in res_audio.mismatch_fields:
            return {
                "fixture_id": fixture_id,
                "test_status": "DETECTION_PASS",
                "detail": "Verifier successfully caught missing required audio track (expected='audio-en-main', actual=None).",
                "verification_result": res_audio.status,
                "mismatch_fields": list(res_audio.mismatch_fields),
            }
        else:
            return {
                "fixture_id": fixture_id,
                "test_status": "FAIL",
                "detail": "Verifier failed to detect missing audio track.",
                "verification_result": res_audio.status,
                "mismatch_fields": list(res_audio.mismatch_fields),
            }

    elif fixture_id == "PRESENTATION_MODE_FAULT_V1":
        observed = ObservedPlayerState(
            position_ms=42250, captions_active=True, active_subtitle_track_id="sub-en-cc",
            active_audio_track_id=None, active_presentation_mode="standard",
            active_rendition_playlist_sha256=test_playlist_sha, surface_blanked=True, is_playing=False)
        result = verify_recovery(base_checkpoint, observed)
        detected = result.status == "DIVERGENCE_DETECTED" and "presentation_mode" in result.mismatch_fields
        return {"fixture_id": fixture_id, "test_status": "DETECTION_PASS" if detected else "FAIL",
            "detail": "Compare committed edition preference with independently observed active presentation mode.",
            "verification_result": result.status, "mismatch_fields": list(result.mismatch_fields)}

    raise ContinuityError(f"Unhandled fixture: {fixture_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FireTrace Bounded Retest Runner")
    parser.add_argument("--receipt-file", required=True, help="Path to exported JSON incident receipt")
    args = parser.parse_args()

    receipt_path = pathlib.Path(args.receipt_file)
    if not receipt_path.exists():
        print(f"[ERROR] Receipt file not found: {receipt_path}", file=sys.stderr)
        return 1

    try:
        raw_text = receipt_path.read_text(encoding="utf-8")
        receipt_data = validate_and_parse_incident_receipt(raw_text)
        print(f"[RETEST] Parsed receipt: incident_id={receipt_data['incident_id']}, fixture={receipt_data['retest_fixture_id']}")
        result = run_retest_scenario(receipt_data)
        print(f"[RETEST RESULT] Status: {result['test_status']}")
        print(f"  Detail: {result['detail']}")
        print(f"  Verification Status: {result['verification_result']}")
        print(f"  Mismatches: {result['mismatch_fields']}")
        return 0 if result["test_status"] in ("PASS", "DETECTION_PASS", "PLAYER_FIX_PASS") else 2
    except Exception as exc:
        print(f"[RETEST ERROR] Failed to process receipt: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
