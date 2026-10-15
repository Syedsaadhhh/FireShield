"""
P0 Adversarial Boundary & Ed25519 v2 Contract Compliance Test Suite for FireShieldRuntime.

Note: Passing these deterministic state-machine and cryptographic contract validation
unit tests verifies software state transitions and schema enforcement only; it does NOT
establish clinical or medical safety.
"""

import copy
import hashlib
import json
import pytest

from state_engine import (
    DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    SCHEMA_VERSION_V1_LEGACY_HMAC,
    SCHEMA_VERSION_V2_ED25519,
    FireShieldRuntime,
    FireShieldRuntimeError,
    FireShieldState,
    PlaybackSessionContext,
    PlayerActionType,
    SidecarContractValidator,
    build_signed_sidecar_json,
    build_signed_v2_sidecar_json,
    compute_canonical_manifest_digest,
    compute_ed25519_signature,
    compute_hmac_signature,
    derive_ed25519_public_key_hex,
)

# Fixed deterministic test-only Ed25519 seed (32 bytes) and key ID
TEST_ED25519_SEED = hashlib.sha256(b"fireshield-fixed-unit-test-ed25519-seed-2026").digest()
TEST_KEY_ID = "pub-01-ed25519-test-key"
TEST_PUBLIC_KEY_HEX = derive_ed25519_public_key_hex(TEST_ED25519_SEED)
TEST_TRUSTED_KEYS = {TEST_KEY_ID: TEST_PUBLIC_KEY_HEX}

OTHER_ED25519_SEED = hashlib.sha256(b"fireshield-rogue-or-rotated-ed25519-seed-2026").digest()
LEGACY_HMAC_SECRET = b"fireshield-legacy-v1-hmac-test-secret"

VALID_ASSET_HASH = f"sha256:{hashlib.sha256(b'SOURCE_MEZZANINE_REV_2').hexdigest()}"
VALID_PLAYLIST_HASH = f"sha256:{hashlib.sha256(b'HLS_VARIANT_PLAYLIST_V2').hexdigest()}"


def _make_session(
    *,
    tenant_id: str = "publisher-01",
    asset_revision_id: str = VALID_ASSET_HASH,
    rendition_set_id: str = "release-2026-09-28-v2",
    rendition_playlist_sha256: str = VALID_PLAYLIST_HASH,
    allow_auto_seek: bool = False,
) -> PlaybackSessionContext:
    return PlaybackSessionContext(
        tenant_id=tenant_id,
        asset_revision_id=asset_revision_id,
        rendition_set_id=rendition_set_id,
        rendition_playlist_sha256=rendition_playlist_sha256,
        allow_auto_seek_on_pause_or_skip=allow_auto_seek,
    )


def _default_intervals():
    return [
        {
            "interval_id": "int-001",
            "start_ms": 10000,
            "end_ms": 12000,
            "safe_resume_ms": 12500,
            "types": ["luminance_flash"],
            "policy": "pause_or_skip",
            "context_cue_ids": ["cue-10", "cue-11"],
        },
        {
            "interval_id": "int-002",
            "start_ms": 20000,
            "end_ms": 20300,
            "safe_resume_ms": 20600,
            "types": ["saturated_red", "spatial_pattern"],
            "policy": "seek_to_safe_resume",
            "context_cue_ids": ["cue-25"],
        },
    ]


_UNSET = object()


def _make_signed_manifest(
    *,
    tenant_id: str = "publisher-01",
    asset_revision_id: str = VALID_ASSET_HASH,
    rendition_set_id: str = "release-2026-09-28-v2",
    rendition_playlist_sha256: str = VALID_PLAYLIST_HASH,
    detector_version: str = "fireshield-screening-proto:0.3.0-sdr-bt709-2dfft",
    review_state: str = "approved",
    intervals: object = _UNSET,
    key_id: str = TEST_KEY_ID,
    private_key_seed: bytes = TEST_ED25519_SEED,
    zero_findings_attested: bool = False,
) -> str:
    if intervals is _UNSET:
        intervals = _default_intervals()
    return build_signed_v2_sidecar_json(
        tenant_id=tenant_id,
        asset_revision_id=asset_revision_id,
        rendition_set_id=rendition_set_id,
        rendition_playlist_sha256=rendition_playlist_sha256,
        detector_version=detector_version,
        review_state=review_state,
        intervals=intervals,  # type: ignore[arg-type]
        key_id=key_id,
        private_key_seed_32b=private_key_seed,
        zero_findings_attested=zero_findings_attested,
    )


def _resign_v2_dict(
    payload: dict,
    *,
    private_seed: bytes = TEST_ED25519_SEED,
) -> str:
    d = copy.deepcopy(payload)
    d.pop("manifest_digest", None)
    d.pop("signature", None)
    digest = compute_canonical_manifest_digest(d)
    d["manifest_digest"] = digest
    key_id = str(d.get("key_id", TEST_KEY_ID))
    signing_alg = str(d.get("signing_alg", "Ed25519"))
    schema_ver = d.get("schema_version", SCHEMA_VERSION_V2_ED25519)
    d["signature"] = compute_ed25519_signature(
        manifest_digest=digest,
        key_id=key_id,
        private_seed_32b=private_seed,
        schema_version=schema_ver if isinstance(schema_ver, int) else 2,
        signing_alg=signing_alg,
    )
    return json.dumps(d)


# ---------------------------------------------------------------------------
# 1. Missing Fields, Schema Version, Ed25519 Public-Key & Downgrade Protection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_field",
    [
        "schema_version",
        "signing_alg",
        "key_id",
        "tenant_id",
        "asset_revision_id",
        "rendition_set_id",
        "rendition_playlist_sha256",
        "timebase",
        "detector_version",
        "review_state",
        "manifest_digest",
        "signature",
    ],
)
def test_reject_missing_required_v2_fields(missing_field: str):
    session = _make_session()
    raw = json.loads(_make_signed_manifest())
    del raw[missing_field]
    if missing_field not in ("manifest_digest", "signature"):
        tampered_str = _resign_v2_dict(raw)
        if missing_field == "signature":
            pass
    else:
        tampered_str = json.dumps(raw)

    runtime = FireShieldRuntime(session, tampered_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert runtime.state == FireShieldState.ERROR
    assert not runtime.verified
    cmd = runtime.update_player_head(1000)
    assert cmd.action == PlayerActionType.ERROR
    assert cmd.requires_surface_blanking is True


@pytest.mark.parametrize("bad_version", [0, 3, "2", True, None, -1])
def test_reject_invalid_schema_version(bad_version):
    session = _make_session()
    raw = json.loads(_make_signed_manifest())
    raw["schema_version"] = bad_version
    runtime = FireShieldRuntime(session, _resign_v2_dict(raw), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert runtime.state == FireShieldState.ERROR
    cmd = runtime.update_player_head(0)
    assert cmd.action == PlayerActionType.ERROR
    assert cmd.requires_surface_blanking is True


def test_ed25519_signature_and_downgrade_attacks_rejected():
    session = _make_session()
    valid_v2_str = _make_signed_manifest()
    raw = json.loads(valid_v2_str)

    # 1. Tamper with payload without updating manifest_digest
    tampered_payload = copy.deepcopy(raw)
    tampered_payload["intervals"][0]["start_ms"] = 10500
    rt_bad_digest = FireShieldRuntime(session, json.dumps(tampered_payload), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_bad_digest.state == FireShieldState.ERROR
    assert "manifest_digest integrity mismatch" in (rt_bad_digest.error_reason or "")

    # 2. Signed by wrong/rogue Ed25519 private key claiming TEST_KEY_ID
    rogue_signed_str = _make_signed_manifest(
        key_id=TEST_KEY_ID,
        private_key_seed=OTHER_ED25519_SEED,
    )
    rt_bad_sig = FireShieldRuntime(session, rogue_signed_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_bad_sig.state == FireShieldState.ERROR
    assert "ed25519 signature verification failed" in (rt_bad_sig.error_reason or "").lower()

    # 3. Unknown or rotated-out key_id not in client's trusted_public_keys
    unknown_key_str = _make_signed_manifest(
        key_id="revoked-key-2025",
        private_key_seed=OTHER_ED25519_SEED,
    )
    rt_unknown_kid = FireShieldRuntime(session, unknown_key_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_unknown_kid.state == FireShieldState.ERROR
    assert "unknown or untrusted publisher key_id" in (rt_unknown_kid.error_reason or "").lower()

    # 4. Unsupported signing_alg in v2 manifest (e.g. 'HMAC-SHA256' or 'RS256')
    bad_alg_dict = copy.deepcopy(raw)
    bad_alg_dict["signing_alg"] = "HMAC-SHA256"
    rt_bad_alg = FireShieldRuntime(
        session, _resign_v2_dict(bad_alg_dict), trusted_public_keys=TEST_TRUSTED_KEYS
    )
    assert rt_bad_alg.state == FireShieldState.ERROR
    assert "signing_alg" in (rt_bad_alg.error_reason or "")

    # 5. Malformed Ed25519 signature format (wrong length or hmac-sha256 prefix on v2)
    hmac_on_v2 = copy.deepcopy(raw)
    hmac_on_v2["signature"] = compute_hmac_signature(hmac_on_v2["manifest_digest"], LEGACY_HMAC_SECRET)
    rt_hmac_on_v2 = FireShieldRuntime(session, json.dumps(hmac_on_v2), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_hmac_on_v2.state == FireShieldState.ERROR
    assert "downgrade" in (rt_hmac_on_v2.error_reason or "").lower()

    # 6. Downgrade to schema_version=1 (HMAC) rejected by production verifier (allow_legacy_v1_hmac=False)
    v1_hmac_str = build_signed_sidecar_json(
        tenant_id=session.tenant_id,
        asset_revision_id=session.asset_revision_id,
        rendition_set_id=session.rendition_set_id,
        rendition_playlist_sha256=session.rendition_playlist_sha256,
        detector_version="fireshield-screening-proto:0.3.0",
        review_state="approved",
        intervals=_default_intervals(),
        hmac_secret=LEGACY_HMAC_SECRET,
        schema_version=SCHEMA_VERSION_V1_LEGACY_HMAC,
    )
    rt_v1_rejected = FireShieldRuntime(
        session,
        v1_hmac_str,
        trusted_public_keys=TEST_TRUSTED_KEYS,
        allow_legacy_v1_hmac=False,
        hmac_secret=LEGACY_HMAC_SECRET,
    )
    assert rt_v1_rejected.state == FireShieldState.ERROR
    assert "downgrade to legacy schema_version=1" in (rt_v1_rejected.error_reason or "").lower()

    # 7. Legacy v1 HMAC accepted ONLY when explicitly opted in via allow_legacy_v1_hmac=True
    rt_v1_opt_in = FireShieldRuntime(
        session,
        v1_hmac_str,
        allow_legacy_v1_hmac=True,
        hmac_secret=LEGACY_HMAC_SECRET,
    )
    assert rt_v1_opt_in.state == FireShieldState.ARMED


# ---------------------------------------------------------------------------
# 2. Source Revision & Stale Rendition Rejection
# ---------------------------------------------------------------------------

def test_fail_closed_on_source_hash_or_stale_rendition_mismatch():
    wrong_asset_session = _make_session(
        asset_revision_id=f"sha256:{hashlib.sha256(b'DIFFERENT_CUT').hexdigest()}"
    )
    rt_asset = FireShieldRuntime(wrong_asset_session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_asset.state == FireShieldState.ERROR
    cmd_asset = rt_asset.update_player_head(1000)
    assert cmd_asset.action == PlayerActionType.ERROR
    assert cmd_asset.requires_surface_blanking is True

    stale_set_session = _make_session(rendition_set_id="release-2026-09-28-v3-reencoded")
    rt_set = FireShieldRuntime(stale_set_session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_set.state == FireShieldState.ERROR
    assert "rendition_set_id" in (rt_set.error_reason or "")

    stale_playlist_session = _make_session(
        rendition_playlist_sha256=f"sha256:{hashlib.sha256(b'EDITED_HLS_PLAYLIST').hexdigest()}"
    )
    rt_playlist = FireShieldRuntime(
        stale_playlist_session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS
    )
    assert rt_playlist.state == FireShieldState.ERROR
    assert "Stale rendition_playlist_sha256" in (rt_playlist.error_reason or "")


# ---------------------------------------------------------------------------
# 3. Malformed Interval Types, Margins, and Ordering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_interval_patch,expected_err_substr",
    [
        ({"start_ms": True}, "strict integers"),
        ({"end_ms": 12000.5}, "strict integers"),
        ({"safe_resume_ms": "12500"}, "strict integers"),
        ({"start_ms": -10}, "negative start_ms"),
        ({"start_ms": 13000, "end_ms": 12000, "safe_resume_ms": 13500}, "end_ms (12000) < start_ms (13000)"),
        ({"safe_resume_ms": 12000}, "violates safe_resume_ms margin"),
        ({"safe_resume_ms": 12000 + DEFAULT_MIN_SAFE_RESUME_MARGIN_MS - 1}, "violates safe_resume_ms margin"),
        ({"types": []}, "non-empty 'types' list"),
        ({"types": ["unknown_hazard_kind"]}, "unsupported hazard type"),
        ({"policy": "warn_only_overlay"}, "unsupported policy"),
        ({"context_cue_ids": [123]}, "invalid context_cue_ids"),
    ],
)
def test_reject_malformed_interval_fields(bad_interval_patch: dict, expected_err_substr: str):
    session = _make_session()
    intervals = _default_intervals()
    intervals[0].update(bad_interval_patch)
    manifest_str = _make_signed_manifest(intervals=[intervals[0]])
    runtime = FireShieldRuntime(session, manifest_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert runtime.state == FireShieldState.ERROR
    assert expected_err_substr in (runtime.error_reason or "")


def test_reject_unsorted_and_overlapping_intervals():
    session = _make_session()

    unsorted_intervals = [
        {
            "interval_id": "i1",
            "start_ms": 15000,
            "end_ms": 16000,
            "safe_resume_ms": 16500,
            "types": ["luminance_flash"],
            "policy": "pause_before_interval",
        },
        {
            "interval_id": "i2",
            "start_ms": 10000,
            "end_ms": 11000,
            "safe_resume_ms": 11500,
            "types": ["luminance_flash"],
            "policy": "pause_before_interval",
        },
    ]
    rt_unsorted = FireShieldRuntime(
        session, _make_signed_manifest(intervals=unsorted_intervals), trusted_public_keys=TEST_TRUSTED_KEYS
    )
    assert rt_unsorted.state == FireShieldState.ERROR
    assert "Unsorted or overlapping" in (rt_unsorted.error_reason or "")

    margin_overlap_intervals = [
        {
            "interval_id": "i1",
            "start_ms": 10000,
            "end_ms": 12000,
            "safe_resume_ms": 13000,
            "types": ["luminance_flash"],
            "policy": "pause_before_interval",
        },
        {
            "interval_id": "i2",
            "start_ms": 12500,
            "end_ms": 14000,
            "safe_resume_ms": 14500,
            "types": ["saturated_red"],
            "policy": "pause_before_interval",
        },
    ]
    rt_margin_overlap = FireShieldRuntime(
        session, _make_signed_manifest(intervals=margin_overlap_intervals), trusted_public_keys=TEST_TRUSTED_KEYS
    )
    assert rt_margin_overlap.state == FireShieldState.ERROR
    assert "before previous interval's safe_resume_ms" in (rt_margin_overlap.error_reason or "")


# ---------------------------------------------------------------------------
# 4. Review State & Empty / Absent Intervals Rules
# ---------------------------------------------------------------------------

def test_no_reviewed_intervals_and_review_state_semantics():
    session = _make_session()

    unattested_empty_str = _make_signed_manifest(
        intervals=[], review_state="approved", zero_findings_attested=False
    )
    rt_unattested = FireShieldRuntime(session, unattested_empty_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_unattested.state == FireShieldState.UNREVIEWED
    assert not rt_unattested.verified
    cmd_unattested = rt_unattested.update_player_head(500)
    assert cmd_unattested.action == PlayerActionType.PAUSE
    assert cmd_unattested.requires_surface_blanking is True

    attested_empty_str = _make_signed_manifest(
        intervals=[], review_state="approved", zero_findings_attested=True
    )
    rt_attested = FireShieldRuntime(session, attested_empty_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_attested.state == FireShieldState.ARMED
    assert rt_attested.verified is True
    cmd_attested = rt_attested.update_player_head(500)
    assert cmd_attested.action == PlayerActionType.CONTINUE
    assert cmd_attested.requires_surface_blanking is False

    absent_attested_str = _make_signed_manifest(
        intervals=None, review_state="approved", zero_findings_attested=True
    )
    rt_absent_ok = FireShieldRuntime(session, absent_attested_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_absent_ok.state == FireShieldState.ARMED

    absent_unattested_str = _make_signed_manifest(
        intervals=None, review_state="approved", zero_findings_attested=False
    )
    rt_absent_bad = FireShieldRuntime(session, absent_unattested_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_absent_bad.state == FireShieldState.ERROR

    rejected_str = _make_signed_manifest(review_state="rejected")
    rt_rejected = FireShieldRuntime(session, rejected_str, trusted_public_keys=TEST_TRUSTED_KEYS)
    assert rt_rejected.state == FireShieldState.ERROR
    cmd_rejected = rt_rejected.update_player_head(0)
    assert cmd_rejected.action == PlayerActionType.ERROR
    assert cmd_rejected.requires_surface_blanking is True


# ---------------------------------------------------------------------------
# 5. Exact Tick Boundaries, Pre-Guard, Duplicate Ticks, Jump-Over, Seek, & Re-Entry
# ---------------------------------------------------------------------------

def test_tick_boundaries_pre_guard_and_duplicate_tick_idempotency():
    session = _make_session(allow_auto_seek=False)
    runtime = FireShieldRuntime(
        session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS, pre_guard_ms=500
    )
    assert runtime.state == FireShieldState.ARMED

    cmd_before = runtime.update_player_head(9499)
    assert cmd_before.state == FireShieldState.ARMED
    assert cmd_before.action == PlayerActionType.CONTINUE
    assert cmd_before.requires_surface_blanking is False

    cmd_at_guard = runtime.update_player_head(9500)
    assert cmd_at_guard.state == FireShieldState.INTERVENING
    assert cmd_at_guard.action == PlayerActionType.PAUSE
    assert cmd_at_guard.requires_surface_blanking is True
    assert cmd_at_guard.audit_evidence["trigger_kind"] == "PRE_GUARD_WINDOW"

    cmd_dup = runtime.update_player_head(9500)
    assert cmd_dup.state == FireShieldState.INTERVENING
    assert cmd_dup.action == PlayerActionType.HOLD
    assert cmd_dup.reason_code == "DUPLICATE_TICK_INTERVENTION_HOLD"
    assert cmd_dup.audit_evidence["duplicate_tick"] is True

    cmd_at_start = runtime.update_player_head(10000)
    assert cmd_at_start.state == FireShieldState.INTERVENING
    assert cmd_at_start.action == PlayerActionType.PAUSE
    assert cmd_at_start.audit_evidence["trigger_kind"] == "INSIDE_HAZARD_WINDOW"

    cmd_margin = runtime.update_player_head(12100)
    assert cmd_margin.state == FireShieldState.INTERVENING
    assert cmd_margin.action == PlayerActionType.PAUSE
    assert cmd_margin.reason_code == "HOLD_INSIDE_POST_HAZARD_SAFETY_MARGIN"

    cmd_bypass = runtime.request_interval_bypass_seek()
    assert cmd_bypass.action == PlayerActionType.SEEK
    assert cmd_bypass.target_seek_ms == 12500

    cmd_resolved = runtime.update_player_head(12500)
    assert cmd_resolved.state == FireShieldState.RESOLVED
    assert cmd_resolved.action == PlayerActionType.CONTINUE
    assert cmd_resolved.requires_surface_blanking is False


def test_playhead_jumping_over_short_interval_triggers_intervention():
    session = _make_session()
    runtime = FireShieldRuntime(
        session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS, pre_guard_ms=200
    )

    cmd1 = runtime.update_player_head(19500)
    assert cmd1.action == PlayerActionType.CONTINUE

    cmd_jump = runtime.update_player_head(20400)
    assert cmd_jump.state == FireShieldState.INTERVENING
    assert cmd_jump.action == PlayerActionType.SEEK
    assert cmd_jump.target_seek_ms == 20600
    assert cmd_jump.audit_evidence["trigger_kind"] == "PLAYHEAD_JUMPED_OVER_INTERVAL"


def test_seek_into_hazard_and_reset_reentry():
    session = _make_session()
    runtime = FireShieldRuntime(
        session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS, pre_guard_ms=500
    )

    cmd_seek_in = runtime.notify_seek(11000)
    assert cmd_seek_in.state == FireShieldState.INTERVENING
    assert cmd_seek_in.action == PlayerActionType.PAUSE
    assert cmd_seek_in.requires_surface_blanking is True

    runtime.request_interval_bypass_seek()
    cmd_res = runtime.update_player_head(12500)
    assert cmd_res.state == FireShieldState.RESOLVED

    cmd_rewind = runtime.notify_seek(5000)
    assert cmd_rewind.state == FireShieldState.ARMED
    assert cmd_rewind.action == PlayerActionType.CONTINUE

    cmd_reenter = runtime.update_player_head(9600)
    assert cmd_reenter.state == FireShieldState.INTERVENING
    assert cmd_reenter.action == PlayerActionType.PAUSE
    assert cmd_reenter.requires_surface_blanking is True


def test_failed_seek_handling_fails_closed():
    session = _make_session(allow_auto_seek=True)
    runtime = FireShieldRuntime(
        session, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS, pre_guard_ms=500
    )

    cmd1 = runtime.update_player_head(9500)
    assert cmd1.action == PlayerActionType.SEEK
    assert cmd1.target_seek_ms == 12500

    cmd_fail_1 = runtime.update_player_head(10100)
    assert cmd_fail_1.state == FireShieldState.INTERVENING
    assert cmd_fail_1.action == PlayerActionType.PAUSE
    assert cmd_fail_1.reason_code == "SEEK_INCOMPLETE_EMERGENCY_PAUSE"
    assert cmd_fail_1.requires_surface_blanking is True

    cmd_fail_2 = runtime.update_player_head(10200)
    assert cmd_fail_2.state == FireShieldState.ERROR
    assert cmd_fail_2.action == PlayerActionType.ERROR
    assert cmd_fail_2.reason_code == "SEEK_FAILED_MAX_RETRIES_EXCEEDED"
    assert cmd_fail_2.requires_surface_blanking is True


def test_rendition_switch_and_network_sidecar_errors_never_transition_unsafely():
    session_v1 = _make_session()
    runtime = FireShieldRuntime(session_v1, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS)
    assert runtime.update_player_head(2000).action == PlayerActionType.CONTINUE

    session_v2 = _make_session(
        rendition_set_id="release-2026-09-28-v3",
        rendition_playlist_sha256=f"sha256:{hashlib.sha256(b'HLS_V3').hexdigest()}",
    )
    cmd_stale_switch = runtime.on_rendition_switch(session_v2, _make_signed_manifest())
    assert cmd_stale_switch.state == FireShieldState.ERROR
    assert cmd_stale_switch.action == PlayerActionType.ERROR
    assert cmd_stale_switch.requires_surface_blanking is True

    runtime2 = FireShieldRuntime(session_v1, _make_signed_manifest(), trusted_public_keys=TEST_TRUSTED_KEYS)
    runtime2.update_player_head(10500)
    valid_v2_sidecar = _make_signed_manifest(
        rendition_set_id=session_v2.rendition_set_id,
        rendition_playlist_sha256=session_v2.rendition_playlist_sha256,
    )
    cmd_valid_switch = runtime2.on_rendition_switch(session_v2, valid_v2_sidecar)
    assert cmd_valid_switch.state == FireShieldState.INTERVENING
    assert cmd_valid_switch.action == PlayerActionType.PAUSE
    assert cmd_valid_switch.requires_surface_blanking is True

    cmd_net_err = runtime2.on_sidecar_error("CDN_503_SIDECAR_REVOKED")
    assert cmd_net_err.state == FireShieldState.ERROR
    assert cmd_net_err.action == PlayerActionType.ERROR
    assert cmd_net_err.requires_surface_blanking is True
