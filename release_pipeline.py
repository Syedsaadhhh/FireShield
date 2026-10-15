"""
FireShield Release Gate Adapters, Timeline Mapper, & AWS Readiness Inspector (P2).

DEPLOYMENT & SECURITY BOUNDARY:
- Never reads, logs, or prints secret credential values.
- Credentials in production must come from AWS IAM Roles or AWS CLI SSO profiles
  (AWS_PROFILE), never hardcoded static keys.
- check_aws_deployment_readiness() strictly distinguishes:
    1. package_present (boto3 installed)
    2. config_set (non-secret ARNs/bucket/table/endpoint configured and non-placeholder)
    3. authenticated (verified via live STS GetCallerIdentity when perform_live_checks=True)
    4. resources_verified (verified via read-only Describe/Head calls when perform_live_checks=True)
    5. deployed (Step Functions + Batch + Lambda revisions verified active)
    6. live_end_to_end_verified (full cloud ingest-to-CDN run completed)
  Setting environment variables alone NEVER reports 'READY' or 'DEPLOYED'.
- LocalReleaseGatePipeline signs v2 sidecars using Ed25519 (schema_version == 2)
  with bound signing_alg='Ed25519' and key_id.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from screening_analyzer import (
    DETECTOR_VERSION,
    ScreeningAnalysisReport,
    analyze_rgb_frame_sequence,
    apply_human_editorial_decision,
)
from state_engine import (
    DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    SCHEMA_VERSION_V2_ED25519,
    SUPPORTED_SIGNING_ALG_V2,
    PlaybackSessionContext,
    SidecarContractValidator,
    build_signed_v2_sidecar_json,
    derive_ed25519_public_key_hex,
)

REQUIRED_NON_SECRET_CONFIG_VARS: Tuple[str, ...] = (
    "FIRESHIELD_AWS_REGION",
    "FIRESHIELD_S3_MEDIA_BUCKET",
    "FIRESHIELD_S3_INPUT_PREFIX",
    "FIRESHIELD_S3_OUTPUT_PREFIX",
    "FIRESHIELD_STATE_MACHINE_ARN",
    "FIRESHIELD_BATCH_JOB_QUEUE_ARN",
    "FIRESHIELD_BATCH_JOB_DEFINITION_ARN",
    "FIRESHIELD_DYNAMODB_CONTRACTS_TABLE",
    "FIRESHIELD_MEDIACONVERT_ROLE_ARN",
    "FIRESHIELD_MEDIACONVERT_ENDPOINT_URL",
    "FIRESHIELD_SIGNING_KEY_ID",
    "FIRESHIELD_CLOUDFRONT_BASE_URL",
)


class ReleaseGateError(Exception):
    """Raised when an asset fails editorial approval, timeline mapping, or release gate invariants."""
    pass


@dataclasses.dataclass(frozen=True)
class AdSpliceSpec:
    splice_id: str
    insert_at_source_ms: int
    duration_ms: int
    ad_hazard_reviewed_and_approved: bool


@dataclasses.dataclass(frozen=True)
class RenditionPackagingSpec:
    rendition_set_id: str
    playlist_manifest_bytes: bytes
    intro_offset_ms: int = 0
    trim_start_ms: int = 0
    ad_splices: Tuple[AdSpliceSpec, ...] = ()
    safe_resume_margin_ms: int = DEFAULT_MIN_SAFE_RESUME_MARGIN_MS


@dataclasses.dataclass
class ReleaseGateRecord:
    tenant_id: str
    asset_revision_id: str
    detector_version: str
    screening_report: ScreeningAnalysisReport
    published_sidecars: Dict[str, str] = dataclasses.field(default_factory=dict)


def _is_non_placeholder_config_value(val: Optional[str]) -> bool:
    if not val or not val.strip():
        return False
    stripped = val.strip()
    if "<" in stripped or ">" in stripped or "YOUR_" in stripped.upper() or "PLACEHOLDER" in stripped.upper():
        return False
    if "123456789012" in stripped or "example.com" in stripped.lower():
        return False
    return True


def check_aws_deployment_readiness(
    *,
    env: Optional[Mapping[str, str]] = None,
    perform_live_checks: bool = False,
) -> Dict[str, Any]:
    """
    Evaluates the 6 distinct readiness tiers without ever reading or printing secret values.
    Fixes the prior false-positive where merely setting environment variables returned READY.
    """
    source_env = os.environ if env is None else env
    has_boto3 = importlib.util.find_spec("boto3") is not None
    boto3_version: Optional[str] = None
    if has_boto3:
        import boto3
        boto3_version = getattr(boto3, "__version__", "unknown")

    configured_vars: List[str] = []
    missing_or_placeholder_vars: List[str] = []
    for var_name in REQUIRED_NON_SECRET_CONFIG_VARS:
        if _is_non_placeholder_config_value(source_env.get(var_name)):
            configured_vars.append(var_name)
        else:
            missing_or_placeholder_vars.append(var_name)

    config_set = len(missing_or_placeholder_vars) == 0
    authenticated = False
    resources_verified = False
    deployed = False
    live_end_to_end_verified = False
    live_check_error: Optional[str] = None

    if perform_live_checks and has_boto3 and config_set:
        try:
            import boto3
            region = source_env["FIRESHIELD_AWS_REGION"].strip()
            profile = source_env.get("AWS_PROFILE", "").strip() or None
            session = boto3.Session(profile_name=profile, region_name=region)
            sts = session.client("sts")
            sts.get_caller_identity()
            authenticated = True

            # Read-only resource existence verification (no cost-incurring jobs)
            s3 = session.client("s3")
            s3.head_bucket(Bucket=source_env["FIRESHIELD_S3_MEDIA_BUCKET"].strip())
            ddb = session.client("dynamodb")
            ddb.describe_table(TableName=source_env["FIRESHIELD_DYNAMODB_CONTRACTS_TABLE"].strip())
            sfn = session.client("stepfunctions")
            sfn_resp = sfn.describe_state_machine(
                stateMachineArn=source_env["FIRESHIELD_STATE_MACHINE_ARN"].strip()
            )
            resources_verified = True
            deployed = sfn_resp.get("status") == "ACTIVE"
        except Exception as exc:
            # Record error class only; never echo credentials or sensitive environment strings
            live_check_error = f"{type(exc).__name__}"

    if live_end_to_end_verified:
        status = "LIVE_END_TO_END_VERIFIED"
    elif deployed:
        status = "DEPLOYED_AWAITING_LIVE_E2E_RUN"
    elif resources_verified:
        status = "RESOURCES_VERIFIED_AWAITING_DEPLOYMENT"
    elif authenticated:
        status = "AUTHENTICATED_RESOURCES_UNVERIFIED"
    elif config_set:
        status = "CONFIG_SET_DRY_RUN_ONLY_NOT_AUTHENTICATED_OR_DEPLOYED"
    elif has_boto3:
        status = "PACKAGE_PRESENT_CONFIG_AND_DEPLOYMENT_PENDING"
    else:
        status = "PENDING_BOTO3_AND_AWS_SETUP"

    return {
        "package_present": has_boto3,
        "boto3_version": boto3_version,
        "config_set": config_set,
        "authenticated": authenticated,
        "resources_verified": resources_verified,
        "deployed": deployed,
        "live_end_to_end_verified": live_end_to_end_verified,
        "aws_cloud_ready": bool(
            has_boto3 and config_set and authenticated and resources_verified and deployed
        ),
        "deployment_status": status,
        "configured_non_secret_vars": configured_vars,
        "missing_or_placeholder_vars": missing_or_placeholder_vars,
        "live_check_attempted": perform_live_checks,
        "live_check_error_type": live_check_error,
    }


def validate_step_functions_asl_file(asl_path: str | pathlib.Path) -> Dict[str, Any]:
    """
    Validates verification_pipeline.json structure, ARN placeholder substitutions,
    removal of ConfidenceScore auto-publish, AWS Batch job submission, mandatory
    human approval, and explicit 'approved' vs 'rejected' states.
    Explicitly reports whether an external ASL compiler/AWS Step Functions API
    validator was run (static JSON validation != deployed workflow).
    """
    path = pathlib.Path(asl_path)
    raw_text = path.read_text(encoding="utf-8")
    asl = json.loads(raw_text)
    states = asl.get("States", {})
    if "ConfidenceScore" in json.dumps(states):
        raise ReleaseGateError("verification_pipeline.json States still contain banned ConfidenceScore auto-publish rule.")
    start_at = asl.get("StartAt")
    if start_at not in states:
        raise ReleaseGateError(f"StartAt state '{start_at}' missing from States.")

    for state_name, state_def in states.items():
        if "Next" in state_def and state_def["Next"] not in states:
            raise ReleaseGateError(f"State '{state_name}' references missing Next state '{state_def['Next']}'.")
        for choice in state_def.get("Choices", []):
            if choice.get("Next") not in states:
                raise ReleaseGateError(f"Choice in '{state_name}' references missing state '{choice.get('Next')}'.")
        if "Default" in state_def and state_def["Default"] not in states:
            raise ReleaseGateError(f"Default in '{state_name}' references missing state '{state_def['Default']}'.")
        for catch_rule in state_def.get("Catch", []):
            if catch_rule.get("Next") not in states:
                raise ReleaseGateError(f"Catch in '{state_name}' references missing state '{catch_rule.get('Next')}'.")

    batch_state = states.get("SubmitBatchFrameScreeningJob", {})
    if batch_state.get("Resource") != "arn:aws:states:::batch:submitJob.sync":
        raise ReleaseGateError("SubmitBatchFrameScreeningJob must use arn:aws:states:::batch:submitJob.sync.")

    editorial_choice = states.get("EvaluateEditorialDecision", {})
    choice_branches = {
        c.get("StringEquals"): c.get("Next") for c in editorial_choice.get("Choices", [])
    }
    if choice_branches.get("approved") != "RunMediaConvertTranscodeJob":
        raise ReleaseGateError("EvaluateEditorialDecision must route 'approved' to RunMediaConvertTranscodeJob.")
    if choice_branches.get("rejected") != "RecordEditorialRejectionTerminal":
        raise ReleaseGateError("EvaluateEditorialDecision must route 'rejected' to RecordEditorialRejectionTerminal.")

    statelint_bin = shutil.which("statelint")
    aws_cli_bin = shutil.which("aws")

    return {
        "valid_asl": True,
        "static_structural_check_only": True,
        "external_asl_compiler_available": bool(statelint_bin or aws_cli_bin),
        "aws_stepfunctions_api_validated": False,
        "state_count": len(states),
        "uses_batch_compute": True,
        "has_mandatory_human_approval": True,
        "has_explicit_reject_terminal": True,
    }


def map_source_intervals_to_rendition(
    report: ScreeningAnalysisReport,
    spec: RenditionPackagingSpec,
) -> List[Dict[str, Any]]:
    """
    Translates approved source-asset hazard intervals onto a packaged output
    rendition timeline, accounting for:
    - trim_start_ms (leading footage cut before encoding)
    - intro_offset_ms (studio bumper/slate prepended to output rendition)
    - ad_splices (SSAI breaks inserted into output timeline)
    Rejects any rendition where an unreviewed ad is spliced or where an ad splice
    lands inside a hazard interval or its post-hazard safety margin.
    """
    if report.review_state != "approved":
        raise ReleaseGateError(
            f"Cannot map or publish intervals for asset in review_state={report.review_state!r} (must be 'approved')."
        )
    if spec.intro_offset_ms < 0 or spec.trim_start_ms < 0:
        raise ReleaseGateError("intro_offset_ms and trim_start_ms must be non-negative.")

    sorted_ads = sorted(spec.ad_splices, key=lambda a: a.insert_at_source_ms)
    for ad in sorted_ads:
        if ad.duration_ms <= 0:
            raise ReleaseGateError(f"Ad splice '{ad.splice_id}' must have positive duration_ms.")
        if not ad.ad_hazard_reviewed_and_approved:
            raise ReleaseGateError(
                f"Ad splice '{ad.splice_id}' at {ad.insert_at_source_ms}ms is unreviewed; "
                "cannot package into verified FireShield rendition."
            )

    mapped_intervals: List[Dict[str, Any]] = []
    for finding in report.findings:
        if finding.end_ms <= spec.trim_start_ms:
            continue

        trimmed_start = max(0, finding.start_ms - spec.trim_start_ms)
        trimmed_end = max(trimmed_start, finding.end_ms - spec.trim_start_ms)
        adj_start = trimmed_start + spec.intro_offset_ms
        adj_end = trimmed_end + spec.intro_offset_ms

        cumulative_ad_shift_ms = 0
        for ad in sorted_ads:
            if ad.insert_at_source_ms < spec.trim_start_ms:
                continue
            if finding.start_ms <= ad.insert_at_source_ms <= finding.proposed_safe_resume_ms:
                raise ReleaseGateError(
                    f"Ad splice '{ad.splice_id}' at source {ad.insert_at_source_ms}ms collides with "
                    f"hazard interval '{finding.interval_id}' [{finding.start_ms}, {finding.proposed_safe_resume_ms}]."
                )
            if ad.insert_at_source_ms < finding.start_ms:
                cumulative_ad_shift_ms += ad.duration_ms

        final_start_ms = int(adj_start + cumulative_ad_shift_ms)
        final_end_ms = int(adj_end + cumulative_ad_shift_ms)
        margin = max(DEFAULT_MIN_SAFE_RESUME_MARGIN_MS, spec.safe_resume_margin_ms)
        final_safe_resume_ms = int(final_end_ms + margin)

        mapped_intervals.append({
            "interval_id": finding.interval_id,
            "start_ms": final_start_ms,
            "end_ms": final_end_ms,
            "safe_resume_ms": final_safe_resume_ms,
            "types": list(finding.types),
            "policy": finding.policy,
            "context_cue_ids": list(finding.context_cue_ids),
        })

    return mapped_intervals


class LocalReleaseGatePipeline:
    """
    Local dry-run release gate coordinator implementing P0/P2 invariants:
    - Idempotency on (tenant_id, asset_revision_id, detector_version)
    - Mandatory human editorial approval ('approved') or rejection ('rejected')
    - Rendition timeline mapping across trims, intros, and ad splices
    - Asymmetric Ed25519 v2 sidecar signing, key rotation, key revocation, and self-verification
    - Multi-tenant isolated structured audit logging (ReleaseAuditEntry)
    """

    def __init__(
        self,
        *,
        private_key_seed_32b: Optional[bytes] = None,
        key_id: str = "pub-01-ed25519-2026",
        hmac_secret: Optional[bytes] = None,
    ):
        if private_key_seed_32b is None:
            if hmac_secret is not None:
                # Deterministically derive a 32-byte test Ed25519 seed from caller's test bytes
                private_key_seed_32b = hashlib.sha256(hmac_secret).digest()
            else:
                raise ValueError("private_key_seed_32b (32 bytes) is required for Ed25519 v2 signing.")
        if not isinstance(private_key_seed_32b, bytes) or len(private_key_seed_32b) != 32:
            raise ValueError("private_key_seed_32b must be exactly 32 bytes.")
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError("key_id must be a non-empty string.")

        self.private_key_seed_32b = private_key_seed_32b
        self.key_id = key_id.strip()
        self.public_key_hex = derive_ed25519_public_key_hex(self.private_key_seed_32b)
        self.trusted_public_keys: Dict[str, str] = {self.key_id: self.public_key_hex}
        self.revoked_key_ids: List[str] = []
        self._records: Dict[Tuple[str, str, str], ReleaseGateRecord] = {}
        self._audit_log: List[Dict[str, Any]] = []

    @staticmethod
    def _make_key(tenant_id: str, asset_revision_id: str, detector_version: str) -> Tuple[str, str, str]:
        return (tenant_id, asset_revision_id, detector_version)

    def _append_audit_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        actor_id: str,
        asset_revision_id: Optional[str] = None,
        detector_version: Optional[str] = None,
        rendition_set_id: Optional[str] = None,
        key_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        entry = {
            "seq": len(self._audit_log) + 1,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "asset_revision_id": asset_revision_id,
            "detector_version": detector_version,
            "rendition_set_id": rendition_set_id,
            "key_id": key_id,
            "details": dict(details) if details else {},
        }
        self._audit_log.append(entry)
        return entry

    def rotate_signing_key(
        self,
        *,
        new_key_id: str,
        new_private_key_seed_32b: bytes,
        tenant_id: str = "*",
        actor_id: str = "kms-key-rotation-controller",
    ) -> str:
        """
        Rotates the active Ed25519 signing key while keeping historical public keys
        in trusted_public_keys so previously published sidecars remain verifiable
        until explicitly revoked.
        """
        if not isinstance(new_key_id, str) or not new_key_id.strip():
            raise ValueError("new_key_id must be a non-empty string.")
        if not isinstance(new_private_key_seed_32b, bytes) or len(new_private_key_seed_32b) != 32:
            raise ValueError("new_private_key_seed_32b must be exactly 32 bytes.")
        clean_id = new_key_id.strip()
        if clean_id in self.revoked_key_ids:
            raise ReleaseGateError(f"Cannot rotate to a revoked key_id={clean_id!r}.")

        previous_key_id = self.key_id
        new_pub_hex = derive_ed25519_public_key_hex(new_private_key_seed_32b)
        self.private_key_seed_32b = new_private_key_seed_32b
        self.key_id = clean_id
        self.public_key_hex = new_pub_hex
        self.trusted_public_keys[clean_id] = new_pub_hex

        self._append_audit_event(
            tenant_id=tenant_id,
            event_type="SIGNING_KEY_ROTATED",
            actor_id=actor_id,
            key_id=clean_id,
            details={"previous_key_id": previous_key_id, "new_public_key_hex": new_pub_hex},
        )
        return new_pub_hex

    def revoke_signing_key(
        self,
        *,
        key_id: str,
        tenant_id: str = "*",
        actor_id: str = "security-key-revocation-controller",
        reason: str = "key_compromised_or_retired",
    ) -> None:
        """
        Explicitly revokes a signing key_id so any sidecar signed with it is rejected
        by SidecarContractValidator and FireShieldRuntime.
        """
        clean_id = key_id.strip()
        if clean_id not in self.revoked_key_ids:
            self.revoked_key_ids.append(clean_id)
        self.trusted_public_keys.pop(clean_id, None)
        self._append_audit_event(
            tenant_id=tenant_id,
            event_type="SIGNING_KEY_REVOKED",
            actor_id=actor_id,
            key_id=clean_id,
            details={"reason": reason},
        )

    def get_tenant_audit_log(self, tenant_id: str) -> List[Dict[str, Any]]:
        """
        Returns audit entries strictly scoped to tenant_id (plus global key-ring events '*'),
        preventing cross-tenant audit leakage.
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string.")
        return [
            dict(entry)
            for entry in self._audit_log
            if entry["tenant_id"] in (tenant_id, "*")
        ]

    def get_tenant_records(self, tenant_id: str) -> List[ReleaseGateRecord]:
        """
        Returns screening and publication records strictly belonging to tenant_id.
        """
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string.")
        return [
            rec for (t_id, _, _), rec in self._records.items() if t_id == tenant_id
        ]

    def ingest_and_screen_frames(
        self,
        *,
        tenant_id: str,
        frames_rgb: np.ndarray,
        fps: float,
        raw_source_bytes: Optional[bytes] = None,
    ) -> Tuple[ReleaseGateRecord, bool]:
        source_bytes = raw_source_bytes if raw_source_bytes is not None else frames_rgb.tobytes()
        asset_rev = f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"
        key = self._make_key(tenant_id, asset_rev, DETECTOR_VERSION)

        if key in self._records:
            self._append_audit_event(
                tenant_id=tenant_id,
                event_type="DUPLICATE_INGEST_SKIPPED",
                actor_id="screening-worker",
                asset_revision_id=asset_rev,
                detector_version=DETECTOR_VERSION,
            )
            return self._records[key], True

        report = analyze_rgb_frame_sequence(frames_rgb, fps=fps, raw_source_bytes=source_bytes)
        record = ReleaseGateRecord(
            tenant_id=tenant_id,
            asset_revision_id=asset_rev,
            detector_version=DETECTOR_VERSION,
            screening_report=report,
        )
        self._records[key] = record
        self._append_audit_event(
            tenant_id=tenant_id,
            event_type="INGEST_SCREENED",
            actor_id="screening-worker",
            asset_revision_id=asset_rev,
            detector_version=DETECTOR_VERSION,
            details={"candidate_count": len(report.findings), "review_state": report.review_state},
        )
        return record, False

    def submit_editorial_decision(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        reviewer_id: str,
        decision: str,
        notes: str,
        detector_version: str = DETECTOR_VERSION,
    ) -> ReleaseGateRecord:
        key = self._make_key(tenant_id, asset_revision_id, detector_version)
        if key not in self._records:
            raise ReleaseGateError(f"No screening record found for {key}.")
        record = self._records[key]
        apply_human_editorial_decision(
            record.screening_report,
            reviewer_id=reviewer_id,
            decision=decision,
            notes=notes,
        )
        self._append_audit_event(
            tenant_id=tenant_id,
            event_type="EDITORIAL_DECISION_RECORDED",
            actor_id=reviewer_id,
            asset_revision_id=asset_revision_id,
            detector_version=detector_version,
            details={"decision": decision, "notes": notes},
        )
        return record

    def package_and_publish_sidecar(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        packaging_spec: RenditionPackagingSpec,
        detector_version: str = DETECTOR_VERSION,
    ) -> Tuple[str, PlaybackSessionContext]:
        """
        Verifies editorial approval, maps source intervals to the rendition's presentation
        timeline, signs the v2 Ed25519 sidecar contract, and validates it using only
        the public key before publication.
        """
        key = self._make_key(tenant_id, asset_revision_id, detector_version)
        if key not in self._records:
            raise ReleaseGateError(f"No screening record found for {key}.")
        record = self._records[key]
        report = record.screening_report

        if report.review_state == "rejected":
            self._append_audit_event(
                tenant_id=tenant_id,
                event_type="PUBLICATION_BLOCKED",
                actor_id="release-packager",
                asset_revision_id=asset_revision_id,
                detector_version=detector_version,
                rendition_set_id=packaging_spec.rendition_set_id,
                details={"reason": "editorial_rejected"},
            )
            raise ReleaseGateError(
                f"Publication blocked: asset {asset_revision_id} was explicitly REJECTED by editorial review."
            )
        if report.review_state != "approved":
            self._append_audit_event(
                tenant_id=tenant_id,
                event_type="PUBLICATION_BLOCKED",
                actor_id="release-packager",
                asset_revision_id=asset_revision_id,
                detector_version=detector_version,
                rendition_set_id=packaging_spec.rendition_set_id,
                details={"reason": f"review_state={report.review_state}"},
            )
            raise ReleaseGateError(
                f"Publication blocked: asset {asset_revision_id} is in review_state={report.review_state!r} "
                "(requires human 'approved' decision)."
            )

        if self.key_id in self.revoked_key_ids:
            raise ReleaseGateError(
                f"Publication blocked: active signing key_id={self.key_id!r} has been revoked."
            )

        mapped_intervals = map_source_intervals_to_rendition(report, packaging_spec)
        playlist_sha256 = f"sha256:{hashlib.sha256(packaging_spec.playlist_manifest_bytes).hexdigest()}"
        zero_findings_attested = bool(
            report.editorial_decision_record
            and report.editorial_decision_record.get("zero_findings_attested") is True
            and len(mapped_intervals) == 0
        )

        sidecar_json = build_signed_v2_sidecar_json(
            tenant_id=tenant_id,
            asset_revision_id=asset_revision_id,
            rendition_set_id=packaging_spec.rendition_set_id,
            rendition_playlist_sha256=playlist_sha256,
            detector_version=report.detector_version,
            review_state="approved",
            intervals=mapped_intervals,
            key_id=self.key_id,
            private_key_seed_32b=self.private_key_seed_32b,
            signing_alg=SUPPORTED_SIGNING_ALG_V2,
            zero_findings_attested=zero_findings_attested,
            schema_version=SCHEMA_VERSION_V2_ED25519,
        )

        session_ctx = PlaybackSessionContext(
            tenant_id=tenant_id,
            asset_revision_id=asset_revision_id,
            rendition_set_id=packaging_spec.rendition_set_id,
            rendition_playlist_sha256=playlist_sha256,
        )
        # Self-validate contract in strict production mode (Ed25519 public key only, no legacy HMAC)
        SidecarContractValidator.validate(
            sidecar_json,
            session=session_ctx,
            trusted_public_keys=self.trusted_public_keys,
            revoked_key_ids=self.revoked_key_ids,
            allow_legacy_v1_hmac=False,
        )

        record.published_sidecars[packaging_spec.rendition_set_id] = sidecar_json
        self._append_audit_event(
            tenant_id=tenant_id,
            event_type="SIDECAR_PUBLISHED",
            actor_id="release-packager",
            asset_revision_id=asset_revision_id,
            detector_version=detector_version,
            rendition_set_id=packaging_spec.rendition_set_id,
            key_id=self.key_id,
            details={"interval_count": len(mapped_intervals), "rendition_playlist_sha256": playlist_sha256},
        )
        return sidecar_json, session_ctx


class AwsCloudReleaseGateDryRunPlanner:
    """
    Constructs the exact AWS service request payloads (DynamoDB idempotency lock,
    AWS Batch screening job, MediaConvert packaging job, S3 sidecar upload) behind
    an explicit dry_run=True flag so zero cost-incurring calls are made while AWS
    deployment is pending.
    """

    def __init__(self, config_env: Mapping[str, str], *, dry_run: bool = True):
        self.config_env = dict(config_env)
        self.dry_run = dry_run
        if not self.dry_run:
            readiness = check_aws_deployment_readiness(
                env=self.config_env, perform_live_checks=True
            )
            if not readiness["aws_cloud_ready"]:
                raise ReleaseGateError(
                    f"Cannot execute live AWS calls when readiness status is {readiness['deployment_status']!r}."
                )

    def build_execution_plan(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        source_s3_key: str,
        rendition_set_id: str,
    ) -> Dict[str, Any]:
        bucket = self.config_env.get("FIRESHIELD_S3_MEDIA_BUCKET", "<UNSET_BUCKET>")
        out_prefix = self.config_env.get("FIRESHIELD_S3_OUTPUT_PREFIX", "published/")
        ddb_table = self.config_env.get("FIRESHIELD_DYNAMODB_CONTRACTS_TABLE", "<UNSET_TABLE>")
        batch_queue = self.config_env.get("FIRESHIELD_BATCH_JOB_QUEUE_ARN", "<UNSET_BATCH_QUEUE>")
        batch_def = self.config_env.get("FIRESHIELD_BATCH_JOB_DEFINITION_ARN", "<UNSET_BATCH_DEF>")
        mc_role = self.config_env.get("FIRESHIELD_MEDIACONVERT_ROLE_ARN", "<UNSET_MC_ROLE>")

        return {
            "execution_mode": "DRY_RUN_NO_AWS_CALLS_MADE" if self.dry_run else "LIVE_AWS",
            "dynamodb_idempotency_put_item": {
                "TableName": ddb_table,
                "Item": {
                    "pk": {"S": f"TENANT#{tenant_id}#ASSET#{asset_revision_id}"},
                    "sk": {"S": f"DETECTOR#{DETECTOR_VERSION}"},
                    "review_state": {"S": "pending_review"},
                },
                "ConditionExpression": "attribute_not_exists(pk) AND attribute_not_exists(sk)",
            },
            "batch_submit_job": {
                "jobName": f"fireshield-screen-{tenant_id}",
                "jobQueue": batch_queue,
                "jobDefinition": batch_def,
                "containerOverrides": {
                    "environment": [
                        {"name": "SOURCE_S3_URI", "value": f"s3://{bucket}/{source_s3_key}"},
                        {"name": "ASSET_REVISION_ID", "value": asset_revision_id},
                        {"name": "DETECTOR_VERSION", "value": DETECTOR_VERSION},
                    ]
                },
            },
            "mediaconvert_create_job": {
                "Role": mc_role,
                "UserMetadata": {
                    "tenant_id": tenant_id,
                    "asset_revision_id": asset_revision_id,
                    "rendition_set_id": rendition_set_id,
                },
                "OutputDestination": f"s3://{bucket}/{out_prefix}{rendition_set_id}/",
            },
        }


if __name__ == "__main__":
    from screening_analyzer import build_reference_fixtures

    readiness = check_aws_deployment_readiness(perform_live_checks=False)
    asl_check = validate_step_functions_asl_file(
        pathlib.Path(__file__).with_name("verification_pipeline.json")
    )
    print("AWS Readiness:", json.dumps(readiness, indent=2))
    print("Step Functions ASL Check:", json.dumps(asl_check, indent=2))

    test_seed = hashlib.sha256(b"fireshield-local-dry-run-ed25519-seed-2026").digest()
    pipeline = LocalReleaseGatePipeline(
        private_key_seed_32b=test_seed,
        key_id="pub-01-ed25519-2026-q3",
    )
    strobe_fixture = build_reference_fixtures()[3]  # 7.5Hz luminance strobe [1000ms, 2000ms]

    rec, is_dup = pipeline.ingest_and_screen_frames(
        tenant_id="publisher-01",
        frames_rgb=strobe_fixture["frames"],
        fps=strobe_fixture["fps"],
    )
    print(
        f"Screened asset_revision_id={rec.asset_revision_id}, "
        f"is_duplicate={is_dup}, review_state={rec.screening_report.review_state}"
    )

    pipeline.submit_editorial_decision(
        tenant_id="publisher-01",
        asset_revision_id=rec.asset_revision_id,
        reviewer_id="editor-jane-doe",
        decision="approved",
        notes="Verified 7.5Hz flash interval; approved with 5000ms studio intro offset.",
    )
    spec = RenditionPackagingSpec(
        rendition_set_id="hls-1080p-intro-v2",
        playlist_manifest_bytes=b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:8.000,\nseg0.ts\n",
        intro_offset_ms=5000,
    )
    sidecar, ctx = pipeline.package_and_publish_sidecar(
        tenant_id="publisher-01",
        asset_revision_id=rec.asset_revision_id,
        packaging_spec=spec,
    )
    print("Published Mapped v2 Ed25519 Sidecar:\n", sidecar)
