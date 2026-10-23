"""
FireShield Deterministic Player State Engine & Sidecar Contract Validator (P0).

Scope & Boundaries:
- Production verifier enforces schema_version == 2 with asymmetric Ed25519
  publisher signing (bound signing_alg='Ed25519' and key_id inside canonical
  payload) verified against a client-held trusted public key registry.
- Rejects downgrade attacks to schema_version == 1 (HMAC-SHA256) in production
  mode (allow_legacy_v1_hmac=False by default). Legacy v1 HMAC is retained
  solely for explicitly flagged local dry-run compatibility.
- Separates source-asset identity (asset_revision_id) from published rendition
  identity (rendition_set_id, rendition_playlist_sha256). Does NOT hash raw
  protected/DRM video frame buffers on client devices.
- Returns typed PlayerCommand objects (PAUSE, SEEK, CONTINUE, HOLD, ERROR) with
  explicit surface-blanking requirements and structured audit evidence.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import hmac
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

SHA256_PREFIXED_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
HMAC_SHA256_PREFIXED_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
ED25519_PREFIXED_RE = re.compile(r"^ed25519:[0-9a-f]{128}$")

SCHEMA_VERSION_V1_LEGACY_HMAC = 1
SCHEMA_VERSION_V2_ED25519 = 2
SUPPORTED_PRODUCTION_SCHEMA_VERSION = SCHEMA_VERSION_V2_ED25519
SUPPORTED_SCHEMA_VERSION = SCHEMA_VERSION_V2_ED25519

SUPPORTED_SIGNING_ALG_V2 = "Ed25519"
SUPPORTED_TIMEBASE = "presentation_ms"
DEFAULT_MIN_SAFE_RESUME_MARGIN_MS = 200
DEFAULT_PRE_GUARD_MS = 500
MAX_SEEK_FAILURE_ATTEMPTS = 2

ALLOWED_HAZARD_TYPES = frozenset({
    "luminance_flash",
    "saturated_red",
    "spatial_pattern",
})

ALLOWED_REVIEW_STATES = frozenset({
    "approved",
    "rejected",
    "pending_review",
    "unreviewed",
})

ALLOWED_INTERVAL_POLICIES = frozenset({
    "pause_before_interval",
    "pause_or_skip",
    "seek_to_safe_resume",
    "hold_playback",
})


class FireShieldState(enum.Enum):
    UNREVIEWED = "UNREVIEWED"
    ARMED = "ARMED"
    INTERVENING = "INTERVENING"
    RESOLVED = "RESOLVED"
    ERROR = "ERROR"


class PlayerActionType(enum.Enum):
    PAUSE = "PAUSE"
    SEEK = "SEEK"
    CONTINUE = "CONTINUE"
    HOLD = "HOLD"
    ERROR = "ERROR"


class FireShieldRuntimeError(Exception):
    """Raised when a sidecar contract or runtime transition violates safety invariants."""
    pass


@dataclasses.dataclass(frozen=True)
class PlaybackSessionContext:
    """
    Identifies the authorized stream rendition loaded by the publisher's player.
    Instead of hashing protected GPU frame buffers on Fire TV, the player binds to
    the source asset revision and the active rendition/playlist digest.
    """
    tenant_id: str
    asset_revision_id: str
    rendition_set_id: str
    rendition_playlist_sha256: str
    timebase: str = SUPPORTED_TIMEBASE
    allow_auto_seek_on_pause_or_skip: bool = False


@dataclasses.dataclass(frozen=True)
class HazardInterval:
    interval_id: str
    start_ms: int
    end_ms: int
    safe_resume_ms: int
    types: Tuple[str, ...]
    policy: str
    context_cue_ids: Tuple[str, ...] = ()
    context_note: Optional[Dict[str, Any]] = None


@dataclasses.dataclass(frozen=True)
class PlayerCommand:
    """
    Typed, side-effect-explicit directive emitted by FireShieldRuntime to the
    publisher's media player adapter.
    """
    action: PlayerActionType
    state: FireShieldState
    presentation_ms: int
    reason_code: str
    requires_surface_blanking: bool
    target_seek_ms: Optional[int] = None
    interval_id: Optional[str] = None
    hazard_types: Tuple[str, ...] = ()
    context_cue_ids: Tuple[str, ...] = ()
    context_note: Optional[Dict[str, Any]] = None
    audit_evidence: Dict[str, Any] = dataclasses.field(default_factory=dict)


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def compute_canonical_manifest_digest(manifest_dict: Mapping[str, Any]) -> str:
    """
    Computes the canonical SHA-256 digest of a sidecar manifest dictionary,
    excluding 'manifest_digest' and 'signature'. In schema_version 2, this
    includes 'signing_alg' and 'key_id' so neither can be altered without
    invalidating both the digest and signature.
    """
    payload = {
        k: v
        for k, v in manifest_dict.items()
        if k not in ("manifest_digest", "signature")
    }
    canonical_bytes = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"


def build_ed25519_signing_message(
    *,
    schema_version: int,
    signing_alg: str,
    key_id: str,
    manifest_digest: str,
) -> bytes:
    """
    Constructs the domain-separated message signed by the publisher's Ed25519 private key.
    """
    return f"fireshield-sidecar:v{schema_version}:{signing_alg}:{key_id}:{manifest_digest}".encode("utf-8")


def derive_ed25519_public_key_bytes(private_seed_32b: bytes) -> bytes:
    """Derives the 32-byte raw Ed25519 public key from a 32-byte private key seed."""
    if not isinstance(private_seed_32b, bytes) or len(private_seed_32b) != 32:
        raise ValueError("Ed25519 private key seed must be exactly 32 bytes.")
    priv = Ed25519PrivateKey.from_private_bytes(private_seed_32b)
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def derive_ed25519_public_key_hex(private_seed_32b: bytes) -> str:
    """Returns the 64-character lowercase hex string of the 32-byte Ed25519 public key."""
    return derive_ed25519_public_key_bytes(private_seed_32b).hex()


def compute_ed25519_signature(
    *,
    manifest_digest: str,
    key_id: str,
    private_seed_32b: bytes,
    schema_version: int = SCHEMA_VERSION_V2_ED25519,
    signing_alg: str = SUPPORTED_SIGNING_ALG_V2,
) -> str:
    """Signs the bound v2 sidecar digest using a 32-byte Ed25519 private key seed."""
    if not isinstance(private_seed_32b, bytes) or len(private_seed_32b) != 32:
        raise ValueError("Ed25519 private_seed_32b must be exactly 32 bytes.")
    priv = Ed25519PrivateKey.from_private_bytes(private_seed_32b)
    msg = build_ed25519_signing_message(
        schema_version=schema_version,
        signing_alg=signing_alg,
        key_id=key_id,
        manifest_digest=manifest_digest,
    )
    sig_bytes = priv.sign(msg)
    return f"ed25519:{sig_bytes.hex()}"


def _coerce_ed25519_public_key(key_material: Union[bytes, str, Ed25519PublicKey]) -> Ed25519PublicKey:
    if isinstance(key_material, Ed25519PublicKey):
        return key_material
    if isinstance(key_material, str):
        cleaned = key_material.removeprefix("ed25519-pub:")
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as exc:
            raise FireShieldRuntimeError(f"Malformed hex public key in trusted_public_keys: {exc}") from exc
    elif isinstance(key_material, bytes):
        raw = key_material
    else:
        raise FireShieldRuntimeError(f"Unsupported public key type: {type(key_material)!r}")

    if len(raw) != 32:
        raise FireShieldRuntimeError(f"Ed25519 public key must be 32 bytes, got {len(raw)} bytes.")
    return Ed25519PublicKey.from_public_bytes(raw)


def compute_hmac_signature(manifest_digest: str, secret_key: bytes) -> str:
    """
    Legacy v1 local dry-run helper: computes an HMAC-SHA256 tag over manifest_digest.
    Rejected by production-mode verifier unless allow_legacy_v1_hmac=True.
    """
    mac = hmac.new(secret_key, manifest_digest.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{mac}"


class SidecarContractValidator:
    """
    Validates a versioned FireShield sidecar manifest against schema invariants,
    Ed25519 public-key signature (or explicit legacy v1 HMAC when opted in),
    interval ordering/margins, and session identity.
    """

    @staticmethod
    def validate(
        manifest_raw: str,
        session: PlaybackSessionContext,
        *,
        trusted_public_keys: Optional[Mapping[str, Union[bytes, str, Ed25519PublicKey]]] = None,
        revoked_key_ids: Optional[Sequence[str]] = None,
        allow_legacy_v1_hmac: bool = False,
        legacy_hmac_secret: Optional[bytes] = None,
        require_signature: bool = True,
        min_safe_resume_margin_ms: int = DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    ) -> Tuple[Dict[str, Any], List[HazardInterval], str]:
        """
        Validates the sidecar JSON string against the active PlaybackSessionContext.
        Returns (parsed_manifest, validated_intervals, review_state).
        Raises FireShieldRuntimeError on any schema, digest, signature, or identity fault.
        """
        if not isinstance(manifest_raw, str) or not manifest_raw.strip():
            raise FireShieldRuntimeError("Sidecar manifest must be a non-empty JSON string.")

        try:
            manifest = json.loads(manifest_raw)
        except Exception as exc:
            raise FireShieldRuntimeError(f"Malformed sidecar JSON: {exc}") from exc

        if not isinstance(manifest, dict):
            raise FireShieldRuntimeError("Sidecar manifest root must be a JSON object.")

        # Validate session context fields first
        if not SHA256_PREFIXED_RE.match(session.asset_revision_id or ""):
            raise FireShieldRuntimeError("Session asset_revision_id is missing or invalid sha256 digest.")
        if not SHA256_PREFIXED_RE.match(session.rendition_playlist_sha256 or ""):
            raise FireShieldRuntimeError("Session rendition_playlist_sha256 is missing or invalid sha256 digest.")
        if not isinstance(session.tenant_id, str) or not session.tenant_id.strip():
            raise FireShieldRuntimeError("Session tenant_id must be a non-empty string.")
        if not isinstance(session.rendition_set_id, str) or not session.rendition_set_id.strip():
            raise FireShieldRuntimeError("Session rendition_set_id must be a non-empty string.")

        required_top_fields = (
            "schema_version",
            "tenant_id",
            "asset_revision_id",
            "rendition_set_id",
            "rendition_playlist_sha256",
            "timebase",
            "detector_version",
            "review_state",
            "manifest_digest",
        )
        for field in required_top_fields:
            if field not in manifest:
                raise FireShieldRuntimeError(f"Missing required sidecar field: '{field}'.")

        schema_version = manifest["schema_version"]
        if not _is_strict_int(schema_version):
            raise FireShieldRuntimeError(
                f"Invalid schema_version type: {schema_version!r} (must be integer)."
            )

        if schema_version == SCHEMA_VERSION_V1_LEGACY_HMAC:
            if not allow_legacy_v1_hmac:
                raise FireShieldRuntimeError(
                    "Downgrade to legacy schema_version=1 (HMAC) rejected in production verifier mode "
                    "(expected schema_version=2 with Ed25519)."
                )
        elif schema_version != SCHEMA_VERSION_V2_ED25519:
            raise FireShieldRuntimeError(
                f"Unsupported schema_version: {schema_version!r} (expected {SCHEMA_VERSION_V2_ED25519})."
            )

        if manifest["tenant_id"] != session.tenant_id:
            raise FireShieldRuntimeError(
                f"Tenant mismatch: manifest='{manifest['tenant_id']}' vs session='{session.tenant_id}'."
            )

        asset_rev = manifest["asset_revision_id"]
        if not isinstance(asset_rev, str) or not SHA256_PREFIXED_RE.match(asset_rev):
            raise FireShieldRuntimeError(f"Invalid asset_revision_id format: {asset_rev!r}.")
        if asset_rev != session.asset_revision_id:
            raise FireShieldRuntimeError(
                f"Source asset_revision_id mismatch: manifest={asset_rev} vs session={session.asset_revision_id}."
            )

        rendition_set_id = manifest["rendition_set_id"]
        if not isinstance(rendition_set_id, str) or not rendition_set_id.strip():
            raise FireShieldRuntimeError("rendition_set_id must be a non-empty string.")
        if rendition_set_id != session.rendition_set_id:
            raise FireShieldRuntimeError(
                f"Stale or mismatched rendition_set_id: manifest='{rendition_set_id}' vs session='{session.rendition_set_id}'."
            )

        rendition_sha = manifest["rendition_playlist_sha256"]
        if not isinstance(rendition_sha, str) or not SHA256_PREFIXED_RE.match(rendition_sha):
            raise FireShieldRuntimeError(f"Invalid rendition_playlist_sha256 format: {rendition_sha!r}.")
        if rendition_sha != session.rendition_playlist_sha256:
            raise FireShieldRuntimeError(
                f"Stale rendition_playlist_sha256 after re-encode or edit: "
                f"manifest={rendition_sha} vs session={session.rendition_playlist_sha256}."
            )

        if manifest["timebase"] != SUPPORTED_TIMEBASE or session.timebase != SUPPORTED_TIMEBASE:
            raise FireShieldRuntimeError(
                f"Unsupported timebase: manifest={manifest['timebase']!r}, session={session.timebase!r}."
            )

        detector_version = manifest["detector_version"]
        if not isinstance(detector_version, str) or not detector_version.strip():
            raise FireShieldRuntimeError("detector_version must be a non-empty string.")

        review_state = manifest["review_state"]
        if not isinstance(review_state, str) or review_state not in ALLOWED_REVIEW_STATES:
            raise FireShieldRuntimeError(f"Invalid review_state: {review_state!r}.")

        # Verify manifest_digest first (binds signing_alg and key_id in v2)
        manifest_digest = manifest["manifest_digest"]
        if not isinstance(manifest_digest, str) or not SHA256_PREFIXED_RE.match(manifest_digest):
            raise FireShieldRuntimeError(f"Invalid manifest_digest format: {manifest_digest!r}.")
        expected_digest = compute_canonical_manifest_digest(manifest)
        if not hmac.compare_digest(manifest_digest, expected_digest):
            raise FireShieldRuntimeError(
                f"manifest_digest integrity mismatch: declared={manifest_digest}, computed={expected_digest}."
            )

        # Verify cryptographic signature according to schema_version
        signature = manifest.get("signature")
        if schema_version == SCHEMA_VERSION_V2_ED25519:
            if "signing_alg" not in manifest:
                raise FireShieldRuntimeError("Missing required v2 field: 'signing_alg'.")
            if "key_id" not in manifest:
                raise FireShieldRuntimeError("Missing required v2 field: 'key_id'.")

            signing_alg = manifest["signing_alg"]
            if signing_alg != SUPPORTED_SIGNING_ALG_V2:
                raise FireShieldRuntimeError(
                    f"Unsupported or downgraded signing_alg={signing_alg!r} in v2 manifest (must be 'Ed25519')."
                )

            key_id = manifest["key_id"]
            if not isinstance(key_id, str) or not key_id.strip():
                raise FireShieldRuntimeError("Field 'key_id' must be a non-empty string.")

            if not isinstance(signature, str) or not signature.strip():
                raise FireShieldRuntimeError("Missing required 'signature' field on v2 sidecar manifest.")
            if signature.startswith("hmac-sha256:"):
                raise FireShieldRuntimeError("Algorithm downgrade attack: hmac-sha256 signature rejected on v2 manifest.")
            if not ED25519_PREFIXED_RE.match(signature):
                raise FireShieldRuntimeError(f"Malformed Ed25519 signature format: {signature!r}.")

            if revoked_key_ids and key_id in set(revoked_key_ids):
                raise FireShieldRuntimeError(
                    f"Publisher key_id={key_id!r} has been explicitly revoked."
                )

            if not trusted_public_keys or key_id not in trusted_public_keys:
                raise FireShieldRuntimeError(
                    f"Unknown or untrusted publisher key_id: {key_id!r}."
                )

            pub_key = _coerce_ed25519_public_key(trusted_public_keys[key_id])
            sig_bytes = bytes.fromhex(signature.removeprefix("ed25519:"))
            signing_msg = build_ed25519_signing_message(
                schema_version=SCHEMA_VERSION_V2_ED25519,
                signing_alg=signing_alg,
                key_id=key_id,
                manifest_digest=manifest_digest,
            )
            try:
                pub_key.verify(sig_bytes, signing_msg)
            except InvalidSignature as exc:
                raise FireShieldRuntimeError(
                    f"Ed25519 signature verification failed for key_id={key_id!r}."
                ) from exc
        else:
            # Legacy v1 HMAC path (only reachable when allow_legacy_v1_hmac=True)
            if "signing_alg" in manifest and manifest["signing_alg"] != "HMAC-SHA256":
                raise FireShieldRuntimeError(f"Invalid v1 signing_alg: {manifest['signing_alg']!r}.")
            if require_signature:
                if not isinstance(signature, str) or not signature.strip():
                    raise FireShieldRuntimeError("Missing required 'signature' field on sidecar manifest.")
                if signature.startswith("unimplemented:") or signature == "UNSIGNED":
                    raise FireShieldRuntimeError(
                        "Sidecar signature is marked unimplemented/unsigned while require_signature=True."
                    )
                if legacy_hmac_secret is None:
                    raise FireShieldRuntimeError(
                        "Legacy v1 HMAC verification required but no legacy_hmac_secret was configured."
                    )
                if not HMAC_SHA256_PREFIXED_RE.match(signature):
                    raise FireShieldRuntimeError(f"Unsupported or malformed v1 HMAC signature format: {signature!r}.")
                expected_sig = compute_hmac_signature(manifest_digest, legacy_hmac_secret)
                if not hmac.compare_digest(signature, expected_sig):
                    raise FireShieldRuntimeError("Cryptographic HMAC-SHA256 signature verification failed.")

        # Validate intervals list and zero-findings attestation rule
        has_intervals_key = "intervals" in manifest
        raw_intervals = manifest.get("intervals")

        if not has_intervals_key:
            if review_state == "approved" and manifest.get("zero_findings_attested") is True:
                raw_intervals = []
            elif review_state in ("unreviewed", "pending_review"):
                raw_intervals = []
            else:
                raise FireShieldRuntimeError(
                    "Absent 'intervals' list is only permitted when review_state=='approved' "
                    "with explicit zero_findings_attested=True, or when unreviewed/pending_review."
                )

        if not isinstance(raw_intervals, list):
            raise FireShieldRuntimeError("Field 'intervals' must be a JSON list.")

        if len(raw_intervals) == 0 and review_state == "approved":
            if manifest.get("zero_findings_attested") is not True:
                review_state = "unreviewed"

        validated_intervals: List[HazardInterval] = []
        prev_end_ms = -1
        prev_safe_resume_ms = -1

        for idx, item in enumerate(raw_intervals):
            if not isinstance(item, dict):
                raise FireShieldRuntimeError(f"Interval at index {idx} must be a JSON object.")

            for req_key in ("start_ms", "end_ms", "safe_resume_ms", "types", "policy"):
                if req_key not in item:
                    raise FireShieldRuntimeError(f"Interval at index {idx} missing required field '{req_key}'.")

            start_ms = item["start_ms"]
            end_ms = item["end_ms"]
            safe_resume_ms = item["safe_resume_ms"]

            if not (_is_strict_int(start_ms) and _is_strict_int(end_ms) and _is_strict_int(safe_resume_ms)):
                raise FireShieldRuntimeError(
                    f"Interval at index {idx} timestamps must be strict integers (booleans/floats/strings rejected)."
                )
            if start_ms < 0:
                raise FireShieldRuntimeError(f"Interval at index {idx} has negative start_ms={start_ms}.")
            if end_ms < start_ms:
                raise FireShieldRuntimeError(
                    f"Interval at index {idx} has end_ms ({end_ms}) < start_ms ({start_ms})."
                )
            if safe_resume_ms < end_ms + min_safe_resume_margin_ms:
                raise FireShieldRuntimeError(
                    f"Interval at index {idx} violates safe_resume_ms margin: "
                    f"safe_resume_ms={safe_resume_ms} must be >= end_ms ({end_ms}) + {min_safe_resume_margin_ms}ms."
                )

            if idx > 0:
                if start_ms <= prev_end_ms:
                    raise FireShieldRuntimeError(
                        f"Unsorted or overlapping intervals at index {idx}: "
                        f"start_ms={start_ms} <= previous end_ms={prev_end_ms}."
                    )
                if start_ms < prev_safe_resume_ms:
                    raise FireShieldRuntimeError(
                        f"Interval at index {idx} starts at {start_ms}ms before previous interval's "
                        f"safe_resume_ms={prev_safe_resume_ms}ms."
                    )

            raw_types = item["types"]
            if not isinstance(raw_types, list) or len(raw_types) == 0:
                raise FireShieldRuntimeError(f"Interval at index {idx} must have a non-empty 'types' list.")
            for htype in raw_types:
                if not isinstance(htype, str) or htype not in ALLOWED_HAZARD_TYPES:
                    raise FireShieldRuntimeError(
                        f"Interval at index {idx} contains unsupported hazard type: {htype!r}."
                    )

            policy = item["policy"]
            if not isinstance(policy, str) or policy not in ALLOWED_INTERVAL_POLICIES:
                raise FireShieldRuntimeError(
                    f"Interval at index {idx} contains unsupported policy: {policy!r}."
                )

            raw_cues = item.get("context_cue_ids", [])
            if not isinstance(raw_cues, list) or any(not isinstance(c, str) or not c.strip() for c in raw_cues):
                raise FireShieldRuntimeError(
                    f"Interval at index {idx} has invalid context_cue_ids (must be list of non-empty strings)."
                )

            raw_note = item.get("context_note")
            if raw_note is not None:
                if not isinstance(raw_note, dict):
                    raise FireShieldRuntimeError(f"Interval at index {idx} has invalid context_note (must be a JSON object).")
                if "text" not in raw_note or not isinstance(raw_note["text"], str) or not raw_note["text"].strip():
                    raise FireShieldRuntimeError(f"Interval at index {idx} context_note missing valid non-empty 'text' field.")

            interval_id = item.get("interval_id", f"interval-{idx:03d}")
            if not isinstance(interval_id, str) or not interval_id.strip():
                raise FireShieldRuntimeError(f"Interval at index {idx} has invalid interval_id.")

            validated_intervals.append(
                HazardInterval(
                    interval_id=interval_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    safe_resume_ms=safe_resume_ms,
                    types=tuple(raw_types),
                    policy=policy,
                    context_cue_ids=tuple(raw_cues),
                    context_note=raw_note,
                )
            )
            prev_end_ms = end_ms
            prev_safe_resume_ms = safe_resume_ms

        return manifest, validated_intervals, review_state


class FireShieldRuntime:
    """
    Deterministic playback intervention state machine for publisher-controlled players.
    By default, requires schema_version == 2 with Ed25519 signature verification
    against trusted_public_keys and rejects legacy v1 HMAC manifests.
    """

    def __init__(
        self,
        session: PlaybackSessionContext,
        sidecar_manifest_str: Optional[str],
        *,
        trusted_public_keys: Optional[Mapping[str, Union[bytes, str, Ed25519PublicKey]]] = None,
        revoked_key_ids: Optional[Sequence[str]] = None,
        allow_legacy_v1_hmac: bool = False,
        hmac_secret: Optional[bytes] = None,
        require_signature: bool = True,
        pre_guard_ms: int = DEFAULT_PRE_GUARD_MS,
        min_safe_resume_margin_ms: int = DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    ):
        if not _is_strict_int(pre_guard_ms) or pre_guard_ms < 0:
            raise ValueError("pre_guard_ms must be a non-negative integer.")
        if not _is_strict_int(min_safe_resume_margin_ms) or min_safe_resume_margin_ms <= 0:
            raise ValueError("min_safe_resume_margin_ms must be a positive integer.")

        self.session = session
        self.trusted_public_keys = dict(trusted_public_keys) if trusted_public_keys else None
        self.revoked_key_ids = tuple(revoked_key_ids) if revoked_key_ids else ()
        self.allow_legacy_v1_hmac = allow_legacy_v1_hmac
        self.hmac_secret = hmac_secret
        self.require_signature = require_signature
        self.pre_guard_ms = pre_guard_ms
        self.min_safe_resume_margin_ms = min_safe_resume_margin_ms
        self._raw_sidecar_manifest_str: Optional[str] = sidecar_manifest_str

        self.state: FireShieldState = FireShieldState.UNREVIEWED
        self.verified: bool = False
        self.review_state: str = "unreviewed"
        self.error_reason: Optional[str] = None
        self.manifest: Dict[str, Any] = {}
        self.intervals: Tuple[HazardInterval, ...] = ()

        self.current_position_ms: Optional[int] = None
        self.active_interval: Optional[HazardInterval] = None
        self.last_intervened_interval_id: Optional[str] = None
        self.last_emitted_action: Optional[PlayerActionType] = None
        self.pending_seek_target_ms: Optional[int] = None
        self.seek_failure_count: int = 0
        self.audit_log: List[PlayerCommand] = []

        self._load_and_verify_contract(sidecar_manifest_str)

    def _load_and_verify_contract(self, sidecar_manifest_str: Optional[str]) -> None:
        self._raw_sidecar_manifest_str = sidecar_manifest_str
        self.active_interval = None
        self.pending_seek_target_ms = None
        self.seek_failure_count = 0
        self.last_emitted_action = None

        if sidecar_manifest_str is None:
            self.state = FireShieldState.UNREVIEWED
            self.verified = False
            self.review_state = "unreviewed"
            self.error_reason = "NO_SIDECAR_PROVIDED"
            return

        try:
            manifest, intervals, effective_review_state = SidecarContractValidator.validate(
                manifest_raw=sidecar_manifest_str,
                session=self.session,
                trusted_public_keys=self.trusted_public_keys,
                revoked_key_ids=self.revoked_key_ids,
                allow_legacy_v1_hmac=self.allow_legacy_v1_hmac,
                legacy_hmac_secret=self.hmac_secret,
                require_signature=self.require_signature,
                min_safe_resume_margin_ms=self.min_safe_resume_margin_ms,
            )
            self.manifest = manifest
            self.intervals = tuple(intervals)
            self.review_state = effective_review_state

            if effective_review_state == "rejected":
                self.state = FireShieldState.ERROR
                self.verified = False
                self.error_reason = "ASSET_EDITORIALLY_REJECTED"
            elif effective_review_state == "approved":
                self.state = FireShieldState.ARMED
                self.verified = True
                self.error_reason = None
            else:
                self.state = FireShieldState.UNREVIEWED
                self.verified = False
                self.error_reason = f"ASSET_NOT_APPROVED:{effective_review_state}"
        except Exception as exc:
            self.state = FireShieldState.ERROR
            self.verified = False
            self.review_state = "error"
            self.error_reason = str(exc)

    def update_key_ring(
        self,
        trusted_public_keys: Mapping[str, Union[bytes, str, Ed25519PublicKey]],
        revoked_key_ids: Optional[Sequence[str]] = None,
    ) -> PlayerCommand:
        """
        Applies a key-ring rotation or revocation update during an active playback session
        and immediately re-verifies the bound sidecar contract.
        """
        last_pos = self.current_position_ms or 0
        self.trusted_public_keys = dict(trusted_public_keys)
        self.revoked_key_ids = tuple(revoked_key_ids) if revoked_key_ids else ()
        self._load_and_verify_contract(self._raw_sidecar_manifest_str)
        self.current_position_ms = None
        return self.update_player_head(last_pos, is_rendition_switch=True)

    def _record_command(self, cmd: PlayerCommand) -> PlayerCommand:
        self.audit_log.append(cmd)
        return cmd

    def _guard_start_ms(self, interval: HazardInterval) -> int:
        return max(0, interval.start_ms - self.pre_guard_ms)

    def _find_matching_interval(
        self,
        prev_ms: Optional[int],
        current_ms: int,
        *,
        is_explicit_seek: bool = False,
        is_rendition_switch: bool = False,
        seek_completed_interval_id: Optional[str] = None,
    ) -> Tuple[Optional[HazardInterval], str]:
        for interval in self.intervals:
            guard_start = self._guard_start_ms(interval)
            if guard_start <= current_ms <= interval.end_ms:
                trigger_kind = "PRE_GUARD_WINDOW" if current_ms < interval.start_ms else "INSIDE_HAZARD_WINDOW"
                return interval, trigger_kind
            if (
                not is_explicit_seek
                and not is_rendition_switch
                and prev_ms is not None
                and prev_ms < interval.start_ms
                and current_ms > interval.end_ms
            ):
                if seek_completed_interval_id == interval.interval_id and current_ms >= interval.safe_resume_ms:
                    continue
                if self.last_intervened_interval_id != interval.interval_id or current_ms < interval.safe_resume_ms:
                    return interval, "PLAYHEAD_JUMPED_OVER_INTERVAL"
        return None, ""

    def notify_seek(self, target_ms: int) -> PlayerCommand:
        if not _is_strict_int(target_ms) or target_ms < 0:
            self.state = FireShieldState.ERROR
            self.error_reason = f"Invalid seek target_ms={target_ms!r}"
            return self._record_command(
                PlayerCommand(
                    action=PlayerActionType.ERROR,
                    state=self.state,
                    presentation_ms=self.current_position_ms or 0,
                    reason_code="INVALID_SEEK_TARGET",
                    requires_surface_blanking=True,
                    audit_evidence={"invalid_target_ms": target_ms},
                )
            )
        return self.update_player_head(target_ms, is_explicit_seek=True)

    def notify_seek_failed(self, actual_position_ms: int, reason: str = "PLAYER_SEEK_REJECTED") -> PlayerCommand:
        self.seek_failure_count += 1
        self.state = FireShieldState.ERROR
        self.error_reason = f"SEEK_FAILED:{reason}"
        interval_id = self.active_interval.interval_id if self.active_interval else None
        return self._record_command(
            PlayerCommand(
                action=PlayerActionType.ERROR,
                state=self.state,
                presentation_ms=actual_position_ms,
                reason_code="SEEK_FAILED_FAIL_CLOSED",
                requires_surface_blanking=True,
                interval_id=interval_id,
                audit_evidence={
                    "expected_seek_target_ms": self.pending_seek_target_ms,
                    "actual_position_ms": actual_position_ms,
                    "seek_failure_count": self.seek_failure_count,
                    "failure_reason": reason,
                },
            )
        )

    def request_interval_bypass_seek(self) -> PlayerCommand:
        pos_ms = self.current_position_ms or 0
        if self.state != FireShieldState.INTERVENING or self.active_interval is None:
            return self._record_command(
                PlayerCommand(
                    action=PlayerActionType.HOLD,
                    state=self.state,
                    presentation_ms=pos_ms,
                    reason_code="NO_ACTIVE_INTERVENTION_TO_BYPASS",
                    requires_surface_blanking=(self.state == FireShieldState.ERROR),
                )
            )
        interval = self.active_interval
        self.pending_seek_target_ms = interval.safe_resume_ms
        self.last_emitted_action = PlayerActionType.SEEK
        return self._record_command(
            PlayerCommand(
                action=PlayerActionType.SEEK,
                state=self.state,
                presentation_ms=pos_ms,
                target_seek_ms=interval.safe_resume_ms,
                reason_code="VIEWER_REQUESTED_SAFE_BYPASS_SEEK",
                requires_surface_blanking=True,
                interval_id=interval.interval_id,
                hazard_types=interval.types,
                context_cue_ids=interval.context_cue_ids,
                audit_evidence={
                    "interval_start_ms": interval.start_ms,
                    "interval_end_ms": interval.end_ms,
                    "safe_resume_ms": interval.safe_resume_ms,
                },
            )
        )

    def on_rendition_switch(
        self,
        new_session: PlaybackSessionContext,
        new_sidecar_manifest_str: Optional[str],
    ) -> PlayerCommand:
        last_pos = self.current_position_ms or 0
        self.session = new_session
        self._load_and_verify_contract(new_sidecar_manifest_str)
        self.current_position_ms = None
        return self.update_player_head(last_pos, is_rendition_switch=True)

    def on_sidecar_error(self, error_detail: str) -> PlayerCommand:
        pos_ms = self.current_position_ms or 0
        self.state = FireShieldState.ERROR
        self.verified = False
        self.error_reason = f"RUNTIME_SIDECAR_FAULT:{error_detail}"
        return self._record_command(
            PlayerCommand(
                action=PlayerActionType.ERROR,
                state=self.state,
                presentation_ms=pos_ms,
                reason_code="SIDECAR_NETWORK_OR_INTEGRITY_ERROR",
                requires_surface_blanking=True,
                audit_evidence={"error_detail": error_detail},
            )
        )

    def update_player_head(
        self,
        presentation_ms: int,
        *,
        is_explicit_seek: bool = False,
        is_rendition_switch: bool = False,
    ) -> PlayerCommand:
        if not _is_strict_int(presentation_ms) or presentation_ms < 0:
            self.state = FireShieldState.ERROR
            self.error_reason = f"Invalid presentation_ms: {presentation_ms!r}"
            return self._record_command(
                PlayerCommand(
                    action=PlayerActionType.ERROR,
                    state=self.state,
                    presentation_ms=0,
                    reason_code="INVALID_PRESENTATION_TIMESTAMP",
                    requires_surface_blanking=True,
                    audit_evidence={"invalid_presentation_ms": presentation_ms},
                )
            )

        prev_ms = self.current_position_ms

        # 1. Fail-closed on ERROR state
        if self.state == FireShieldState.ERROR:
            self.current_position_ms = presentation_ms
            return self._record_command(
                PlayerCommand(
                    action=PlayerActionType.ERROR,
                    state=FireShieldState.ERROR,
                    presentation_ms=presentation_ms,
                    reason_code="FAIL_CLOSED_CONTRACT_OR_RUNTIME_ERROR",
                    requires_surface_blanking=True,
                    audit_evidence={
                        "error_reason": self.error_reason,
                        "review_state": self.review_state,
                    },
                )
            )

        # 2. Unreviewed assets: hold/pause playback with surface blanking
        if not self.verified or self.state == FireShieldState.UNREVIEWED:
            self.state = FireShieldState.UNREVIEWED
            self.current_position_ms = presentation_ms
            return self._record_command(
                PlayerCommand(
                    action=PlayerActionType.PAUSE,
                    state=FireShieldState.UNREVIEWED,
                    presentation_ms=presentation_ms,
                    reason_code="UNREVIEWED_ASSET_HOLD_FOR_EXPLICIT_CONSENT",
                    requires_surface_blanking=True,
                    audit_evidence={
                        "review_state": self.review_state,
                        "error_reason": self.error_reason,
                    },
                )
            )

        # 3. Verify pending seek completion if a SEEK command was previously issued
        seek_completed_interval_id: Optional[str] = None
        if self.pending_seek_target_ms is not None and not is_explicit_seek and not is_rendition_switch:
            expected_target = self.pending_seek_target_ms
            if presentation_ms < expected_target:
                self.seek_failure_count += 1
                self.current_position_ms = presentation_ms
                interval_id = self.active_interval.interval_id if self.active_interval else None
                if self.seek_failure_count >= MAX_SEEK_FAILURE_ATTEMPTS:
                    self.state = FireShieldState.ERROR
                    self.error_reason = (
                        f"Seek failed {self.seek_failure_count} times: "
                        f"expected >= {expected_target}ms, actual={presentation_ms}ms."
                    )
                    return self._record_command(
                        PlayerCommand(
                            action=PlayerActionType.ERROR,
                            state=FireShieldState.ERROR,
                            presentation_ms=presentation_ms,
                            reason_code="SEEK_FAILED_MAX_RETRIES_EXCEEDED",
                            requires_surface_blanking=True,
                            interval_id=interval_id,
                            audit_evidence={
                                "expected_seek_target_ms": expected_target,
                                "actual_presentation_ms": presentation_ms,
                                "seek_failure_count": self.seek_failure_count,
                            },
                        )
                    )
                self.state = FireShieldState.INTERVENING
                self.last_emitted_action = PlayerActionType.PAUSE
                return self._record_command(
                    PlayerCommand(
                        action=PlayerActionType.PAUSE,
                        state=FireShieldState.INTERVENING,
                        presentation_ms=presentation_ms,
                        reason_code="SEEK_INCOMPLETE_EMERGENCY_PAUSE",
                        requires_surface_blanking=True,
                        interval_id=interval_id,
                        audit_evidence={
                            "expected_seek_target_ms": expected_target,
                            "actual_presentation_ms": presentation_ms,
                            "seek_failure_count": self.seek_failure_count,
                        },
                    )
                )
            else:
                seek_completed_interval_id = (
                    self.active_interval.interval_id if self.active_interval is not None else None
                )
                self.pending_seek_target_ms = None
                self.seek_failure_count = 0

        # 4. Check if playhead is inside a pre-guard window, hazard interval, or jumped over one
        matched_interval, trigger_kind = self._find_matching_interval(
            prev_ms,
            presentation_ms,
            is_explicit_seek=is_explicit_seek,
            is_rendition_switch=is_rendition_switch,
            seek_completed_interval_id=seek_completed_interval_id,
        )

        if matched_interval is not None:
            if (
                self.state == FireShieldState.INTERVENING
                and self.active_interval is not None
                and self.active_interval.interval_id == matched_interval.interval_id
                and prev_ms == presentation_ms
                and not is_explicit_seek
                and not is_rendition_switch
            ):
                self.current_position_ms = presentation_ms
                return self._record_command(
                    PlayerCommand(
                        action=PlayerActionType.HOLD,
                        state=FireShieldState.INTERVENING,
                        presentation_ms=presentation_ms,
                        reason_code="DUPLICATE_TICK_INTERVENTION_HOLD",
                        requires_surface_blanking=True,
                        interval_id=matched_interval.interval_id,
                        hazard_types=matched_interval.types,
                        context_cue_ids=matched_interval.context_cue_ids,
                        audit_evidence={
                            "duplicate_tick": True,
                            "trigger_kind": trigger_kind,
                            "previous_action": self.last_emitted_action.value if self.last_emitted_action else None,
                        },
                    )
                )

            self.state = FireShieldState.INTERVENING
            self.active_interval = matched_interval
            self.last_intervened_interval_id = matched_interval.interval_id
            self.current_position_ms = presentation_ms

            if matched_interval.policy == "hold_playback":
                action = PlayerActionType.HOLD
                target_seek = None
                reason = f"POLICY_HOLD_{trigger_kind}"
            elif matched_interval.policy == "seek_to_safe_resume" or (
                matched_interval.policy == "pause_or_skip" and self.session.allow_auto_seek_on_pause_or_skip
            ):
                action = PlayerActionType.SEEK
                target_seek = matched_interval.safe_resume_ms
                self.pending_seek_target_ms = target_seek
                reason = f"POLICY_SEEK_TO_SAFE_RESUME_{trigger_kind}"
            else:
                action = PlayerActionType.PAUSE
                target_seek = None
                reason = f"POLICY_PREEMPTIVE_PAUSE_{trigger_kind}"

            self.last_emitted_action = action
            return self._record_command(
                PlayerCommand(
                    action=action,
                    state=FireShieldState.INTERVENING,
                    presentation_ms=presentation_ms,
                    target_seek_ms=target_seek,
                    reason_code=reason,
                    requires_surface_blanking=True,
                    interval_id=matched_interval.interval_id,
                    hazard_types=matched_interval.types,
                    context_cue_ids=matched_interval.context_cue_ids,
                    context_note=matched_interval.context_note,
                    audit_evidence={
                        "trigger_kind": trigger_kind,
                        "guard_start_ms": self._guard_start_ms(matched_interval),
                        "interval_start_ms": matched_interval.start_ms,
                        "interval_end_ms": matched_interval.end_ms,
                        "safe_resume_ms": matched_interval.safe_resume_ms,
                        "policy": matched_interval.policy,
                        "is_explicit_seek": is_explicit_seek,
                        "is_rendition_switch": is_rendition_switch,
                    },
                )
            )

        # 5. Outside any hazard or pre-guard window
        if self.state == FireShieldState.INTERVENING and self.active_interval is not None:
            active_guard_start = self._guard_start_ms(self.active_interval)
            if presentation_ms >= self.active_interval.safe_resume_ms:
                resolved_id = self.active_interval.interval_id
                self.state = FireShieldState.RESOLVED
                self.active_interval = None
                self.pending_seek_target_ms = None
                self.seek_failure_count = 0
                self.last_emitted_action = PlayerActionType.CONTINUE
                self.current_position_ms = presentation_ms
                return self._record_command(
                    PlayerCommand(
                        action=PlayerActionType.CONTINUE,
                        state=FireShieldState.RESOLVED,
                        presentation_ms=presentation_ms,
                        reason_code="HAZARD_INTERVAL_RESOLVED_AT_SAFE_RESUME",
                        requires_surface_blanking=False,
                        interval_id=resolved_id,
                        audit_evidence={"resolved_at_ms": presentation_ms},
                    )
                )
            elif is_explicit_seek and presentation_ms < active_guard_start:
                self.state = FireShieldState.ARMED
                self.active_interval = None
                self.last_intervened_interval_id = None
                self.pending_seek_target_ms = None
                self.seek_failure_count = 0
                self.last_emitted_action = PlayerActionType.CONTINUE
                self.current_position_ms = presentation_ms
                return self._record_command(
                    PlayerCommand(
                        action=PlayerActionType.CONTINUE,
                        state=FireShieldState.ARMED,
                        presentation_ms=presentation_ms,
                        reason_code="REARMED_BEFORE_HAZARD_AFTER_SEEK",
                        requires_surface_blanking=False,
                        audit_evidence={"rearmed_at_ms": presentation_ms},
                    )
                )
            else:
                self.current_position_ms = presentation_ms
                return self._record_command(
                    PlayerCommand(
                        action=PlayerActionType.PAUSE,
                        state=FireShieldState.INTERVENING,
                        presentation_ms=presentation_ms,
                        target_seek_ms=self.active_interval.safe_resume_ms,
                        reason_code="HOLD_INSIDE_POST_HAZARD_SAFETY_MARGIN",
                        requires_surface_blanking=True,
                        interval_id=self.active_interval.interval_id,
                        hazard_types=self.active_interval.types,
                        context_cue_ids=self.active_interval.context_cue_ids,
                        audit_evidence={
                            "interval_end_ms": self.active_interval.end_ms,
                            "safe_resume_ms": self.active_interval.safe_resume_ms,
                        },
                    )
                )

        if self.state == FireShieldState.RESOLVED and prev_ms is not None and presentation_ms < prev_ms:
            self.last_intervened_interval_id = None
            self.state = FireShieldState.ARMED

        self.current_position_ms = presentation_ms
        self.last_emitted_action = PlayerActionType.CONTINUE
        return self._record_command(
            PlayerCommand(
                action=PlayerActionType.CONTINUE,
                state=self.state,
                presentation_ms=presentation_ms,
                reason_code="PLAYBACK_SAFE_CONTINUE",
                requires_surface_blanking=False,
                audit_evidence={
                    "rendition_set_id": self.session.rendition_set_id,
                    "verified": self.verified,
                },
            )
        )


def build_signed_v2_sidecar_json(
    *,
    tenant_id: str,
    asset_revision_id: str,
    rendition_set_id: str,
    rendition_playlist_sha256: str,
    detector_version: str,
    review_state: str,
    intervals: Optional[List[Dict[str, Any]]],
    key_id: str,
    private_key_seed_32b: bytes,
    signing_alg: str = SUPPORTED_SIGNING_ALG_V2,
    zero_findings_attested: bool = False,
    schema_version: int = SCHEMA_VERSION_V2_ED25519,
    timebase: str = SUPPORTED_TIMEBASE,
) -> str:
    """
    Constructs a canonical, digest-locked, and Ed25519-signed v2 sidecar JSON string.
    Binds signing_alg and key_id inside the signed canonical payload.
    """
    payload: Dict[str, Any] = {
        "schema_version": schema_version,
        "signing_alg": signing_alg,
        "key_id": key_id,
        "tenant_id": tenant_id,
        "asset_revision_id": asset_revision_id,
        "rendition_set_id": rendition_set_id,
        "rendition_playlist_sha256": rendition_playlist_sha256,
        "timebase": timebase,
        "detector_version": detector_version,
        "review_state": review_state,
    }
    if intervals is not None:
        payload["intervals"] = intervals
    if zero_findings_attested:
        payload["zero_findings_attested"] = True

    digest = compute_canonical_manifest_digest(payload)
    payload["manifest_digest"] = digest
    payload["signature"] = compute_ed25519_signature(
        manifest_digest=digest,
        key_id=key_id,
        private_seed_32b=private_key_seed_32b,
        schema_version=schema_version,
        signing_alg=signing_alg,
    )
    return json.dumps(payload, indent=2, sort_keys=True)


def build_signed_sidecar_json(
    *,
    tenant_id: str,
    asset_revision_id: str,
    rendition_set_id: str,
    rendition_playlist_sha256: str,
    detector_version: str,
    review_state: str,
    intervals: Optional[List[Dict[str, Any]]],
    hmac_secret: Optional[bytes] = None,
    private_key_seed_32b: Optional[bytes] = None,
    key_id: str = "pub-01-ed25519-v2",
    zero_findings_attested: bool = False,
    schema_version: int = SCHEMA_VERSION_V1_LEGACY_HMAC,
    timebase: str = SUPPORTED_TIMEBASE,
) -> str:
    """
    Legacy v1 HMAC sidecar builder (or v2 Ed25519 when private_key_seed_32b is provided).
    Note: v1 HMAC sidecars are rejected by the default production verifier
    (allow_legacy_v1_hmac=False).
    """
    if private_key_seed_32b is not None or schema_version == SCHEMA_VERSION_V2_ED25519:
        if private_key_seed_32b is None:
            raise ValueError("private_key_seed_32b is required when building schema_version=2 sidecar.")
        return build_signed_v2_sidecar_json(
            tenant_id=tenant_id,
            asset_revision_id=asset_revision_id,
            rendition_set_id=rendition_set_id,
            rendition_playlist_sha256=rendition_playlist_sha256,
            detector_version=detector_version,
            review_state=review_state,
            intervals=intervals,
            key_id=key_id,
            private_key_seed_32b=private_key_seed_32b,
            zero_findings_attested=zero_findings_attested,
            schema_version=schema_version,
            timebase=timebase,
        )

    payload: Dict[str, Any] = {
        "schema_version": schema_version,
        "tenant_id": tenant_id,
        "asset_revision_id": asset_revision_id,
        "rendition_set_id": rendition_set_id,
        "rendition_playlist_sha256": rendition_playlist_sha256,
        "timebase": timebase,
        "detector_version": detector_version,
        "review_state": review_state,
    }
    if intervals is not None:
        payload["intervals"] = intervals
    if zero_findings_attested:
        payload["zero_findings_attested"] = True

    digest = compute_canonical_manifest_digest(payload)
    payload["manifest_digest"] = digest
    if hmac_secret is not None:
        payload["signature"] = compute_hmac_signature(digest, hmac_secret)
    else:
        payload["signature"] = "unimplemented:kms-ed25519-pending"
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    demo_seed = hashlib.sha256(b"fireshield-cli-demo-ed25519-seed-only").digest()
    demo_key_id = "pub-01-ed25519-demo"
    demo_pub_hex = derive_ed25519_public_key_hex(demo_seed)
    source_rev = f"sha256:{hashlib.sha256(b'MEZZANINE_MASTER_V2').hexdigest()}"
    playlist_sha = f"sha256:{hashlib.sha256(b'#EXTM3U_RENDITION_1080P_V2').hexdigest()}"

    session_ctx = PlaybackSessionContext(
        tenant_id="publisher-01",
        asset_revision_id=source_rev,
        rendition_set_id="release-2026-09-28-v2",
        rendition_playlist_sha256=playlist_sha,
    )
    sidecar_str = build_signed_v2_sidecar_json(
        tenant_id="publisher-01",
        asset_revision_id=source_rev,
        rendition_set_id="release-2026-09-28-v2",
        rendition_playlist_sha256=playlist_sha,
        detector_version="fireshield-screening-proto:0.3.0-sdr-bt709-2d",
        review_state="approved",
        intervals=[
            {
                "interval_id": "int-001",
                "start_ms": 42000,
                "end_ms": 51000,
                "safe_resume_ms": 51500,
                "types": ["luminance_flash"],
                "policy": "pause_or_skip",
                "context_cue_ids": ["cue-184", "cue-185"],
            }
        ],
        key_id=demo_key_id,
        private_key_seed_32b=demo_seed,
    )
    runtime = FireShieldRuntime(
        session_ctx,
        sidecar_str,
        trusted_public_keys={demo_key_id: demo_pub_hex},
        pre_guard_ms=500,
    )
    print(f"[FireShieldRuntime v2 Ed25519] Initial state: {runtime.state.value} (verified={runtime.verified})")
    for tick in (0, 41499, 41500, 41500, 51500):
        cmd = runtime.update_player_head(tick)
        print(
            f"  tick={tick}ms -> state={cmd.state.value}, action={cmd.action.value}, "
            f"blank={cmd.requires_surface_blanking}, reason={cmd.reason_code}"
        )
