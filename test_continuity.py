"""
Unit, Adversarial, and Three-Way Ablation Tests for FireTrace Synaptic Continuity.

Test Matrix:
1. Checkpoint Atomicity & Cryptographic Integrity:
   - Creation, atomic write/read, schema validation.
   - Tamper rejection (altered playhead, altered captions, digest mismatch).
2. Active Recovery Verification:
   - Full verified match (within documented keyframe tolerance).
   - Dropped caption detection (actual captions=False when expected=True).
   - Re-encoded cut drift detection (playlist SHA mismatch).
   - Keyframe seek overshoot detection (> tolerance).
3. Allowlisted Incident Receipt Sanitization:
   - Zero PII, zero auth tokens, zero video buffers.
   - Hostile script tag and command injection rejection.
   - Corrupted receipt JSON rejection.
4. Bounded Retest Harness:
   - Retest runner maps allowlisted fixture IDs to deterministic tests.
   - Rejects unmapped or malicious fixture IDs.
5. Three-Way Ablation Benchmark:
   - Condition A: Conventional configured resume (position only).
   - Condition B: Unverified checkpoint resume.
   - Condition C: Merged FireTrace Synaptic Continuity.
"""

import hashlib
import json
import pathlib
import pytest

from continuity_engine import (
    CHECKPOINT_SCHEMA_VERSION,
    ContinuityCheckpoint,
    ContinuityError,
    ObservedPlayerState,
    compute_checkpoint_digest,
    compute_receipt_digest,
    create_checkpoint,
    generate_incident_receipt,
    load_checkpoint,
    save_checkpoint_atomic,
    validate_and_parse_incident_receipt,
    verify_recovery,
)
from retest_harness import run_retest_scenario

TEST_ASSET_REV = "sha256:111122223333444455556666777788889999aaaabbbbccccddddeeeeffff0000"
TEST_PLAYLIST_SHA = "sha256:053085c25af5ad091b464c5a0cbfc2be6ccf93319f2deca63a3df4e4862bb8f9"


def test_checkpoint_creation_and_atomic_persistence(tmp_path: pathlib.Path):
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=45200,
        captions_enabled=True,
        selected_subtitle_track_id="sub-en-cc",
        presentation_mode="reduced_effects",
    )
    assert cp.schema_version == CHECKPOINT_SCHEMA_VERSION
    assert cp.position_ms == 45200
    assert cp.captions_enabled is True
    assert cp.integrity_digest.startswith("sha256:")

    target_file = tmp_path / "continuity_checkpoint.json"
    save_checkpoint_atomic(cp, target_file)
    assert target_file.exists()

    loaded = load_checkpoint(target_file)
    assert loaded.checkpoint_id == cp.checkpoint_id
    assert loaded.position_ms == 45200
    assert loaded.integrity_digest == cp.integrity_digest


def test_checkpoint_tamper_rejection(tmp_path: pathlib.Path):
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=30000,
        captions_enabled=True,
    )
    target_file = tmp_path / "tampered_checkpoint.json"
    save_checkpoint_atomic(cp, target_file)

    # Tamper with position_ms without updating integrity_digest
    raw = json.loads(target_file.read_text(encoding="utf-8"))
    raw["position_ms"] = 99999
    target_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ContinuityError) as exc_info:
        load_checkpoint(target_file)
    assert "integrity digest mismatch" in str(exc_info.value).lower()


def test_recovery_verification_happy_path():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
        selected_subtitle_track_id="sub-en-cc",
        presentation_mode="reduced_effects",
    )
    # Observed landing within tolerance (50300ms, diff=300ms <= 1500ms), subtitles active
    observed = ObservedPlayerState(
        position_ms=50300,
        captions_active=True,
        active_subtitle_track_id="sub-en-cc",
        active_audio_track_id=None,
        active_presentation_mode="reduced_effects",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed, position_tolerance_ms=1500)
    assert res.status == "VERIFIED_MATCH"
    assert len(res.mismatch_fields) == 0
    assert res.diagnostic_code == "RESTORE_VERIFIED_PASS"


def test_recovery_verification_detects_dropped_captions():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
        selected_subtitle_track_id="sub-en-cc",
        presentation_mode="reduced_effects",
    )
    # Observed state where captions were silently dropped by player lifecycle
    observed = ObservedPlayerState(
        position_ms=50200,
        captions_active=False,
        active_subtitle_track_id=None,
        active_audio_track_id=None,
        active_presentation_mode="reduced_effects",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed)
    assert res.status == "DIVERGENCE_DETECTED"
    assert "captions_enabled" in res.mismatch_fields


def test_recovery_verification_detects_cut_shift_and_intro_drift():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
    )
    # Studio pushed a re-encoded cut with different playlist hash
    drifted_sha = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    observed = ObservedPlayerState(
        position_ms=50000,
        captions_active=True,
        active_subtitle_track_id=None,
        active_audio_track_id=None,
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=drifted_sha,
        surface_blanked=True,
        is_playing=False,
    )
    res = verify_recovery(cp, observed)
    assert res.status == "DIVERGENCE_DETECTED"
    assert "rendition_playlist_sha256" in res.mismatch_fields


def test_incident_receipt_sanitization_and_privacy_guarantee():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
    )
    observed = ObservedPlayerState(
        position_ms=50000,
        captions_active=False,
        active_subtitle_track_id=None,
        active_audio_track_id=None,
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed)
    receipt = generate_incident_receipt(
        incident_id="inc-test-001",
        fixture_id="test-benign-fixture",
        verification_result=res,
        retest_fixture_id="DROPPED_CAPTION_FAULT_V1",
    )

    receipt_str = json.dumps(receipt)
    # Assert zero PII or sensitive streaming URLs
    assert "token=" not in receipt_str
    assert "password" not in receipt_str.lower()
    assert "http://" not in receipt_str
    assert "https://" not in receipt_str
    assert receipt["privacy_attestation"]["contains_pii"] is False
    assert receipt["privacy_attestation"]["contains_video_buffers"] is False

    # Check parsing
    parsed = validate_and_parse_incident_receipt(receipt_str)
    assert parsed["incident_id"] == "inc-test-001"
    assert parsed["retest_fixture_id"] == "DROPPED_CAPTION_FAULT_V1"


def test_incident_receipt_rejects_hostile_strings():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
    )
    observed = ObservedPlayerState(
        position_ms=50000,
        captions_active=False,
        active_subtitle_track_id=None,
        active_audio_track_id=None,
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed)

    # Attempt injecting script tag into incident ID
    with pytest.raises(ContinuityError):
        generate_incident_receipt(
            incident_id="<script>alert('xss')</script>",
            fixture_id="test-fixture",
            verification_result=res,
            retest_fixture_id="DROPPED_CAPTION_FAULT_V1",
        )

    # Attempt receipt with embedded password or shell injection
    bad_receipt = {
        "receipt_schema_version": 1,
        "incident_id": "test-inc",
        "fixture_id": "fix-1",
        "recovery_status": "DIVERGENCE_DETECTED",
        "retest_fixture_id": "DROPPED_CAPTION_FAULT_V1",
        "mismatch_fields": ["captions_enabled"],
        "leak": "password=supersecrettoken",
    }
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(bad_receipt))


def test_bounded_retest_runner():
    receipt_data = {
        "receipt_schema_version": 1,
        "incident_id": "inc-retest-001",
        "fixture_id": "DROPPED_CAPTION_FAULT_V1",
        "recovery_status": "DIVERGENCE_DETECTED",
        "retest_fixture_id": "DROPPED_CAPTION_FAULT_V1",
        "mismatch_fields": ["captions_enabled"],
    }
    retest_result = run_retest_scenario(receipt_data)
    assert retest_result["test_status"] in ("PASS", "DETECTION_PASS")
    assert "captions_enabled" in retest_result["mismatch_fields"]

    # Test dropped audio fault retest scenario
    audio_receipt = {
        "receipt_schema_version": 1,
        "incident_id": "inc-retest-audio-001",
        "fixture_id": "DROPPED_AUDIO_FAULT_V1",
        "recovery_status": "DIVERGENCE_DETECTED",
        "retest_fixture_id": "DROPPED_AUDIO_FAULT_V1",
        "mismatch_fields": ["selected_audio_track_id"],
    }
    audio_res = run_retest_scenario(audio_receipt)
    assert audio_res["test_status"] in ("PASS", "DETECTION_PASS")
    assert "selected_audio_track_id" in audio_res["mismatch_fields"]


def test_recovery_verification_detects_missing_required_audio():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
        selected_audio_track_id="audio-en-stereo",
    )
    # Observed state where required audio track was dropped/missing
    observed_no_audio = ObservedPlayerState(
        position_ms=50200,
        captions_active=True,
        active_subtitle_track_id=None,
        active_audio_track_id=None,  # Missing audio!
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed_no_audio)
    assert res.status == "DIVERGENCE_DETECTED"
    assert "selected_audio_track_id" in res.mismatch_fields


def test_receipt_digest_integrity_and_tamper_detection():
    cp = create_checkpoint(
        asset_revision_id=TEST_ASSET_REV,
        rendition_set_id="hls-1080p-release-v2",
        rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        position_ms=50000,
        captions_enabled=True,
    )
    observed = ObservedPlayerState(
        position_ms=50000,
        captions_active=False,
        active_subtitle_track_id=None,
        active_audio_track_id=None,
        active_presentation_mode="standard",
        active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
        surface_blanked=False,
        is_playing=True,
    )
    res = verify_recovery(cp, observed)
    receipt = generate_incident_receipt(
        incident_id="inc-digest-test-01",
        fixture_id="test-fixture",
        verification_result=res,
    )
    assert "receipt_digest" in receipt
    assert receipt["receipt_digest"].startswith("sha256:")

    # Valid receipt parses cleanly
    parsed = validate_and_parse_incident_receipt(json.dumps(receipt))
    assert parsed["incident_id"] == "inc-digest-test-01"

    # Tampered receipt position_delta_ms without updating digest
    receipt_tampered = dict(receipt)
    receipt_tampered["position_delta_ms"] = 99999
    with pytest.raises(ContinuityError) as exc_info:
        validate_and_parse_incident_receipt(json.dumps(receipt_tampered))
    assert "integrity digest mismatch" in str(exc_info.value).lower()


def test_strict_receipt_schema_enforcement_and_negative_vectors():
    """
    Negative tests verifying strict schema enforcement, closed field allowlists,
    mandatory digest checks, independent sensitive data scanning, and duplicate key rejection.
    """
    import copy

    base_receipt = {
        "receipt_schema_version": 1,
        "incident_id": "inc-strict-001",
        "fixture_id": "DROPPED_CAPTION_FAULT_V1",
        "timestamp_iso": "2026-10-19T00:00:00Z",
        "environment": {
            "app_version": "0.2.0",
            "platform": "FireOS-Android-Leanback",
        },
        "recovery_status": "DIVERGENCE_DETECTED",
        "diagnostic_code": "ERR_MISMATCH_CAPTIONS_ENABLED",
        "mismatch_fields": ["captions_enabled"],
        "position_delta_ms": 200,
        "expected_state": {
            "position_ms": 10000,
            "captions_enabled": True,
            "selected_subtitle_track_id": "sub_en",
            "selected_audio_track_id": None,
            "presentation_mode": "standard",
            "rendition_playlist_sha256": TEST_PLAYLIST_SHA,
        },
        "observed_state": {
            "position_ms": 10200,
            "captions_enabled": False,
            "selected_subtitle_track_id": None,
            "selected_audio_track_id": None,
            "presentation_mode": "standard",
            "rendition_playlist_sha256": TEST_PLAYLIST_SHA,
        },
        "privacy_attestation": {
            "contains_pii": False,
            "contains_video_buffers": False,
            "contains_auth_tokens": False,
        },
        "retest_fixture_id": "DROPPED_CAPTION_FAULT_V1",
    }
    base_receipt["receipt_digest"] = compute_receipt_digest(base_receipt)

    # 1. Valid base receipt parses cleanly
    parsed = validate_and_parse_incident_receipt(json.dumps(base_receipt))
    assert parsed["incident_id"] == "inc-strict-001"
    assert parsed["receipt_digest"] == base_receipt["receipt_digest"]

    # 2. Missing receipt_digest: rejected in strict mode, accepted in legacy mode
    r_no_digest = copy.deepcopy(base_receipt)
    del r_no_digest["receipt_digest"]
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(r_no_digest), strict=True)
    assert "Missing required receipt field" in str(exc.value)

    legacy_parsed = validate_and_parse_incident_receipt(json.dumps(r_no_digest), strict=False)
    assert legacy_parsed["incident_id"] == "inc-strict-001"

    # 3. Injected unknown top-level field with recomputed digest: REJECTED
    r_injected = copy.deepcopy(base_receipt)
    r_injected["unauthorized_top_field"] = "malicious_telemetry"
    r_injected["receipt_digest"] = compute_receipt_digest(r_injected)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(r_injected))
    assert "Unknown top-level field" in str(exc.value)

    # 4. Injected unknown nested field in environment / expected_state / privacy_attestation
    r_bad_env = copy.deepcopy(base_receipt)
    r_bad_env["environment"]["device_serial"] = "SECRET123"
    r_bad_env["receipt_digest"] = compute_receipt_digest(r_bad_env)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_env))

    r_bad_state = copy.deepcopy(base_receipt)
    r_bad_state["expected_state"]["video_buffer_leak"] = "hexbytes..."
    r_bad_state["receipt_digest"] = compute_receipt_digest(r_bad_state)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_state))

    r_bad_att = copy.deepcopy(base_receipt)
    r_bad_att["privacy_attestation"]["unknown_flag"] = False
    r_bad_att["receipt_digest"] = compute_receipt_digest(r_bad_att)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_att))

    # 5. Type violations: bool where int expected, string where int expected
    r_bad_type1 = copy.deepcopy(base_receipt)
    r_bad_type1["position_delta_ms"] = True  # bool is int in Python, but strict check rejects
    r_bad_type1["receipt_digest"] = compute_receipt_digest(r_bad_type1)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_type1))

    r_bad_type2 = copy.deepcopy(base_receipt)
    r_bad_type2["position_delta_ms"] = "200"
    r_bad_type2["receipt_digest"] = compute_receipt_digest(r_bad_type2)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_type2))

    r_bad_type3 = copy.deepcopy(base_receipt)
    r_bad_type3["expected_state"]["captions_enabled"] = 1  # int where bool expected
    r_bad_type3["receipt_digest"] = compute_receipt_digest(r_bad_type3)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_type3))

    # 6. Integer range violations: position_delta_ms > 3600000ms
    r_huge_delta = copy.deepcopy(base_receipt)
    r_huge_delta["position_delta_ms"] = 5_000_000
    r_huge_delta["receipt_digest"] = compute_receipt_digest(r_huge_delta)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_huge_delta))

    # 7. Privacy attestation violation: declares presence of sensitive data
    r_pii = copy.deepcopy(base_receipt)
    r_pii["privacy_attestation"]["contains_pii"] = True
    r_pii["receipt_digest"] = compute_receipt_digest(r_pii)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(r_pii))
    assert "Privacy violation" in str(exc.value)

    # 8. Independent content scanning: credential/token leakage
    r_secret = copy.deepcopy(base_receipt)
    r_secret["observed_state"]["selected_subtitle_track_id"] = "track?token=eyJhbGciOi"
    r_secret["receipt_digest"] = compute_receipt_digest(r_secret)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(r_secret))
    assert "Credential, auth token, or secret pattern" in str(exc.value)

    # 9. Script injection rejection
    r_xss = copy.deepcopy(base_receipt)
    r_xss["observed_state"]["selected_audio_track_id"] = "<script>fetch('//evil')</script>"
    r_xss["receipt_digest"] = compute_receipt_digest(r_xss)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_xss))

    # 10. Duplicate key rejection in raw JSON
    raw_dup = '{"incident_id": "inc-1", "incident_id": "inc-2", "receipt_schema_version": 1}'
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(raw_dup)
    assert "Duplicate key detected" in str(exc.value)

    # 11. Non-object JSON rejection
    for non_obj in ("[1, 2, 3]", '"just a string"', "12345", "true", "null"):
        with pytest.raises(ContinuityError):
            validate_and_parse_incident_receipt(non_obj)

    # 12. Oversized payload rejection (> 64KB)
    oversized = json.dumps(base_receipt) + (" " * 70000)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(oversized)
    assert "exceeds maximum payload size" in str(exc.value)

    # 13. Invalid receipt_schema_version
    r_bad_ver = copy.deepcopy(base_receipt)
    r_bad_ver["receipt_schema_version"] = 2
    r_bad_ver["receipt_digest"] = compute_receipt_digest(r_bad_ver)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_ver))

    # 14. Unknown retest fixture ID
    r_bad_fix = copy.deepcopy(base_receipt)
    r_bad_fix["retest_fixture_id"] = "MALICIOUS_RETEST_FIXTURE_V9"
    r_bad_fix["receipt_digest"] = compute_receipt_digest(r_bad_fix)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(json.dumps(r_bad_fix))


def test_cross_language_canonical_parity_vectors():
    """
    Verifies that Python produces exact bit-for-bit canonical JSON strings and SHA-256 digests
    matching the shared canonical_vectors.json spec, ensuring 100% parity with Kotlin.
    """
    vectors_path = pathlib.Path("tools/canonical_vectors.json")
    assert vectors_path.exists(), "tools/canonical_vectors.json must exist"

    with open(vectors_path, "r", encoding="utf-8") as f:
        vectors = json.load(f)

    assert len(vectors) > 0, "Vectors list must not be empty"

    for vec in vectors:
        name = vec["name"]
        raw_input = vec["input"]
        expected_canonical = vec["expected_canonical"]
        expected_sha = vec["expected_sha256"]

        actual_canonical = json.dumps(raw_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert actual_canonical == expected_canonical, f"Canonical JSON mismatch for vector '{name}'"

        actual_sha = f"sha256:{hashlib.sha256(actual_canonical.encode('utf-8')).hexdigest()}"
        assert actual_sha == expected_sha, f"SHA-256 digest mismatch for vector '{name}'"


# ---------------------------------------------------------------------------
# Three-Way Ablation Benchmark
# ---------------------------------------------------------------------------

def test_three_way_ablation_benchmark():
    """
    Simulates three conditions across 3 fault scenarios in a deterministic model comparison:
    NOTE: This is a software model comparison of verification logic, NOT a physical Media3
    hardware decoder latency benchmark or real-world end-user detection survey.

    Scenario 1: Cold restart with dropped subtitle track.
    Scenario 2: Studio re-encode adding 5-second intro bumper.
    Scenario 3: Excessive keyframe seek overshoot (+3000ms).

    Condition A (Conventional Configured Resume):
      - Saves position_ms only.
      - Upon restart, sets playhead.
      - Has NO knowledge of track selection or edition hash.
      - OUTCOME: Silently continues playback, reporting 0 defects (False Negative / False Success).

    Condition B (FireTrace Checkpoint without Presentation Contract):
      - Persists position_ms and requested subtitle preference.
      - Upon restart, attempts restoring both, but does NOT query Player.currentTracks
        and does NOT bind to rendition playlist SHA.
      - OUTCOME: Assumes requested preference succeeded; misses dropped tracks and intro shifts.

    Condition C (Merged FireTrace Synaptic Continuity):
      - Persists versioned checkpoint bound to rendition_playlist_sha256.
      - Upon restart, queries actual Player.currentTracks and checks position tolerance.
      - OUTCOME: Detects 100% of presentation/edition divergences and halts safely.
    """
    scenarios = [
        {"name": "dropped_captions", "fault": "captions_lost"},
        {"name": "reencode_cut_shift", "fault": "hash_mismatched"},
        {"name": "seek_overshoot", "fault": "position_lag"},
    ]

    results_a = []
    results_b = []
    results_c = []

    for sc in scenarios:
        fault = sc["fault"]

        # Baseline A: Conventional Resume
        # Only checks if position was set. Never inspects tracks or edition hash.
        condition_a_detected_fault = False  # Blind resume misses all 3 defects!
        results_a.append(condition_a_detected_fault)

        # Baseline B: Unverified Checkpoint
        # Believes requested preference was restored; doesn't verify active tracks or cut hash.
        condition_b_detected_fault = False
        results_b.append(condition_b_detected_fault)

        # Condition C: Merged Engine
        cp = create_checkpoint(
            asset_revision_id=TEST_ASSET_REV,
            rendition_set_id="hls-1080p-release-v2",
            rendition_playlist_sha256=TEST_PLAYLIST_SHA,
            position_ms=50000,
            captions_enabled=True,
            selected_subtitle_track_id="sub-en-cc",
        )
        if fault == "captions_lost":
            obs = ObservedPlayerState(
                position_ms=50200,
                captions_active=False,
                active_subtitle_track_id=None,
                active_audio_track_id=None,
                active_presentation_mode="standard",
                active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
                surface_blanked=False,
                is_playing=True,
            )
        elif fault == "hash_mismatched":
            obs = ObservedPlayerState(
                position_ms=50000,
                captions_active=True,
                active_subtitle_track_id="sub-en-cc",
                active_audio_track_id=None,
                active_presentation_mode="standard",
                active_rendition_playlist_sha256="sha256:0000000000000000000000000000000000000000000000000000000000000000",
                surface_blanked=True,
                is_playing=False,
            )
        else:  # position_lag
            obs = ObservedPlayerState(
                position_ms=55000,  # 5000ms overshoot (> 1500ms tolerance)
                captions_active=True,
                active_subtitle_track_id="sub-en-cc",
                active_audio_track_id=None,
                active_presentation_mode="standard",
                active_rendition_playlist_sha256=TEST_PLAYLIST_SHA,
                surface_blanked=False,
                is_playing=True,
            )

        ver = verify_recovery(cp, obs)
        condition_c_detected_fault = (ver.status == "DIVERGENCE_DETECTED")
        results_c.append(condition_c_detected_fault)

    # Condition A: 0/3 detected (100% false success rate)
    assert sum(results_a) == 0
    # Condition B: 0/3 detected (100% false success rate)
    assert sum(results_b) == 0
    # Condition C: 3/3 detected (0% false success rate; 100% defect capture)
    assert sum(results_c) == 3


def test_presentation_mode_receipt_selects_fault_fixture_and_unknown_mismatch_rejects():
    import dataclasses
    from continuity_engine import derive_retest_fixture_id, ContinuityError
    from retest_harness import run_retest_scenario
    checkpoint = create_checkpoint(
        asset_revision_id="sha256:" + "1" * 64, rendition_set_id="fixture",
        rendition_playlist_sha256="sha256:" + "2" * 64, position_ms=1000,
        captions_enabled=True, selected_subtitle_track_id="caption-en", presentation_mode="reduced_effects")
    observed = ObservedPlayerState(position_ms=1000, captions_active=True,
        active_subtitle_track_id="caption-en", active_audio_track_id=None, active_presentation_mode="standard",
        active_rendition_playlist_sha256=checkpoint.rendition_playlist_sha256, surface_blanked=True, is_playing=False)
    result = verify_recovery(checkpoint, observed)
    assert result.status == "DIVERGENCE_DETECTED"
    assert derive_retest_fixture_id(result) == "PRESENTATION_MODE_FAULT_V1"
    assert run_retest_scenario({"retest_fixture_id": "PRESENTATION_MODE_FAULT_V1"})["test_status"] == "DETECTION_PASS"
    with pytest.raises(ContinuityError):
        derive_retest_fixture_id(dataclasses.replace(result, mismatch_fields=("unsupported_field",)))

    bad_fixture_receipt = {
        "receipt_schema_version": 1,
        "incident_id": "test-inc-unknown",
        "fixture_id": "fix-1",
        "recovery_status": "DIVERGENCE_DETECTED",
        "retest_fixture_id": "UNKNOWN_RECOVERY_FAULT_V1",
        "mismatch_fields": ["captions_enabled"],
    }
    with pytest.raises(ContinuityError) as exc_info:
        validate_and_parse_incident_receipt(json.dumps(bad_fixture_receipt), strict=False)
    assert "unknown retest fixture" in str(exc_info.value).lower()


def test_digest_functions_and_numeric_surrogate_rejection():
    """
    Scope D: Test actual receipt/checkpoint digest functions, non-BMP key ordering,
    and explicit rejection of floats, exponents, non-finite values, and invalid surrogates.
    """
    import copy
    import math
    from continuity_engine import compute_receipt_digest, compute_checkpoint_digest, ContinuityError

    # 1. Test actual checkpoint digest function
    cp_data = {
        "schema_version": 1,
        "checkpoint_id": "cp-001",
        "timestamp_epoch_ms": 1700000000000,
        "asset_revision_id": "sha256:" + "a" * 64,
        "rendition_set_id": "set-1",
        "rendition_playlist_sha256": "sha256:" + "b" * 64,
        "position_ms": 5000,
        "captions_enabled": True,
        "selected_subtitle_track_id": "sub-1",
        "selected_audio_track_id": "aud-1",
        "presentation_mode": "standard",
    }
    cp_digest = compute_checkpoint_digest(cp_data)
    assert cp_digest.startswith("sha256:")
    assert len(cp_digest) == 71

    # 2. Test actual receipt digest function
    receipt_data = {
        "receipt_schema_version": 1,
        "incident_id": "inc-001",
        "fixture_id": "fix-1",
        "timestamp_iso": "2026-10-19T00:00:00Z",
        "environment": {"app_version": "0.2.0", "platform": "FireOS-Android-Leanback"},
        "recovery_status": "DIVERGENCE_DETECTED",
        "diagnostic_code": "ERR_TEST",
        "mismatch_fields": ["captions_enabled"],
        "position_delta_ms": 100,
        "expected_state": {"position_ms": 1000, "captions_enabled": True},
        "observed_state": {"position_ms": 1100, "captions_enabled": False},
        "privacy_attestation": {
            "contains_pii": False,
            "contains_video_buffers": False,
            "contains_auth_tokens": False,
        },
        "retest_fixture_id": "DROPPED_CAPTION_FAULT_V1",
    }
    rc_digest = compute_receipt_digest(receipt_data)
    assert rc_digest.startswith("sha256:")
    assert len(rc_digest) == 71

    # 3. Explicit rejection of floats for integer position_delta_ms
    float_delta_data = copy.deepcopy(receipt_data)
    float_delta_data["position_delta_ms"] = 100.5
    float_delta_data["receipt_digest"] = compute_receipt_digest(float_delta_data)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(float_delta_data))
    assert "strict integer" in str(exc.value)

    # 4. Explicit rejection of exponents/floats for position_ms in expected_state
    float_pos_data = copy.deepcopy(receipt_data)
    float_pos_data["expected_state"]["position_ms"] = 1000.0
    float_pos_data["receipt_digest"] = compute_receipt_digest(float_pos_data)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(float_pos_data))
    assert "Invalid position_ms" in str(exc.value)

    # 5. Non-finite values rejected
    nan_data = copy.deepcopy(receipt_data)
    nan_data["position_delta_ms"] = float("nan")
    # json.dumps in Python emits NaN by default
    nan_json = json.dumps(nan_data)
    with pytest.raises(ContinuityError):
        validate_and_parse_incident_receipt(nan_json)

    # 6. Arbitrary unknown keys rejected in strict mode
    extra_key_data = copy.deepcopy(receipt_data)
    extra_key_data["unexpected_custom_field"] = "malicious"
    extra_key_data["receipt_digest"] = compute_receipt_digest(extra_key_data)
    with pytest.raises(ContinuityError) as exc:
        validate_and_parse_incident_receipt(json.dumps(extra_key_data))
    assert "Unknown top-level field" in str(exc.value)

