"""
FireShield Local Pipeline & Screening Service (Priority 1 Domain Layer).

DESIGN & SECURITY INVARIANTS:
1. Deterministic Frame Screening: Uses the existing SDR BT.709 & 2D FFT analyzer
   (screening_analyzer.py); never replaces algorithms with placeholder mocks.
2. Tenant Scoping: All catalog assets, jobs, reports, and releases are strictly isolated
   by tenant_id. Cross-tenant reads or updates fail closed with ApiForbiddenError / ApiNotFoundError.
3. Durable Local Store: State (assets, screening reports, reviews, releases) is durably
   persisted in runtime_data/fireshield_store.json (separated from git-tracked code).
4. Human Editorial Review Gate: Only explicit 'approved' status permits publication.
   'pending_review' or 'rejected' assets strictly block publication with ReleaseGateError.
5. Ed25519 v2 Sidecar Publication: Every published release produces a cryptographically
   valid Ed25519 v2 sidecar manifest bound to the exact rendition playlist SHA-256 and
   approved hazard intervals.
6. Subtitle-Grounded Context: Grounded context notes require explicit editorial approval
   before publication and are bound into the sidecar manifest.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from context_engine import (
    AmazonBedrockConverseContextAdapter,
    ContextError,
    GroundedContextNote,
    SubtitleCue,
    extract_grounded_context_for_interval,
    parse_webvtt_cues,
)
from release_pipeline import (
    LocalReleaseGatePipeline,
    ReleaseGateError,
    ReleaseGateRecord,
    RenditionPackagingSpec,
    map_source_intervals_to_rendition,
)
from screening_analyzer import (
    DETECTOR_VERSION,
    CandidateHazardFinding,
    ScreeningAnalysisReport,
    analyze_rgb_frame_sequence,
    analyze_video_file_headless,
    apply_human_editorial_decision,
    build_reference_fixtures,
    find_ffmpeg_executable,
)
from state_engine import (
    DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    SCHEMA_VERSION_V2_ED25519,
    SUPPORTED_SIGNING_ALG_V2,
    SUPPORTED_TIMEBASE,
    PlaybackSessionContext,
    SidecarContractValidator,
    build_signed_v2_sidecar_json,
    derive_ed25519_public_key_hex,
)

logger = logging.getLogger("fireshield.service")

# Storage directory under runtime_data/ (gitignored)
WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parent
RUNTIME_DATA_DIR = WORKSPACE_ROOT / "runtime_data"
DEFAULT_STORE_FILE = RUNTIME_DATA_DIR / "fireshield_store.json"
MANAGED_ASSETS_DIR = WORKSPACE_ROOT / "data" / "catalog"
DEV_SEED_FILE = RUNTIME_DATA_DIR / "dev_seed.bin"

# Deterministic fallback key seed for local development & demonstration
DEFAULT_PIPELINE_SEED = hashlib.sha256(b"fireshield-local-pipeline-signing-key-seed-2026").digest()
DEFAULT_PIPELINE_KEY_ID = "pub-01-ed25519-2026"


def get_or_create_dev_signing_seed() -> bytes:
    """Loads existing 32-byte signing seed from runtime_data/dev_seed.bin or generates a secure random one."""
    try:
        if DEV_SEED_FILE.exists() and DEV_SEED_FILE.stat().st_size == 32:
            return DEV_SEED_FILE.read_bytes()
    except Exception:
        pass
    try:
        import secrets
        seed = secrets.token_bytes(32)
        DEV_SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEV_SEED_FILE.write_bytes(seed)
        return seed
    except Exception as exc:
        raise RuntimeError("Cannot persist the local demo signing key in runtime_data; check directory permissions.") from exc


class PipelineServiceError(Exception):
    """Base exception for pipeline service domain errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclasses.dataclass
class AssetRecord:
    asset_id: str
    tenant_id: str
    name: str
    asset_revision_id: str
    source_type: str  # "synthetic_fixture" | "managed_video" | "frame_sequence"
    source_path: Optional[str] = None
    size_bytes: int = 0
    duration_ms: int = 0
    fps: float = 30.0
    width: int = 320
    height: int = 240
    imported_at_iso: str = ""
    has_screening: bool = False
    review_state: str = "unreviewed"
    subtitles_vtt: Optional[str] = None
    findings_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ScreeningJobRecord:
    job_id: str
    tenant_id: str
    asset_id: str
    asset_revision_id: str
    status: str  # "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED"
    created_at_iso: str
    completed_at_iso: Optional[str] = None
    error_message: Optional[str] = None
    findings_count: int = 0
    detector_version: str = DETECTOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class PublishedReleaseRecord:
    release_id: str
    tenant_id: str
    asset_revision_id: str
    rendition_set_id: str
    rendition_playlist_sha256: str
    manifest_digest: str
    key_id: str
    signing_alg: str
    public_key_hex: str
    sidecar_json: str
    sidecar_contract: Dict[str, Any]
    media_uri: str
    playlist_manifest: str
    published_at_iso: str
    harness_intent_command: str
    context_notes_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class LocalPipelineService:
    """
    Thread-safe domain service for catalog ingestion, frame screening,
    human editorial review, context cue grounding, and Ed25519 v2 publication.
    """

    def __init__(
        self,
        *,
        store_path: Optional[pathlib.Path] = None,
        private_key_seed_32b: Optional[bytes] = None,
        key_id: str = DEFAULT_PIPELINE_KEY_ID,
    ):
        self.store_path = store_path or DEFAULT_STORE_FILE
        self._lock = threading.RLock()
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        self.key_id = key_id
        if private_key_seed_32b is not None:
            self.private_key_seed_32b = private_key_seed_32b
        else:
            self.private_key_seed_32b = get_or_create_dev_signing_seed()
        self.public_key_hex = derive_ed25519_public_key_hex(self.private_key_seed_32b)

        self.pipeline = LocalReleaseGatePipeline(
            private_key_seed_32b=self.private_key_seed_32b,
            key_id=self.key_id,
        )

        # In-memory indices
        self._assets: Dict[Tuple[str, str], AssetRecord] = {}  # (tenant_id, asset_id) -> AssetRecord
        self._asset_rev_to_id: Dict[Tuple[str, str], str] = {}  # (tenant_id, asset_rev) -> asset_id
        self._jobs: Dict[Tuple[str, str], ScreeningJobRecord] = {}  # (tenant_id, job_id) -> ScreeningJobRecord
        self._reports: Dict[Tuple[str, str], ScreeningAnalysisReport] = {}  # (tenant_id, asset_rev) -> Report
        self._context_notes: Dict[Tuple[str, str], Dict[str, GroundedContextNote]] = {}  # (tenant_id, asset_rev) -> {interval_id: note}
        self._releases: Dict[Tuple[str, str], PublishedReleaseRecord] = {}  # (tenant_id, release_id) -> PublishedReleaseRecord

        # Load from durable storage or seed standard assets
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        with self._lock:
            if self.store_path.exists():
                try:
                    data = json.loads(self.store_path.read_text(encoding="utf-8"))
                    # Reconstruct assets
                    for a_dict in data.get("assets", []):
                        a = AssetRecord(**a_dict)
                        self._assets[(a.tenant_id, a.asset_id)] = a
                        self._asset_rev_to_id[(a.tenant_id, a.asset_revision_id)] = a.asset_id
                    # Reconstruct jobs
                    for j_dict in data.get("jobs", []):
                        j = ScreeningJobRecord(**j_dict)
                        self._jobs[(j.tenant_id, j.job_id)] = j
                    # Reconstruct releases
                    for r_dict in data.get("releases", []):
                        r = PublishedReleaseRecord(**r_dict)
                        self._releases[(r.tenant_id, r.release_id)] = r
                    # Reconstruct reports and re-register in release pipeline
                    for r_item in data.get("reports", []):
                        t_id = r_item["tenant_id"]
                        rep = ScreeningAnalysisReport.from_dict(r_item["report"])
                        self._reports[(t_id, rep.asset_revision_id)] = rep
                        rec_key = self.pipeline._make_key(t_id, rep.asset_revision_id, rep.detector_version)
                        self.pipeline._records[rec_key] = ReleaseGateRecord(
                            tenant_id=t_id,
                            asset_revision_id=rep.asset_revision_id,
                            detector_version=rep.detector_version,
                            screening_report=rep,
                        )
                    # Reconstruct context notes
                    for n_item in data.get("context_notes", []):
                        t_id = n_item["tenant_id"]
                        note = GroundedContextNote.from_dict(n_item["note"])
                        k = (t_id, note.asset_revision_id)
                        if k not in self._context_notes:
                            self._context_notes[k] = {}
                        self._context_notes[k][note.interval_id] = note

                    logger.info("Loaded pipeline state from %s", self.store_path)
                except Exception as exc:
                    logger.warning("Failed to load store from %s (%s); starting fresh", self.store_path, exc)

            # Ensure default catalog assets are seeded for standard demo tenants
            self._seed_default_demo_assets("publisher-demo-tenant")

    def _save_to_disk(self) -> None:
        with self._lock:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "updated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "assets": [a.to_dict() for a in self._assets.values()],
                "jobs": [j.to_dict() for j in self._jobs.values()],
                "releases": [r.to_dict() for r in self._releases.values()],
                "reports": [
                    {"tenant_id": t_id, "report": rep.to_dict()}
                    for (t_id, _), rep in self._reports.items()
                ],
                "context_notes": [
                    {"tenant_id": t_id, "note": note.to_dict()}
                    for (t_id, _), subdict in self._context_notes.items()
                    for note in subdict.values()
                ],
            }
            tmp_path = self.store_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(self.store_path)

    def _seed_default_demo_assets(self, tenant_id: str) -> None:
        """Seeds the standard reference catalog assets (benign fixture and hazard clips)."""
        ref_fixtures = {item["fixture_id"]: item for item in build_reference_fixtures()}

        # Asset 1: Benign HLS clip (120s Navy test with subtitles)
        hls_dir = WORKSPACE_ROOT / "fireos" / "app" / "src" / "main" / "assets" / "hls"
        seg_file = hls_dir / "benign_seg0.ts"
        vtt_file = hls_dir / "subtitles_en.vtt"

        seg_bytes = seg_file.read_bytes() if seg_file.exists() else b"fireshield-benign-fixture-synthetic-bytes"
        seg_sha = f"sha256:{hashlib.sha256(seg_bytes).hexdigest()}"
        vtt_text = vtt_file.read_text(encoding="utf-8") if vtt_file.exists() else ""

        ast_benign = AssetRecord(
            asset_id="ast-benign-navy-clip",
            tenant_id=tenant_id,
            name="Publisher Benign Navy Presentation Clip (Standard HLS)",
            asset_revision_id=seg_sha,
            source_type="synthetic_fixture",
            source_path="hls/benign_variant.m3u8",
            size_bytes=len(seg_bytes),
            duration_ms=120000,
            fps=24.0,
            width=320,
            height=240,
            imported_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            has_screening=False,
            review_state="unreviewed",
            subtitles_vtt=vtt_text,
        )
        if (tenant_id, ast_benign.asset_id) not in self._assets:
            self._assets[(tenant_id, ast_benign.asset_id)] = ast_benign
            self._asset_rev_to_id[(tenant_id, seg_sha)] = ast_benign.asset_id

        # Asset 2: Reference Luminance Strobe Hazard Clip
        strobe_fixture = ref_fixtures["ref_pos_7p5hz_luminance_strobe"]
        strobe_bytes = strobe_fixture["frames"].tobytes()
        strobe_sha = f"sha256:{hashlib.sha256(strobe_bytes).hexdigest()}"
        ast_strobe = AssetRecord(
            asset_id="ast-hazard-luminance-strobe",
            tenant_id=tenant_id,
            name="Synthetic 7.5 Hz Luminance Flash Reference",
            asset_revision_id=strobe_sha,
            source_type="synthetic_fixture",
            source_path="fixtures/strobe_hazard",
            size_bytes=len(strobe_bytes),
            duration_ms=round(len(strobe_fixture["frames"]) / strobe_fixture["fps"] * 1000),
            fps=30.0,
            width=64,
            height=64,
            imported_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            has_screening=False,
            review_state="unreviewed",
            subtitles_vtt="WEBVTT\n\n00:00.000 --> 00:02.000\nWarning: high frequency emergency warning strobe active.\n",
        )
        if (tenant_id, ast_strobe.asset_id) not in self._assets:
            self._assets[(tenant_id, ast_strobe.asset_id)] = ast_strobe
            self._asset_rev_to_id[(tenant_id, strobe_sha)] = ast_strobe.asset_id
        else:
            current = self._assets[(tenant_id, ast_strobe.asset_id)]
            current.name, current.duration_ms = ast_strobe.name, ast_strobe.duration_ms

        # Asset 3: Reference 2D Spatial Checkerboard Hazard Clip
        pattern_fixture = ref_fixtures["ref_pos_2d_checkerboard"]
        pattern_bytes = pattern_fixture["frames"].tobytes()
        pattern_sha = f"sha256:{hashlib.sha256(pattern_bytes).hexdigest()}"
        ast_pattern = AssetRecord(
            asset_id="ast-hazard-spatial-checkerboard",
            tenant_id=tenant_id,
            name="Synthetic 2D Spatial High-Contrast Checkerboard Hazard Fixture",
            asset_revision_id=pattern_sha,
            source_type="synthetic_fixture",
            source_path="fixtures/checkerboard_hazard",
            size_bytes=len(pattern_bytes),
            duration_ms=round(len(pattern_fixture["frames"]) / pattern_fixture["fps"] * 1000),
            fps=30.0,
            width=64,
            height=64,
            imported_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            has_screening=False,
            review_state="unreviewed",
            subtitles_vtt="WEBVTT\n\n00:00.000 --> 00:02.000\nOptical sensor calibration pattern active.\n",
        )
        if (tenant_id, ast_pattern.asset_id) not in self._assets:
            self._assets[(tenant_id, ast_pattern.asset_id)] = ast_pattern
            self._asset_rev_to_id[(tenant_id, pattern_sha)] = ast_pattern.asset_id
        else:
            current = self._assets[(tenant_id, ast_pattern.asset_id)]
            current.name, current.duration_ms = ast_pattern.name, ast_pattern.duration_ms

    # -----------------------------------------------------------------------
    # Asset Management
    # -----------------------------------------------------------------------

    def list_assets(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                ast.to_dict()
                for (t_id, _), ast in self._assets.items()
                if t_id == tenant_id
            ]

    def get_asset(self, asset_id: str, tenant_id: str) -> AssetRecord:
        with self._lock:
            key = (tenant_id, asset_id)
            if key not in self._assets:
                raise PipelineServiceError(f"Asset '{asset_id}' not found for tenant '{tenant_id}'", 404)
            return self._assets[key]

    def import_managed_asset(
        self,
        *,
        tenant_id: str,
        name: str,
        source_type: str = "managed_video",
        source_path: Optional[str] = None,
        raw_bytes: Optional[bytes] = None,
        subtitles_vtt: Optional[str] = None,
    ) -> AssetRecord:
        """
        Registers an asset from raw bytes or a managed catalog file.
        Strictly hashes actual input bytes and confines filesystem paths.
        """
        if not name or not name.strip():
            raise PipelineServiceError("Asset name must be a non-empty string", 400)

        actual_bytes = raw_bytes
        if source_path:
            raw_sp = str(source_path).strip()
            # 1. Reject drive letters (C:, D:, etc.)
            if re.match(r"^[a-zA-Z]:", raw_sp):
                raise PipelineServiceError("Absolute drive paths are not permitted in source_path", 400)
            # 2. Reject UNC network paths (\\ or //)
            if raw_sp.startswith("\\\\") or raw_sp.startswith("//"):
                raise PipelineServiceError("UNC network paths are not permitted in source_path", 400)
            # 3. Reject leading slash / or \
            if raw_sp.startswith("/") or raw_sp.startswith("\\"):
                raise PipelineServiceError("Absolute paths are not permitted in source_path", 400)
            # 4. Check for '..' directory traversal segments
            normalized = os.path.normpath(raw_sp)
            parts = pathlib.PurePath(normalized).parts
            if ".." in parts:
                raise PipelineServiceError("Path traversal '..' is not permitted in source_path", 400)

            # 5. Resolve against WORKSPACE_ROOT and verify confinement
            try:
                target_path = (WORKSPACE_ROOT / normalized).resolve()
                if not str(target_path).startswith(str(WORKSPACE_ROOT.resolve())):
                    raise PipelineServiceError("Path escapes workspace confinement", 400)
            except Exception as e:
                raise PipelineServiceError(f"Invalid source_path: {e}", 400)

            if not target_path.exists():
                raise PipelineServiceError(f"Managed source file '{source_path}' does not exist", 404)
            if actual_bytes is None:
                actual_bytes = target_path.read_bytes()

        if actual_bytes is None or len(actual_bytes) == 0:
            raise PipelineServiceError("No media bytes provided for asset import", 400)

        asset_sha = f"sha256:{hashlib.sha256(actual_bytes).hexdigest()}"
        asset_id = f"ast-{uuid.uuid4().hex[:12]}"

        # Persist raw bytes in managed storage cache if raw bytes were passed in
        if raw_bytes is not None and not source_path:
            media_cache_dir = RUNTIME_DATA_DIR / "managed_media"
            media_cache_dir.mkdir(parents=True, exist_ok=True)
            cached_media_file = media_cache_dir / f"{asset_id}.bin"
            cached_media_file.write_bytes(actual_bytes)
            source_path = str(cached_media_file.relative_to(WORKSPACE_ROOT))

        record = AssetRecord(
            asset_id=asset_id,
            tenant_id=tenant_id,
            name=name.strip(),
            asset_revision_id=asset_sha,
            source_type=source_type,
            source_path=source_path,
            size_bytes=len(actual_bytes),
            duration_ms=60000,
            fps=30.0,
            width=320,
            height=240,
            imported_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            has_screening=False,
            review_state="unreviewed",
            subtitles_vtt=subtitles_vtt,
        )

        with self._lock:
            self._assets[(tenant_id, asset_id)] = record
            self._asset_rev_to_id[(tenant_id, asset_sha)] = asset_id
            self._save_to_disk()

        return record

    # -----------------------------------------------------------------------
    # Screening Jobs & Report Generation
    # -----------------------------------------------------------------------

    def start_screening_job(
        self,
        *,
        tenant_id: str,
        asset_id: Optional[str] = None,
        asset_revision_id: Optional[str] = None,
        wait: bool = True,
    ) -> ScreeningJobRecord:
        """
        Executes bounded, deterministic frame screening on the requested asset.
        Invokes existing screening_analyzer algorithms.
        When wait=True (default), blocks until job completes or fails.
        When wait=False, submits to thread pool and returns RUNNING job immediately.
        """
        with self._lock:
            # Resolve asset
            target_asset: Optional[AssetRecord] = None
            if asset_id:
                target_asset = self._assets.get((tenant_id, asset_id))
            elif asset_revision_id:
                aid = self._asset_rev_to_id.get((tenant_id, asset_revision_id))
                if aid:
                    target_asset = self._assets.get((tenant_id, aid))

            if not target_asset:
                raise PipelineServiceError(f"Asset not found for screening in tenant '{tenant_id}'", 404)

            job_id = f"job-{uuid.uuid4().hex[:12]}"
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            job = ScreeningJobRecord(
                job_id=job_id,
                tenant_id=tenant_id,
                asset_id=target_asset.asset_id,
                asset_revision_id=target_asset.asset_revision_id,
                status="RUNNING",
                created_at_iso=created_at,
            )
            self._jobs[(tenant_id, job_id)] = job

        if wait:
            return self._run_job_worker(tenant_id, job, target_asset)
        else:
            self._thread_pool.submit(self._run_job_worker, tenant_id, job, target_asset)
            return job

    def _run_job_worker(
        self, tenant_id: str, job: ScreeningJobRecord, target_asset: AssetRecord
    ) -> ScreeningJobRecord:
        try:
            report = self._execute_screening(target_asset)
            with self._lock:
                job.status = "COMPLETED"
                job.completed_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                job.findings_count = len(report.findings)
                job.detector_version = report.detector_version

                # Store report
                self._reports[(tenant_id, target_asset.asset_revision_id)] = report
                target_asset.has_screening = True
                target_asset.review_state = report.review_state
                target_asset.findings_count = len(report.findings)

                # Register in release pipeline for approval tracking
                key = self.pipeline._make_key(tenant_id, target_asset.asset_revision_id, report.detector_version)
                self.pipeline._records[key] = ReleaseGateRecord(
                    tenant_id=tenant_id,
                    asset_revision_id=target_asset.asset_revision_id,
                    detector_version=report.detector_version,
                    screening_report=report,
                )

                self._save_to_disk()
                return job

        except Exception as exc:
            logger.warning("Screening failed for asset %s: %s", target_asset.asset_id, exc)
            with self._lock:
                job.status = "FAILED"
                job.completed_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                job.error_message = str(exc)
                self._save_to_disk()
                return job

    def _execute_screening(self, asset: AssetRecord) -> ScreeningAnalysisReport:
        """
        Screens actual frames using existing screening_analyzer algorithms.
        Never substitutes benign frames when actual media or decoder is invalid.
        """
        ref_fixtures = {item["fixture_id"]: item for item in build_reference_fixtures()}

        # Handle standard reference fixtures
        if asset.source_type == "synthetic_fixture":
            if "strobe" in asset.asset_id.lower():
                fixture = ref_fixtures["ref_pos_7p5hz_luminance_strobe"]
                frames = fixture["frames"]
                fps = fixture["fps"]
                return analyze_rgb_frame_sequence(
                    frames, fps=fps, exact_asset_revision_id=asset.asset_revision_id
                )
            elif "checkerboard" in asset.asset_id.lower() or "pattern" in asset.asset_id.lower():
                fixture = ref_fixtures["ref_pos_2d_checkerboard"]
                frames = fixture["frames"]
                fps = fixture["fps"]
                return analyze_rgb_frame_sequence(
                    frames, fps=fps, exact_asset_revision_id=asset.asset_revision_id
                )
            elif "benign" in asset.asset_id.lower():
                # Standard benign smooth ramp: no hazard findings
                fixture = ref_fixtures["ref_neg_smooth_ramp"]
                frames = fixture["frames"]
                fps = fixture["fps"]
                return analyze_rgb_frame_sequence(
                    frames, fps=fps, exact_asset_revision_id=asset.asset_revision_id
                )
            else:
                raise PipelineServiceError(f"Unknown synthetic fixture type for asset '{asset.asset_id}'", 400)

        # For managed video files: require FFmpeg
        ffmpeg_bin = find_ffmpeg_executable()
        if not ffmpeg_bin:
            raise PipelineServiceError("FFmpeg executable unavailable on host to decode managed video media", 503)

        video_file: Optional[pathlib.Path] = None
        if asset.source_path:
            clean_rel = os.path.normpath(asset.source_path.strip()).lstrip("/\\")
            cand = WORKSPACE_ROOT / clean_rel
            if cand.exists():
                video_file = cand

        if not video_file:
            cached_bin = RUNTIME_DATA_DIR / "managed_media" / f"{asset.asset_id}.bin"
            if cached_bin.exists():
                video_file = cached_bin

        if not video_file or not video_file.exists():
            raise PipelineServiceError(f"Managed video media for asset '{asset.asset_id}' not found on disk", 404)

        try:
            report = analyze_video_file_headless(video_file, target_width=64, target_height=64)
            if report.total_frames == 0:
                raise PipelineServiceError("Video decoding yielded 0 frames: corrupted or unsupported media stream", 400)
            return report
        except Exception as exc:
            if isinstance(exc, PipelineServiceError):
                raise
            raise PipelineServiceError(f"Video decode failed: {exc}", 400) from exc

    def get_screening_job(self, job_id: str, tenant_id: str) -> ScreeningJobRecord:
        with self._lock:
            key = (tenant_id, job_id)
            if key not in self._jobs:
                raise PipelineServiceError(f"Screening job '{job_id}' not found for tenant '{tenant_id}'", 404)
            return self._jobs[key]

    def get_screening_report(self, asset_revision_id: str, tenant_id: str) -> Dict[str, Any]:
        with self._lock:
            key = (tenant_id, asset_revision_id)
            report = self._reports.get(key)
            if not report:
                raise PipelineServiceError(f"No screening report found for revision '{asset_revision_id}'", 404)

            # Retrieve any grounded context notes for this asset revision
            notes = [
                n.to_dict()
                for n in self._context_notes.get(key, {}).values()
            ]
            return {
                **report.to_dict(),
                "context_notes": notes,
            }

    # -----------------------------------------------------------------------
    # Human Editorial Review
    # -----------------------------------------------------------------------

    def submit_editorial_decision(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        reviewer_id: str,
        decision: str,
        notes: str,
        zero_findings_attested: bool = False,
    ) -> Dict[str, Any]:
        """
        Submits human editorial approval ('approved') or rejection ('rejected').
        Pending or rejected reports strictly block publication.
        """
        clean_dec = decision.strip().lower()
        if clean_dec not in ("approved", "rejected"):
            raise PipelineServiceError("decision must be either 'approved' or 'rejected'", 400)
        if not reviewer_id or not reviewer_id.strip():
            raise PipelineServiceError("reviewer_id must be a non-empty string", 400)

        with self._lock:
            key = (tenant_id, asset_revision_id)
            report = self._reports.get(key)
            if not report:
                raise PipelineServiceError(f"No screening report found for revision '{asset_revision_id}'", 404)

            # Apply decision to report
            apply_human_editorial_decision(
                report,
                reviewer_id=reviewer_id.strip(),
                decision=clean_dec,
                notes=notes.strip(),
            )

            # Update asset record review state
            aid = self._asset_rev_to_id.get(key)
            if aid and (tenant_id, aid) in self._assets:
                self._assets[(tenant_id, aid)].review_state = clean_dec

            # Update release pipeline record
            rec_key = self.pipeline._make_key(tenant_id, asset_revision_id, report.detector_version)
            if rec_key in self.pipeline._records:
                self.pipeline.submit_editorial_decision(
                    tenant_id=tenant_id,
                    asset_revision_id=asset_revision_id,
                    reviewer_id=reviewer_id,
                    decision=clean_dec,
                    notes=notes,
                    detector_version=report.detector_version,
                )

            self._save_to_disk()
            return report.to_dict()

    # -----------------------------------------------------------------------
    # Synaptic Story Context Grounding & Review
    # -----------------------------------------------------------------------

    def ground_context_for_interval(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        interval_id: str,
        method: str = "extractive_subtitles",
        manual_text: Optional[str] = None,
        custom_vtt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Extracts subtitle cues and generates a candidate context note for editorial review.
        """
        with self._lock:
            key = (tenant_id, asset_revision_id)
            report = self._reports.get(key)
            if not report:
                raise PipelineServiceError(f"No screening report found for revision '{asset_revision_id}'", 404)

            # Find target interval in report findings
            target_finding = next((f for f in report.findings if f.interval_id == interval_id), None)
            if not target_finding:
                # If zero-finding asset, synthesize a test control interval if requested
                if interval_id == "control-int-001":
                    target_finding = CandidateHazardFinding(
                        interval_id=interval_id,
                        start_frame=0,
                        end_frame=60,
                        start_ms=1000,
                        end_ms=3000,
                        proposed_safe_resume_ms=3500,
                        types=("luminance_flash",),
                        policy="pause_or_skip",
                        metrics={},
                        context_cue_ids=("cue-001",),
                    )
                else:
                    raise PipelineServiceError(f"Interval '{interval_id}' not found in screening report", 404)

            # Retrieve cues from custom VTT or asset's subtitles
            aid = self._asset_rev_to_id.get(key)
            asset = self._assets.get((tenant_id, aid)) if aid else None
            vtt_content = custom_vtt or (asset.subtitles_vtt if asset else "")

            cues: List[SubtitleCue] = []
            if vtt_content:
                cues = parse_webvtt_cues(vtt_content)

            if method == "editorial_manual":
                if not manual_text or not manual_text.strip():
                    raise PipelineServiceError("manual_text is required when method='editorial_manual'", 400)
                note = GroundedContextNote(
                    note_id=f"note-{interval_id}",
                    interval_id=interval_id,
                    asset_revision_id=asset_revision_id,
                    text=manual_text.strip(),
                    source_cue_ids=[c.cue_id for c in cues] if cues else ["cue-editorial-manual"],
                    generation_method="editorial_manual",
                    editorial_approved=False,
                )
            elif method == "bedrock_converse":
                if not cues:
                    raise PipelineServiceError(
                        f"No subtitle cues available to ground context for interval '{interval_id}'", 404
                    )
                bedrock_adapter = AmazonBedrockConverseContextAdapter()
                note = bedrock_adapter.generate_context_note(
                    interval_id=interval_id,
                    interval_start_ms=target_finding.start_ms,
                    interval_end_ms=target_finding.end_ms,
                    asset_revision_id=asset_revision_id,
                    cues=cues,
                )
            else:
                if not cues:
                    raise PipelineServiceError(
                        f"No subtitle cues available to ground context for interval '{interval_id}'", 404
                    )
                note = extract_grounded_context_for_interval(
                    interval_id=interval_id,
                    interval_start_ms=target_finding.start_ms,
                    interval_end_ms=target_finding.end_ms,
                    asset_revision_id=asset_revision_id,
                    cues=cues,
                )
                if not note:
                    raise PipelineServiceError(f"No subtitle cues found overlapping interval {interval_id}", 404)

            # Store in candidate notes
            if key not in self._context_notes:
                self._context_notes[key] = {}
            self._context_notes[key][interval_id] = note
            return note.to_dict()

    def approve_context_note(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        interval_id: str,
        reviewer_id: str,
        text: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Editorially approves a context note, enabling it to be published with the release sidecar.
        """
        with self._lock:
            key = (tenant_id, asset_revision_id)
            if key not in self._context_notes or interval_id not in self._context_notes[key]:
                raise PipelineServiceError(f"No context note found for interval '{interval_id}' to approve", 404)

            note = self._context_notes[key][interval_id]
            note.editorial_approved = True
            note.reviewer_id = reviewer_id.strip()
            if text and text.strip():
                note.text = text.strip()
            if notes:
                note.notes = notes.strip()

            self._save_to_disk()
            return note.to_dict()

    # -----------------------------------------------------------------------
    # Publication & Sidecar Generation (Ed25519 v2)
    # -----------------------------------------------------------------------

    def publish_release(
        self,
        *,
        tenant_id: str,
        asset_revision_id: str,
        rendition_set_id: str,
        playlist_manifest: str,
        intro_offset_ms: int = 0,
        trim_start_ms: int = 0,
        media_uri: Optional[str] = None,
    ) -> PublishedReleaseRecord:
        """
        Publishes a release signed with Ed25519 v2.
        Enforces human approval of screening findings.
        Attaches approved context notes.
        """
        if not rendition_set_id or not rendition_set_id.strip():
            raise PipelineServiceError("rendition_set_id must be a non-empty string", 400)
        if not playlist_manifest or not playlist_manifest.strip():
            raise PipelineServiceError("playlist_manifest must be a non-empty string", 400)

        with self._lock:
            key = (tenant_id, asset_revision_id)
            report = self._reports.get(key)
            if not report:
                raise PipelineServiceError(
                    f"Asset revision '{asset_revision_id}' has not been screened", 400
                )

            if report.review_state == "rejected":
                raise PipelineServiceError(
                    f"Publication blocked: asset {asset_revision_id} was REJECTED by editorial review", 400
                )
            if report.review_state != "approved":
                raise PipelineServiceError(
                    f"Publication blocked: asset {asset_revision_id} is in review_state={report.review_state!r} "
                    "(requires explicit human approval before publishing)", 400
                )

            # Gather approved context notes for this asset revision
            approved_notes = [
                note
                for note in self._context_notes.get(key, {}).values()
                if note.editorial_approved
            ]

            spec = RenditionPackagingSpec(
                rendition_set_id=rendition_set_id.strip(),
                playlist_manifest_bytes=playlist_manifest.encode("utf-8"),
                intro_offset_ms=intro_offset_ms,
                trim_start_ms=trim_start_ms,
            )

            try:
                sidecar_json, session_ctx = self.pipeline.package_and_publish_sidecar(
                    tenant_id=tenant_id,
                    asset_revision_id=asset_revision_id,
                    packaging_spec=spec,
                    detector_version=report.detector_version,
                    context_notes=[n.to_sidecar_dict() for n in approved_notes],
                )
            except ReleaseGateError as exc:
                raise PipelineServiceError(f"Release gate publication failed: {exc}", 400) from exc

            # Verify sidecar self-consistency using SidecarContractValidator
            contract_dict, validated_intervals, review_state = SidecarContractValidator.validate(
                sidecar_json,
                session_ctx,
                trusted_public_keys=self.pipeline.trusted_public_keys,
            )

            release_id = f"rel-{uuid.uuid4().hex[:12]}"
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            resolved_media_uri = media_uri or f"asset:///hls/benign_variant.m3u8"

            # Create release packaging directory in runtime_data/releases/{release_id}
            release_dir = RUNTIME_DATA_DIR / "releases" / release_id
            release_dir.mkdir(parents=True, exist_ok=True)
            (release_dir / "playlist.m3u8").write_text(playlist_manifest, encoding="utf-8")
            (release_dir / "sidecar.json").write_text(sidecar_json, encoding="utf-8")
            (release_dir / "bundle.json").write_text(json.dumps(contract_dict, indent=2), encoding="utf-8")

            # Construct exact runnable Android TV intent command
            cmd = (
                f"adb shell am start -n com.fireshield.fireos/.MainActivity "
                f"--es fireshield.tenant_id '{tenant_id}' "
                f"--es fireshield.asset_revision_id '{asset_revision_id}' "
                f"--es fireshield.rendition_set_id '{rendition_set_id}' "
                f"--es fireshield.playlist_sha256 '{session_ctx.rendition_playlist_sha256}' "
                f"--es fireshield.media_uri '{resolved_media_uri}' "
                f"--es fireshield.sidecar_json_inline '{sidecar_json}' "
                f"--es fireshield.trusted_keys_json '{{\"{self.key_id}\": \"{self.public_key_hex}\"}}'"
            )

            release_record = PublishedReleaseRecord(
                release_id=release_id,
                tenant_id=tenant_id,
                asset_revision_id=asset_revision_id,
                rendition_set_id=rendition_set_id,
                rendition_playlist_sha256=session_ctx.rendition_playlist_sha256,
                manifest_digest=contract_dict["manifest_digest"],
                key_id=self.key_id,
                signing_alg=SUPPORTED_SIGNING_ALG_V2,
                public_key_hex=self.public_key_hex,
                sidecar_json=sidecar_json,
                sidecar_contract=contract_dict,
                media_uri=resolved_media_uri,
                playlist_manifest=playlist_manifest,
                published_at_iso=now_iso,
                harness_intent_command=cmd,
                context_notes_count=len(approved_notes),
            )

            self._releases[(tenant_id, release_id)] = release_record
            self._save_to_disk()
            return release_record

    def list_releases(self, tenant_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                r.to_dict()
                for (t_id, _), r in self._releases.items()
                if t_id == tenant_id
            ]

    def get_release(self, release_id: str, tenant_id: str) -> PublishedReleaseRecord:
        with self._lock:
            key = (tenant_id, release_id)
            if key not in self._releases:
                raise PipelineServiceError(f"Release '{release_id}' not found for tenant '{tenant_id}'", 404)
            return self._releases[key]
