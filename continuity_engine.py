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


def compute_checkpoint_digest(payload: Mapping[str, Any]) -> str:
    """Computes SHA-256 digest over canonical JSON representation excluding integrity_digest."""
    clean = {k: v for k, v in payload.items() if k != "integrity_digest"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
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

    computed_digest = compute_checkpoint_digest(raw)
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


def compute_receipt_digest(payload: Mapping[str, Any]) -> str:
    """Computes SHA-256 digest over canonical JSON representation excluding receipt_digest."""
    clean = {k: v for k, v in payload.items() if k != "receipt_digest"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
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


MAX_RECEIPT_BYTES = 64 * 1024
RECEIPT_STATE_FIELDS = frozenset({
    "position_ms", "captions_enabled", "selected_subtitle_track_id",
    "selected_audio_track_id", "presentation_mode", "rendition_playlist_sha256",
})
RECEIPT_FIELDS = frozenset({
    "receipt_schema_version", "incident_id", "fixture_id", "timestamp_iso",
    "environment", "recovery_status", "diagnostic_code", "mismatch_fields",
    "position_delta_ms", "expected_state", "observed_state",
    "privacy_attestation", "retest_fixture_id", "receipt_digest",
})


def _unique_receipt_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ContinuityError("Duplicate JSON key in receipt")
        obj[key] = value
    return obj


def _receipt_object(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContinuityError(f"Invalid fields in {label}")


def _receipt_text(value, label, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ContinuityError(f"Invalid bounded string in {label}")
    if HOSTILE_CHAR_REGEX.search(value) or any(
        marker in value.lower() for marker in ("password", "token=", "://")
    ):
        raise ContinuityError(f"Unsafe diagnostic string in {label}")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ContinuityError(f"Invalid Unicode in {label}") from exc


def _receipt_integer(value, label):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise ContinuityError(f"Invalid non-negative integer in {label}")


def validate_and_parse_incident_receipt(raw_json_str: str) -> Dict[str, Any]:
    """Validate bounded v1 diagnostics; a digest is required but is NOT authentication."""
    if not isinstance(raw_json_str, str) or len(raw_json_str) > MAX_RECEIPT_BYTES:
        raise ContinuityError("Receipt exceeds input size limit")
    try:
        if len(raw_json_str.encode("utf-8")) > MAX_RECEIPT_BYTES:
            raise ContinuityError("Receipt exceeds input size limit")
        data = json.loads(raw_json_str, object_pairs_hook=_unique_receipt_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ContinuityError("Non-finite JSON number in receipt")))
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ContinuityError("Malformed JSON in incident receipt") from exc

    _receipt_object(data, RECEIPT_FIELDS, "receipt")
    if type(data["receipt_schema_version"]) is not int or data["receipt_schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise ContinuityError("Unsupported receipt schema version")
    declared_digest = data["receipt_digest"]
    if not isinstance(declared_digest, str) or not SHA256_PREFIX_REGEX.fullmatch(declared_digest):
        raise ContinuityError("Missing or invalid receipt_digest")
    try:
        expected_digest = compute_receipt_digest(data)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise ContinuityError("Unsupported receipt encoding") from exc
    if declared_digest != expected_digest:
        raise ContinuityError("Incident receipt integrity digest mismatch")

    for field in ("incident_id", "fixture_id", "timestamp_iso", "diagnostic_code", "retest_fixture_id"):
        _receipt_text(data[field], field)
    _receipt_object(data["environment"], {"app_version", "platform"}, "environment")
    for field, value in data["environment"].items():
        _receipt_text(value, field)
    _receipt_object(data["privacy_attestation"],
                    {"contains_pii", "contains_video_buffers", "contains_auth_tokens"}, "privacy_attestation")
    if any(value is not False for value in data["privacy_attestation"].values()):
        raise ContinuityError("Unsupported privacy attestation")
    _receipt_integer(data["position_delta_ms"], "position_delta_ms")
    for state_name in ("expected_state", "observed_state"):
        state = data[state_name]
        _receipt_object(state, RECEIPT_STATE_FIELDS, state_name)
        _receipt_integer(state["position_ms"], state_name + ".position_ms")
        if type(state["captions_enabled"]) is not bool:
            raise ContinuityError("captions_enabled must be a boolean")
        for field in ("selected_subtitle_track_id", "selected_audio_track_id"):
            _receipt_text(state[field], field, nullable=True)
        _receipt_text(state["presentation_mode"], "presentation_mode")
        if not isinstance(state["rendition_playlist_sha256"], str) or not SHA256_PREFIX_REGEX.fullmatch(state["rendition_playlist_sha256"]):
            raise ContinuityError("Invalid state playlist digest")
    mismatches = data["mismatch_fields"]
    if not isinstance(mismatches, list) or any(not isinstance(x, str) or x not in RECEIPT_STATE_FIELDS for x in mismatches):
        raise ContinuityError("Invalid mismatch_fields")
    if len(mismatches) != len(set(mismatches)):
        raise ContinuityError("Duplicate mismatch field")
    expected_status = "DIVERGENCE_DETECTED" if mismatches else "VERIFIED_MATCH"
    if data["recovery_status"] != expected_status:
        raise ContinuityError("Recovery status contradicts mismatch_fields")
    fixture = data["retest_fixture_id"]
    if fixture not in KNOWN_RETEST_FIXTURES:
        raise ContinuityError("Receipt references unknown retest fixture")
    # Match the producer's fixed mapping; do not let a fault receipt select a benign test.
    result = RecoveryVerificationResult(data["recovery_status"], tuple(mismatches),
        data["position_delta_ms"], data["expected_state"], data["observed_state"], data["diagnostic_code"])
    if fixture != derive_retest_fixture_id(result):
        raise ContinuityError("Retest fixture contradicts mismatch_fields")
    return data
