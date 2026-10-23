"""
FireTrace Synaptic Continuity Engine (P0 / P3).

Core Architecture & Boundaries:
1. Version-Bound Local Checkpointing:
   - Persists viewer's chosen presentation experience (position_ms, active caption track,
     audio track, presentation mode) bound to the media edition digest (rendition_playlist_sha256).
   - Atomic disk write (temp file + rename) with SHA-256 integrity checksum.
   - Keeps publisher-signed release metadata strictly separate from local viewer checkpoint.
   - NEVER embeds or uses publisher private signing keys on the client.
2. Active Recovery Verification:
   - Upon restart or reacquisition, actively queries observed player state (actual playhead,
     active tracks via Player.currentTracks, active rendition hash).
   - Compares expected checkpoint vs observed state with documented position tolerance
     (e.g., keyframe snapping lag) and track verification.
   - Detects silent failures that naive resume overlooks (dropped captions, cut drift).
3. Allowlisted Local Incident Receipt:
   - Emits a sanitized JSON incident receipt strictly allowlisting diagnostic fields.
   - Strips all viewer PII, account tokens, playback URLs with credentials, and video frames.
   - Rejects hostile strings (script tags, SQLi, control characters) and corrupt payloads.
4. Three-Way Ablation Benchmark:
   - Condition A: Conventional configured resume (position only; ignores dropped tracks and cut shifts).
   - Condition B: Unverified checkpoint (persists tracks, but assumes restore succeeded without querying).
   - Condition C: Merged FireTrace Synaptic Continuity (actively verifies tracks and rendition hash).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import re
import tempfile
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

CHECKPOINT_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
DEFAULT_POSITION_TOLERANCE_MS = 1500  # Keyframe snapping tolerance / checkpoint write lag
SUPPORTED_PRESENTATION_MODES = frozenset({"standard", "reduced_effects", "dialogue_boost"})

# Regex for strict validation of IDs and digests
SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,128}$")
SHA256_PREFIX_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")
HOSTILE_CHAR_REGEX = re.compile(r"[\x00-\x1f\x7f<>{};\'\"`]")


class ContinuityError(Exception):
    """Raised when checkpoint persistence, verification, or receipt parsing fails."""
    pass


@dataclasses.dataclass(frozen=True)
class ContinuityCheckpoint:
    """
    Locally persisted viewer session state.
    Contains zero publisher secrets and zero personal data.
    """
    schema_version: int
    checkpoint_id: str
    timestamp_epoch_ms: int
    asset_revision_id: str
    rendition_set_id: str
    rendition_playlist_sha256: str
    position_ms: int
    captions_enabled: bool
    selected_subtitle_track_id: Optional[str]
    selected_audio_track_id: Optional[str]
    presentation_mode: str
    integrity_digest: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint_id": self.checkpoint_id,
            "timestamp_epoch_ms": self.timestamp_epoch_ms,
            "asset_revision_id": self.asset_revision_id,
            "rendition_set_id": self.rendition_set_id,
            "rendition_playlist_sha256": self.rendition_playlist_sha256,
            "position_ms": self.position_ms,
            "captions_enabled": self.captions_enabled,
            "selected_subtitle_track_id": self.selected_subtitle_track_id,
            "selected_audio_track_id": self.selected_audio_track_id,
            "presentation_mode": self.presentation_mode,
            "integrity_digest": self.integrity_digest,
        }


def compute_checkpoint_digest(payload: Mapping[str, Any], ensure_ascii: bool = False) -> str:
    """Computes SHA-256 digest over canonical JSON representation excluding integrity_digest."""
    clean = {k: v for k, v in payload.items() if k != "integrity_digest"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def create_checkpoint(
    *,
    asset_revision_id: str,
    rendition_set_id: str,
    rendition_playlist_sha256: str,
    position_ms: int,
    captions_enabled: bool,
    selected_subtitle_track_id: Optional[str] = None,
    selected_audio_track_id: Optional[str] = None,
    presentation_mode: str = "standard",
    checkpoint_id: Optional[str] = None,
    timestamp_epoch_ms: Optional[int] = None,
) -> ContinuityCheckpoint:
    if position_ms < 0:
        raise ContinuityError(f"position_ms cannot be negative, got {position_ms}")
    if presentation_mode not in SUPPORTED_PRESENTATION_MODES:
        raise ContinuityError(f"Unsupported presentation_mode: {presentation_mode}")
    if not SHA256_PREFIX_REGEX.match(rendition_playlist_sha256):
        raise ContinuityError(f"Invalid rendition_playlist_sha256 format: {rendition_playlist_sha256}")

    cid = checkpoint_id or str(uuid.uuid4())
    ts = timestamp_epoch_ms or int(time.time() * 1000)

    data = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": cid,
        "timestamp_epoch_ms": ts,
        "asset_revision_id": asset_revision_id,
        "rendition_set_id": rendition_set_id,
        "rendition_playlist_sha256": rendition_playlist_sha256,
        "position_ms": position_ms,
        "captions_enabled": bool(captions_enabled),
        "selected_subtitle_track_id": selected_subtitle_track_id,
        "selected_audio_track_id": selected_audio_track_id,
        "presentation_mode": presentation_mode,
    }
    digest = compute_checkpoint_digest(data)
    return ContinuityCheckpoint(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        checkpoint_id=cid,
        timestamp_epoch_ms=ts,
        asset_revision_id=asset_revision_id,
        rendition_set_id=rendition_set_id,
        rendition_playlist_sha256=rendition_playlist_sha256,
        position_ms=position_ms,
        captions_enabled=bool(captions_enabled),
        selected_subtitle_track_id=selected_subtitle_track_id,
        selected_audio_track_id=selected_audio_track_id,
        presentation_mode=presentation_mode,
        integrity_digest=digest,
    )


def save_checkpoint_atomic(checkpoint: ContinuityCheckpoint, target_path: pathlib.Path) -> None:
    """Atomically writes checkpoint to disk using temp file write + rename."""
    target_path = pathlib.Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload_str = json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True)

    with tempfile.NamedTemporaryFile("w", dir=target_path.parent, delete=False, encoding="utf-8") as tf:
        tf.write(payload_str)
        tf.flush()
        os.fsync(tf.fileno())
        temp_name = tf.name

    os.replace(temp_name, str(target_path))


def load_checkpoint(file_path: pathlib.Path) -> ContinuityCheckpoint:
    """Loads and cryptographically validates local checkpoint integrity."""
    path = pathlib.Path(file_path)
    if not path.exists():
        raise ContinuityError(f"Checkpoint file does not exist: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContinuityError("Checkpoint root must be a JSON object")

    if raw.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ContinuityError(f"Unsupported checkpoint schema_version: {raw.get('schema_version')}")

    declared_digest = raw.get("integrity_digest")
    if not declared_digest:
        raise ContinuityError("Checkpoint missing integrity_digest")

    computed_digest = compute_checkpoint_digest(raw, ensure_ascii=False)
    if declared_digest != computed_digest:
        if raw.get("schema_version") == 1:
            legacy_computed = compute_checkpoint_digest(raw, ensure_ascii=True)
            if declared_digest == legacy_computed:
                computed_digest = legacy_computed
        if declared_digest != computed_digest:
            raise ContinuityError(f"Checkpoint integrity digest mismatch: declared={declared_digest}, computed={computed_digest}")

    return ContinuityCheckpoint(
        schema_version=raw["schema_version"],
        checkpoint_id=raw["checkpoint_id"],
        timestamp_epoch_ms=raw["timestamp_epoch_ms"],
        asset_revision_id=raw["asset_revision_id"],
        rendition_set_id=raw["rendition_set_id"],
        rendition_playlist_sha256=raw["rendition_playlist_sha256"],
        position_ms=raw["position_ms"],
        captions_enabled=raw["captions_enabled"],
        selected_subtitle_track_id=raw.get("selected_subtitle_track_id"),
        selected_audio_track_id=raw.get("selected_audio_track_id"),
        presentation_mode=raw.get("presentation_mode", "standard"),
        integrity_digest=declared_digest,
    )


@dataclasses.dataclass(frozen=True)
class ObservedPlayerState:
    """Actual player state queried from the native ExoPlayer after recovery."""
    position_ms: int
    captions_active: bool
    active_subtitle_track_id: Optional[str]
    active_audio_track_id: Optional[str]
    active_presentation_mode: str
    active_rendition_playlist_sha256: str
    surface_blanked: bool
    is_playing: bool


@dataclasses.dataclass(frozen=True)
class RecoveryVerificationResult:
    """Result of active expected vs actual recovery comparison."""
    status: str  # "VERIFIED_MATCH" or "DIVERGENCE_DETECTED"
    mismatch_fields: Tuple[str, ...]
    position_delta_ms: int
    expected_values: Dict[str, Any]
    observed_values: Dict[str, Any]
    diagnostic_code: str


def verify_recovery(
    checkpoint: ContinuityCheckpoint,
    observed: ObservedPlayerState,
    *,
    position_tolerance_ms: int = DEFAULT_POSITION_TOLERANCE_MS,
) -> RecoveryVerificationResult:
    """
    Compares expected checkpoint against actual observed player state.
    Detects dropped tracks, keyframe landing overshoot, and shifted cuts.
    """
    mismatches: List[str] = []
    expected = checkpoint.to_dict()
    actual = {
        "position_ms": observed.position_ms,
        "captions_enabled": observed.captions_active,
        "selected_subtitle_track_id": observed.active_subtitle_track_id,
        "selected_audio_track_id": observed.active_audio_track_id,
        "presentation_mode": observed.active_presentation_mode,
        "rendition_playlist_sha256": observed.active_rendition_playlist_sha256,
    }

    # 1. Edition / Playlist digest check (detects cut shifts, new intros, re-encodes)
    if checkpoint.rendition_playlist_sha256 != observed.active_rendition_playlist_sha256:
        mismatches.append("rendition_playlist_sha256")

    # 2. Position tolerance check
    pos_delta = abs(observed.position_ms - checkpoint.position_ms)
    if pos_delta > position_tolerance_ms:
        mismatches.append("position_ms")

    # 3. Caption / Subtitle check
    if checkpoint.captions_enabled != observed.captions_active:
        mismatches.append("captions_enabled")
    elif checkpoint.captions_enabled and checkpoint.selected_subtitle_track_id:
        if observed.active_subtitle_track_id != checkpoint.selected_subtitle_track_id:
            mismatches.append("selected_subtitle_track_id")

    # 4. Presentation mode check
    if checkpoint.presentation_mode != observed.active_presentation_mode:
        mismatches.append("presentation_mode")

    # 5. Audio track check
    if checkpoint.selected_audio_track_id is not None:
        if observed.active_audio_track_id is None or checkpoint.selected_audio_track_id != observed.active_audio_track_id:
            mismatches.append("selected_audio_track_id")

    if mismatches:
        status = "DIVERGENCE_DETECTED"
        diag_code = f"ERR_MISMATCH_{'_'.join(mismatches).upper()}"
    else:
        status = "VERIFIED_MATCH"
        diag_code = "RESTORE_VERIFIED_PASS"

    return RecoveryVerificationResult(
        status=status,
        mismatch_fields=tuple(mismatches),
        position_delta_ms=pos_delta,
        expected_values={k: expected[k] for k in actual.keys()},
        observed_values=actual,
        diagnostic_code=diag_code,
    )


# ---------------------------------------------------------------------------
# Allowlisted Incident Receipt (P3)
# ---------------------------------------------------------------------------

KNOWN_RETEST_FIXTURES = frozenset({
    "BENIGN_SUBTITLE_CONTINUITY_V1",
    "DROPPED_CAPTION_FAULT_V1",
    "STALE_CUT_INTRO_DRIFT_V1",
    "POSITION_OVERSHOOT_FAULT_V1",
    "DROPPED_AUDIO_FAULT_V1",
    "PRESENTATION_MODE_FAULT_V1",
})


def derive_retest_fixture_id(verification_result: RecoveryVerificationResult) -> str:
    """Derives the appropriate bounded retest fixture based on observed mismatch fields."""
    mismatches = set(verification_result.mismatch_fields)
    if "captions_enabled" in mismatches or "selected_subtitle_track_id" in mismatches:
        return "DROPPED_CAPTION_FAULT_V1"
    if "rendition_playlist_sha256" in mismatches:
        return "STALE_CUT_INTRO_DRIFT_V1"
    if "position_ms" in mismatches:
        return "POSITION_OVERSHOOT_FAULT_V1"
    if "selected_audio_track_id" in mismatches:
        return "DROPPED_AUDIO_FAULT_V1"
    if "presentation_mode" in mismatches:
        return "PRESENTATION_MODE_FAULT_V1"
    if mismatches:
        raise ContinuityError("No bounded retest fixture for these mismatch fields")
    return "BENIGN_SUBTITLE_CONTINUITY_V1"


MAX_RECEIPT_PAYLOAD_BYTES = 65536

ALLOWED_RECEIPT_TOP_LEVEL_FIELDS = frozenset({
    "receipt_schema_version",
    "incident_id",
    "fixture_id",
    "timestamp_iso",
    "environment",
    "recovery_status",
    "diagnostic_code",
    "mismatch_fields",
    "position_delta_ms",
    "expected_state",
    "observed_state",
    "privacy_attestation",
    "retest_fixture_id",
    "receipt_digest",
})

REQUIRED_RECEIPT_STRICT_FIELDS = frozenset({
    "receipt_schema_version",
    "incident_id",
    "fixture_id",
    "timestamp_iso",
    "environment",
    "recovery_status",
    "diagnostic_code",
    "mismatch_fields",
    "position_delta_ms",
    "expected_state",
    "observed_state",
    "privacy_attestation",
    "retest_fixture_id",
    "receipt_digest",
})

REQUIRED_RECEIPT_LEGACY_FIELDS = frozenset({
    "receipt_schema_version",
    "incident_id",
    "fixture_id",
    "recovery_status",
    "retest_fixture_id",
    "mismatch_fields",
})

ALLOWED_RECOVERY_STATUSES = frozenset({
    "DIVERGENCE_DETECTED",
    "RECOVERY_VERIFIED",
    "RECOVERY_FAILED_CLOSED",
    "BLOCKED",
})

ALLOWED_MISMATCH_FIELDS = frozenset({
    "captions_enabled",
    "selected_subtitle_track_id",
    "selected_audio_track_id",
    "rendition_playlist_sha256",
    "presentation_mode",
    "position_ms",
})

ALLOWED_ENVIRONMENT_FIELDS = frozenset({"app_version", "platform"})

ALLOWED_STATE_FIELDS = frozenset({
    "position_ms",
    "captions_enabled",
    "selected_subtitle_track_id",
    "selected_audio_track_id",
    "presentation_mode",
    "rendition_playlist_sha256",
})

ALLOWED_PRESENTATION_MODES = SUPPORTED_PRESENTATION_MODES

ALLOWED_PRIVACY_ATTESTATION_FIELDS = frozenset({
    "contains_pii",
    "contains_video_buffers",
    "contains_auth_tokens",
})

ISO_TIMESTAMP_REGEX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
DIGEST_REGEX = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_PATTERN_REGEX = re.compile(
    r"(?:password|token=|bearer\s+|aws_secret|secret_key|access_key|BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY|ssh-rsa)",
    re.IGNORECASE,
)


def compute_receipt_digest(payload: Mapping[str, Any], ensure_ascii: bool = False) -> str:
    """Computes SHA-256 digest over canonical JSON representation excluding receipt_digest."""
    clean = {k: v for k, v in payload.items() if k != "receipt_digest"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def sanitize_receipt_string(val: Any, max_len: int = 128) -> str:
    """Sanitizes strings in receipt, rejecting hostile script or control characters."""
    if val is None:
        return ""
    s = str(val).strip()
    if len(s) > max_len:
        s = s[:max_len]
    if HOSTILE_CHAR_REGEX.search(s):
        raise ContinuityError(f"Hostile or unsafe characters detected in receipt value: {s!r}")
    return s


def _scan_for_sensitive_content(val: Any) -> None:
    """Recursively scans parsed JSON structure for credential, auth, script, or control character patterns."""
    if isinstance(val, str):
        if len(val) > 1024:
            raise ContinuityError(f"String value exceeds safety limit: length={len(val)}")
        if SECRET_PATTERN_REGEX.search(val):
            raise ContinuityError("Credential, auth token, or secret pattern detected in receipt")
        if "<script" in val.lower() or "javascript:" in val.lower():
            raise ContinuityError("Script injection pattern detected in receipt value")
        for ch in val:
            if ord(ch) < 32 and ch not in "\t\n\r":
                raise ContinuityError("Unsafe control character detected in receipt value")
    elif isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(k, str):
                raise ContinuityError("All dictionary keys must be strings")
            _scan_for_sensitive_content(k)
            _scan_for_sensitive_content(v)
    elif isinstance(val, (list, tuple)):
        for item in val:
            _scan_for_sensitive_content(item)


def generate_incident_receipt(
    *,
    incident_id: str,
    fixture_id: str,
    verification_result: RecoveryVerificationResult,
    app_version: str = "0.2.0",
    platform_name: str = "FireOS-Android-Leanback",
    retest_fixture_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates an allowlisted, privacy-safe JSON incident receipt with cryptographic integrity digest.
    Excludes video buffers, personal credentials, URLs with auth tokens, and hardware serials.
    """
    clean_incident_id = sanitize_receipt_string(incident_id)
    clean_fixture_id = sanitize_receipt_string(fixture_id)
    clean_app_version = sanitize_receipt_string(app_version)
    clean_platform = sanitize_receipt_string(platform_name)

    chosen_retest_id = retest_fixture_id or derive_retest_fixture_id(verification_result)
    if chosen_retest_id not in KNOWN_RETEST_FIXTURES:
        raise ContinuityError(f"Unknown retest_fixture_id: {chosen_retest_id}")

    receipt: Dict[str, Any] = {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "incident_id": clean_incident_id,
        "fixture_id": clean_fixture_id,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "app_version": clean_app_version,
            "platform": clean_platform,
        },
        "recovery_status": verification_result.status,
        "diagnostic_code": verification_result.diagnostic_code,
        "mismatch_fields": list(verification_result.mismatch_fields),
        "position_delta_ms": verification_result.position_delta_ms,
        "expected_state": verification_result.expected_values,
        "observed_state": verification_result.observed_values,
        "privacy_attestation": {
            "contains_pii": False,
            "contains_video_buffers": False,
            "contains_auth_tokens": False,
        },
        "retest_fixture_id": chosen_retest_id,
    }
    receipt["receipt_digest"] = compute_receipt_digest(receipt)
    return receipt


def validate_and_parse_incident_receipt(
    raw_json_str: str, *, strict: bool = True
) -> Dict[str, Any]:
    """
    Parses and strictly validates an incident receipt against safety and closed schema invariants.

    In strict mode (default for cloud ingress and verification):
    - Payload length must be <= 64KB.
    - JSON duplicate keys are rejected.
    - Closed schema: unknown top-level or nested fields are rejected.
    - receipt_digest is required, must match sha256:[0-9a-f]{64}, and must match computed digest.
    - Exact types and finite integer bounds enforced.
    - Privacy attestation must be valid and declare zero sensitive data.
    - Recursive independent content scanning verifies no secrets, tokens, or scripts are present.
    """
    if not isinstance(raw_json_str, str):
        raise ContinuityError("Raw incident receipt payload must be a string")

    raw_bytes = raw_json_str.encode("utf-8")
    if len(raw_bytes) > MAX_RECEIPT_PAYLOAD_BYTES:
        raise ContinuityError(
            f"Incident receipt exceeds maximum payload size of {MAX_RECEIPT_PAYLOAD_BYTES} bytes (got {len(raw_bytes)})"
        )

    def _reject_dup_keys(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ContinuityError(f"Duplicate key detected in JSON receipt: {k!r}")
            result[k] = v
        return result

    try:
        data = json.loads(raw_json_str, object_pairs_hook=_reject_dup_keys)
    except ContinuityError:
        raise
    except Exception as exc:
        raise ContinuityError(f"Malformed JSON in incident receipt: {exc}") from exc

    if not isinstance(data, dict):
        raise ContinuityError("Incident receipt must be a JSON object")

    schema_ver = data.get("receipt_schema_version")
    if not isinstance(schema_ver, int) or isinstance(schema_ver, bool) or schema_ver != RECEIPT_SCHEMA_VERSION:
        raise ContinuityError(f"Unsupported or invalid receipt schema version: {schema_ver!r}")

    if strict:
        unknown_top = set(data.keys()) - ALLOWED_RECEIPT_TOP_LEVEL_FIELDS
        if unknown_top:
            raise ContinuityError(f"Unknown top-level field(s) in incident receipt: {sorted(unknown_top)}")

        missing_fields = REQUIRED_RECEIPT_STRICT_FIELDS - set(data.keys())
        if missing_fields:
            raise ContinuityError(f"Missing required receipt field(s): {sorted(missing_fields)}")
    else:
        missing_fields = REQUIRED_RECEIPT_LEGACY_FIELDS - set(data.keys())
        if missing_fields:
            raise ContinuityError(f"Missing required receipt field(s): {sorted(missing_fields)}")

    # incident_id, fixture_id
    for id_field in ("incident_id", "fixture_id"):
        val = data.get(id_field)
        if not isinstance(val, str) or not val.strip() or len(val) > 128:
            raise ContinuityError(f"Field {id_field!r} must be a non-empty string <= 128 chars")
        if HOSTILE_CHAR_REGEX.search(val):
            raise ContinuityError(f"Hostile characters in {id_field!r}: {val!r}")

    # timestamp_iso
    ts = data.get("timestamp_iso")
    if ts is not None:
        if not isinstance(ts, str) or not ISO_TIMESTAMP_REGEX.match(ts) or len(ts) > 64:
            raise ContinuityError(f"Field 'timestamp_iso' must be a valid ISO-8601 string, got {ts!r}")

    # recovery_status
    status = data.get("recovery_status")
    if not isinstance(status, str) or status not in ALLOWED_RECOVERY_STATUSES:
        raise ContinuityError(f"Invalid recovery_status: {status!r}")

    # diagnostic_code
    diag = data.get("diagnostic_code")
    if diag is not None:
        if not isinstance(diag, str) or len(diag) > 128:
            raise ContinuityError("Field 'diagnostic_code' must be a string <= 128 chars")

    # mismatch_fields
    mismatches = data.get("mismatch_fields")
    if not isinstance(mismatches, list):
        raise ContinuityError("Field 'mismatch_fields' must be a list")
    seen_mismatches = set()
    for m in mismatches:
        if not isinstance(m, str) or m not in ALLOWED_MISMATCH_FIELDS:
            raise ContinuityError(f"Unknown or invalid mismatch field: {m!r}")
        if m in seen_mismatches:
            raise ContinuityError(f"Duplicate mismatch field in list: {m!r}")
        seen_mismatches.add(m)

    # position_delta_ms
    delta = data.get("position_delta_ms")
    if delta is not None:
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise ContinuityError("Field 'position_delta_ms' must be a strict integer")
        if delta < -3_600_000 or delta > 3_600_000:
            raise ContinuityError(f"Field 'position_delta_ms' out of bounds [-3600000, 3600000]: {delta}")

    # Required objects in strict mode (must be non-null dicts)
    if strict:
        for req_obj_name in ("environment", "expected_state", "observed_state", "privacy_attestation"):
            obj_val = data.get(req_obj_name)
            if obj_val is None or not isinstance(obj_val, dict):
                raise ContinuityError(f"Field {req_obj_name!r} must be a non-null JSON object")

    # environment
    env = data.get("environment")
    if env is not None:
        if not isinstance(env, dict):
            raise ContinuityError("Field 'environment' must be a JSON object")
        if strict:
            unknown_env = set(env.keys()) - ALLOWED_ENVIRONMENT_FIELDS
            if unknown_env:
                raise ContinuityError(f"Unknown field(s) in environment: {sorted(unknown_env)}")
            for req_env_k in ("app_version", "platform"):
                if req_env_k not in env:
                    raise ContinuityError(f"Missing required environment field: {req_env_k!r}")
        for k in ("app_version", "platform"):
            if k in env:
                v = env.get(k)
                if v is None or not isinstance(v, str) or not v.strip() or len(v) > 64:
                    raise ContinuityError(f"Environment field {k!r} must be a non-empty string <= 64 chars")

    # expected_state and observed_state
    for state_key in ("expected_state", "observed_state"):
        state_obj = data.get(state_key)
        if state_obj is not None:
            if not isinstance(state_obj, dict):
                raise ContinuityError(f"Field {state_key!r} must be a JSON object")
            if strict:
                unknown_state = set(state_obj.keys()) - ALLOWED_STATE_FIELDS
                if unknown_state:
                    raise ContinuityError(f"Unknown field(s) in {state_key}: {sorted(unknown_state)}")
            if "position_ms" in state_obj:
                p_ms = state_obj["position_ms"]
                if not isinstance(p_ms, int) or isinstance(p_ms, bool) or p_ms < 0 or p_ms > 86_400_000:
                    raise ContinuityError(f"Invalid position_ms in {state_key}: {p_ms!r}")
            if "captions_enabled" in state_obj:
                c_en = state_obj["captions_enabled"]
                if not isinstance(c_en, bool):
                    raise ContinuityError(f"captions_enabled in {state_key} must be a boolean")
            for track_k in ("selected_subtitle_track_id", "selected_audio_track_id"):
                if track_k in state_obj and state_obj[track_k] is not None:
                    t_val = state_obj[track_k]
                    if not isinstance(t_val, str) or len(t_val) > 128 or "://" in t_val:
                        raise ContinuityError(f"Invalid {track_k} in {state_key}")
            if "presentation_mode" in state_obj:
                pm = state_obj["presentation_mode"]
                if not isinstance(pm, str) or pm not in ALLOWED_PRESENTATION_MODES:
                    raise ContinuityError(f"Invalid presentation_mode in {state_key}: {pm!r}")
            if "rendition_playlist_sha256" in state_obj:
                r_sha = state_obj["rendition_playlist_sha256"]
                if not isinstance(r_sha, str) or not DIGEST_REGEX.match(r_sha):
                    raise ContinuityError(f"Invalid rendition_playlist_sha256 in {state_key}: {r_sha!r}")

    # privacy_attestation
    att = data.get("privacy_attestation")
    if att is not None:
        if not isinstance(att, dict):
            raise ContinuityError("Field 'privacy_attestation' must be a JSON object")
        if strict:
            unknown_att = set(att.keys()) - ALLOWED_PRIVACY_ATTESTATION_FIELDS
            if unknown_att:
                raise ContinuityError(f"Unknown field(s) in privacy_attestation: {sorted(unknown_att)}")
            for flag in ("contains_pii", "contains_video_buffers", "contains_auth_tokens"):
                if flag not in att:
                    raise ContinuityError(f"Missing required privacy attestation flag: {flag!r}")
                val = att[flag]
                if not isinstance(val, bool):
                    raise ContinuityError(f"Privacy attestation flag {flag!r} must be a boolean")
                if val is True:
                    raise ContinuityError(f"Privacy violation: attestation asserts {flag}=True")
        else:
            for flag in ("contains_pii", "contains_video_buffers", "contains_auth_tokens"):
                if flag in att:
                    val = att[flag]
                    if not isinstance(val, bool):
                        raise ContinuityError(f"Privacy attestation flag {flag!r} must be a boolean")
                    if val is True:
                        raise ContinuityError(f"Privacy violation: attestation asserts {flag}=True")

    # retest_fixture_id
    retest_fix = data.get("retest_fixture_id")
    if not isinstance(retest_fix, str) or retest_fix not in KNOWN_RETEST_FIXTURES:
        raise ContinuityError(f"Receipt references unknown retest fixture: {retest_fix!r}")
    if strict:
        expected_fixture = "BENIGN_SUBTITLE_CONTINUITY_V1"
        for field, fixture in (
            ("captions_enabled", "DROPPED_CAPTION_FAULT_V1"),
            ("selected_subtitle_track_id", "DROPPED_CAPTION_FAULT_V1"),
            ("rendition_playlist_sha256", "STALE_CUT_INTRO_DRIFT_V1"),
            ("position_ms", "POSITION_OVERSHOOT_FAULT_V1"),
            ("selected_audio_track_id", "DROPPED_AUDIO_FAULT_V1"),
            ("presentation_mode", "PRESENTATION_MODE_FAULT_V1"),
        ):
            if field in seen_mismatches:
                expected_fixture = fixture
                break
        if retest_fix != expected_fixture:
            raise ContinuityError("Retest fixture does not match the reported mismatch fields")

    # receipt_digest
    if "receipt_digest" in data:
        declared = data["receipt_digest"]
        if not isinstance(declared, str) or not DIGEST_REGEX.match(declared):
            raise ContinuityError(f"Invalid receipt_digest format: {declared!r}")
        expected = compute_receipt_digest(data, ensure_ascii=False)
        if declared != expected:
            # Check legacy v1 digest with ensure_ascii=True
            if schema_ver == 1:
                legacy_expected = compute_receipt_digest(data, ensure_ascii=True)
                if declared == legacy_expected:
                    expected = legacy_expected
            if declared != expected:
                raise ContinuityError(
                    f"Incident receipt integrity digest mismatch: declared={declared}, computed={expected}"
                )
    elif strict:
        raise ContinuityError("Missing required receipt field: receipt_digest")

    # Independent recursive content scan
    _scan_for_sensitive_content(data)

    return data
