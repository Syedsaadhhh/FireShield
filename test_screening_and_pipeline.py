"""
P0 (Async Gate), P1 (2D Spatial Detector), P2 (Multi-Tier AWS Readiness & Release Gate),
and P3 (Fire OS ExoPlayer Adapter) Automated Test Suite.
"""

import hashlib
import json
import pathlib
import subprocess
import pytest
import numpy as np

from screening_analyzer import (
    DETECTOR_VERSION,
    analyze_rgb_frame_sequence,
    analyze_video_file_headless,
    apply_human_editorial_decision,
    build_reference_fixtures,
    find_ffmpeg_executable,
    run_reference_benchmark,
)
from release_pipeline import (
     REQUIRED_NON_SECRET_CONFIG_VARS,
    AdSpliceSpec,
    AwsCloudReleaseGateDryRunPlanner,
    LocalReleaseGatePipeline,
    ReleaseGateError,
    RenditionPackagingSpec,
    check_aws_deployment_readiness,
    validate_step_functions_asl_file,
)
from firetv_player_adapter import (
    FIRE_OS_EXOPLAYER_VERIFICATION_CHECKLIST,
    AsyncInMemoryExoPlayerDouble,
    FireShieldExoPlayerAdapter,
    check_fireos_sdk_and_device_status,
)
from state_engine import (
    FireShieldRuntime,
    FireShieldState,
    PlaybackSessionContext,
    PlayerActionType,
)

PIPELINE_TEST_SEED = hashlib.sha256(b"fireshield-pipeline-test-ed25519-seed-2026").digest()
PIPELINE_KEY_ID = "pub-01-ed25519-test"


# ---------------------------------------------------------------------------
# P1 Tests — 2D Spatial-Frequency & Multi-Orientation Screening Benchmark
# ---------------------------------------------------------------------------

def test_p1_reference_benchmark_confusion_matrix_with_2d_checkerboard_and_noise():
    summary = run_reference_benchmark()
    assert summary["detector_version"] == DETECTOR_VERSION
    assert summary["ea_iris_status"]["local_binary_present"] is False

    cm = summary["confusion_matrix"]
    # 5 True Positives (luminance strobe, saturated red, vertical stripes, 2D checkerboard,
    # 45-deg diagonal grating) and 3 True Negatives (smooth ramp, 2Hz pulse, compression/sensor noise)
    assert cm == {"TP": 5, "TN": 3, "FP": 0, "FN": 0, "total": 8}

    by_id = {r["fixture_id"]: r for r in summary["results"]}

    # True Negatives (including compression & uncorrelated sensor noise)
    assert by_id["ref_neg_smooth_ramp"]["outcome"] == "TN"
    assert by_id["ref_neg_subthreshold_2hz_pulse"]["outcome"] == "TN"
    assert by_id["ref_neg_compression_and_sensor_noise"]["outcome"] == "TN"

    # True Positives with exact hazard type and bounded timing error (<= 2 frames = 67ms at 30fps)
    lum = by_id["ref_pos_7p5hz_luminance_strobe"]
    assert lum["outcome"] == "TP"
    assert "luminance_flash" in lum["detected_types"]
    assert lum["timing_error_ms"] is not None and lum["timing_error_ms"] <= 67

    red = by_id["ref_pos_7p5hz_saturated_red"]
    assert red["outcome"] == "TP"
    assert "saturated_red" in red["detected_types"]
    assert red["timing_error_ms"] is not None and red["timing_error_ms"] <= 67

    pat_vert = by_id["ref_pos_8pair_vertical_stripes"]
    assert pat_vert["outcome"] == "TP"
    assert "spatial_pattern" in pat_vert["detected_types"]
    assert pat_vert["timing_error_ms"] is not None and pat_vert["timing_error_ms"] <= 67

    pat_checker = by_id["ref_pos_2d_checkerboard"]
    assert pat_checker["outcome"] == "TP"
    assert "spatial_pattern" in pat_checker["detected_types"]
    assert pat_checker["timing_error_ms"] is not None and pat_checker["timing_error_ms"] <= 67

    pat_diag = by_id["ref_pos_diagonal_grating"]
    assert pat_diag["outcome"] == "TP"
    assert "spatial_pattern" in pat_diag["detected_types"]
    assert pat_diag["timing_error_ms"] is not None and pat_diag["timing_error_ms"] <= 67


def test_p1_screening_report_provenance_and_human_review_states():
    fixtures = build_reference_fixtures()
    strobe = fixtures[3]  # ref_pos_7p5hz_luminance_strobe
    raw_bytes = strobe["frames"].tobytes()
    expected_hash = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"

    report = analyze_rgb_frame_sequence(strobe["frames"], fps=30.0, raw_source_bytes=raw_bytes)
    assert report.asset_revision_id == expected_hash
    assert report.detector_version == DETECTOR_VERSION
    assert report.review_state == "pending_review"
    assert "ConfidenceScore" not in json.dumps(report.to_dict())

    with pytest.raises(ValueError, match="must be 'approved' or 'rejected'"):
        apply_human_editorial_decision(report, reviewer_id="rev-1", decision="auto_pass", notes="bad")

    apply_human_editorial_decision(
        report, reviewer_id="editor-01", decision="rejected", notes="Unmitigated 7.5Hz strobe"
    )
    assert report.review_state == "rejected"
    assert report.editorial_decision_record["decision"] == "rejected"


def test_p1_headless_ffmpeg_video_file_analysis(tmp_path: pathlib.Path):
    ffmpeg_bin = find_ffmpeg_executable()
    if not ffmpeg_bin:
        pytest.skip("ffmpeg binary not available on this host")

    fixtures = build_reference_fixtures(fps=30.0, width=64, height=64)
    strobe_frames = fixtures[3]["frames"]
    mp4_path = tmp_path / "synthetic_strobe_fixture.mp4"

    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        "64x64",
        "-r",
        "30",
        "-i",
        "pipe:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "10",
        str(mp4_path),
    ]
    subprocess.run(cmd, input=strobe_frames.tobytes(), check=True, timeout=30)

    report = analyze_video_file_headless(mp4_path, target_width=64, target_height=64, fps=30.0)
    expected_file_sha = f"sha256:{hashlib.sha256(mp4_path.read_bytes()).hexdigest()}"
    assert report.asset_revision_id == expected_file_sha
    assert report.review_state == "pending_review"
    assert len(report.findings) >= 1
    assert "luminance_flash" in report.findings[0].types


# ---------------------------------------------------------------------------
# P2 Tests — Multi-Tier AWS Readiness, Step Functions ASL, & Timeline Mapping
# ---------------------------------------------------------------------------

def test_p2_step_functions_asl_and_multi_tier_aws_readiness():
    asl_path = pathlib.Path(__file__).with_name("verification_pipeline.json")
    asl_summary = validate_step_functions_asl_file(asl_path)
    assert asl_summary["valid_asl"] is True
    assert asl_summary["static_structural_check_only"] is True
    assert asl_summary["aws_stepfunctions_api_validated"] is False
    assert asl_summary["uses_batch_compute"] is True
    assert asl_summary["has_mandatory_human_approval"] is True
    assert asl_summary["has_explicit_reject_terminal"] is True

    # 1. Empty environment: package_present=True (boto3 installed), config_set=False
    r_empty = check_aws_deployment_readiness(env={}, perform_live_checks=False)
    assert r_empty["package_present"] is True
    assert r_empty["config_set"] is False
    assert r_empty["authenticated"] is False
    assert r_empty["resources_verified"] is False
    assert r_empty["deployed"] is False
    assert r_empty["live_end_to_end_verified"] is False
    assert r_empty["aws_cloud_ready"] is False
    assert r_empty["deployment_status"] == "PACKAGE_PRESENT_CONFIG_AND_DEPLOYMENT_PENDING"

    # 2. Setting all non-secret config vars WITHOUT live authentication must NOT report READY!
    populated_env = {
        "FIRESHIELD_AWS_REGION": "us-east-1",
        "FIRESHIELD_S3_MEDIA_BUCKET": "fireshield-prod-media-01",
        "FIRESHIELD_S3_INPUT_PREFIX": "ingest/mezzanine/",
        "FIRESHIELD_S3_OUTPUT_PREFIX": "published/renditions/",
        "FIRESHIELD_STATE_MACHINE_ARN": "arn:aws:states:us-east-1:999888777666:stateMachine:FireShield-Gate",
        "FIRESHIELD_BATCH_JOB_QUEUE_ARN": "arn:aws:batch:us-east-1:999888777666:job-queue/FireShield-Queue",
        "FIRESHIELD_BATCH_JOB_DEFINITION_ARN": "arn:aws:batch:us-east-1:999888777666:job-definition/Worker:1",
        "FIRESHIELD_DYNAMODB_CONTRACTS_TABLE": "FireShield-AssetContracts",
        "FIRESHIELD_MEDIACONVERT_ROLE_ARN": "arn:aws:iam::999888777666:role/FireShield-MediaConvertRole",
        "FIRESHIELD_MEDIACONVERT_ENDPOINT_URL": "https://abcd1234.mediaconvert.us-east-1.amazonaws.com",
        "FIRESHIELD_SIGNING_KEY_ID": "pub-01-ed25519-2026-q3",
        "FIRESHIELD_CLOUDFRONT_BASE_URL": "https://d111111abcdef8.cloudfront.net",
    }
    r_config_only = check_aws_deployment_readiness(env=populated_env, perform_live_checks=False)
    assert r_config_only["config_set"] is True
    assert r_config_only["authenticated"] is False
    assert r_config_only["aws_cloud_ready"] is False
    assert r_config_only["deployment_status"] == "CONFIG_SET_DRY_RUN_ONLY_NOT_AUTHENTICATED_OR_DEPLOYED"

    # 3. Placeholder values from .env.example are rejected as unconfigured
    placeholder_env = dict(populated_env)
    placeholder_env["FIRESHIELD_S3_MEDIA_BUCKET"] = "<YOUR_FIRESHIELD_MEDIA_S3_BUCKET_NAME>"
    r_placeholder = check_aws_deployment_readiness(env=placeholder_env, perform_live_checks=False)
    assert r_placeholder["config_set"] is False
    assert "FIRESHIELD_S3_MEDIA_BUCKET" in r_placeholder["missing_or_placeholder_vars"]

    # 4. Dry-run planner builds exact request payloads without making AWS calls
    planner = AwsCloudReleaseGateDryRunPlanner(populated_env, dry_run=True)
    plan = planner.build_execution_plan(
        tenant_id="publisher-01",
        asset_revision_id="sha256:" + ("a" * 64),
        source_s3_key="ingest/mezzanine/clip01.mp4",
        rendition_set_id="hls-1080p-v2",
    )
    assert plan["execution_mode"] == "DRY_RUN_NO_AWS_CALLS_MADE"
    assert "ConditionExpression" in plan["dynamodb_idempotency_put_item"]


def test_p2_release_pipeline_idempotency_editorial_gate_and_timeline_mapping():
    pipeline = LocalReleaseGatePipeline(
        private_key_seed_32b=PIPELINE_TEST_SEED,
        key_id=PIPELINE_KEY_ID,
    )
    strobe = build_reference_fixtures()[3]  # Hazard at [1000ms, 2000ms]

    rec1, is_dup1 = pipeline.ingest_and_screen_frames(
        tenant_id="publisher-01",
        frames_rgb=strobe["frames"],
        fps=strobe["fps"],
    )
    assert is_dup1 is False
    assert rec1.screening_report.review_state == "pending_review"

    rec2, is_dup2 = pipeline.ingest_and_screen_frames(
        tenant_id="publisher-01",
        frames_rgb=strobe["frames"],
        fps=strobe["fps"],
    )
    assert is_dup2 is True
    assert rec2 is rec1

    base_spec = RenditionPackagingSpec(
        rendition_set_id="hls-1080p-v2",
        playlist_manifest_bytes=b"#EXTM3U\n#EXTINF:4.0,\nseg0.ts\n",
    )

    with pytest.raises(ReleaseGateError, match="requires human 'approved' decision"):
        pipeline.package_and_publish_sidecar(
            tenant_id="publisher-01",
            asset_revision_id=rec1.asset_revision_id,
            packaging_spec=base_spec,
        )

    pipeline.submit_editorial_decision(
        tenant_id="publisher-01",
        asset_revision_id=rec1.asset_revision_id,
        reviewer_id="editor-lead",
        decision="rejected",
        notes="Fails flash policy; hold from release.",
    )
    with pytest.raises(ReleaseGateError, match="explicitly REJECTED"):
        pipeline.package_and_publish_sidecar(
            tenant_id="publisher-01",
            asset_revision_id=rec1.asset_revision_id,
            packaging_spec=base_spec,
        )

    pipeline.submit_editorial_decision(
        tenant_id="publisher-01",
        asset_revision_id=rec1.asset_revision_id,
        reviewer_id="editor-lead",
        decision="approved",
        notes="Approved for release with signed v2 Ed25519 sidecar.",
    )

    unreviewed_ad_spec = RenditionPackagingSpec(
        rendition_set_id="hls-1080p-ad-unreviewed",
        playlist_manifest_bytes=b"#EXTM3U_AD1",
        ad_splices=(
            AdSpliceSpec(
                splice_id="ad-01",
                insert_at_source_ms=200,
                duration_ms=15000,
                ad_hazard_reviewed_and_approved=False,
            ),
        ),
    )
    with pytest.raises(ReleaseGateError, match="is unreviewed"):
        pipeline.package_and_publish_sidecar(
            tenant_id="publisher-01",
            asset_revision_id=rec1.asset_revision_id,
            packaging_spec=unreviewed_ad_spec,
        )

    colliding_ad_spec = RenditionPackagingSpec(
        rendition_set_id="hls-1080p-ad-collision",
        playlist_manifest_bytes=b"#EXTM3U_AD2",
        ad_splices=(
            AdSpliceSpec(
                splice_id="ad-mid-hazard",
                insert_at_source_ms=1500,
                duration_ms=15000,
                ad_hazard_reviewed_and_approved=True,
            ),
        ),
    )
    with pytest.raises(ReleaseGateError, match="collides with hazard interval"):
        pipeline.package_and_publish_sidecar(
            tenant_id="publisher-01",
            asset_revision_id=rec1.asset_revision_id,
            packaging_spec=colliding_ad_spec,
        )

    valid_shifted_spec = RenditionPackagingSpec(
        rendition_set_id="hls-1080p-shifted-v2",
        playlist_manifest_bytes=b"#EXTM3U_SHIFTED_V2",
        intro_offset_ms=5000,
        ad_splices=(
            AdSpliceSpec(
                splice_id="ad-preroll-verified",
                insert_at_source_ms=500,
                duration_ms=10000,
                ad_hazard_reviewed_and_approved=True,
            ),
        ),
        safe_resume_margin_ms=300,
    )
    sidecar_json, session_ctx = pipeline.package_and_publish_sidecar(
        tenant_id="publisher-01",
        asset_revision_id=rec1.asset_revision_id,
        packaging_spec=valid_shifted_spec,
    )
    parsed = json.loads(sidecar_json)
    assert parsed["schema_version"] == 2
    assert parsed["signing_alg"] == "Ed25519"
    assert parsed["key_id"] == PIPELINE_KEY_ID
    assert parsed["signature"].startswith("ed25519:")
    mapped_int = parsed["intervals"][0]
    orig_finding = rec1.screening_report.findings[0]
    assert mapped_int["start_ms"] == orig_finding.start_ms + 5000 + 10000
    assert mapped_int["end_ms"] == orig_finding.end_ms + 5000 + 10000
    assert mapped_int["safe_resume_ms"] == mapped_int["end_ms"] + 300


# ---------------------------------------------------------------------------
# P0 / P3 Tests — Asynchronous Player Gate & Adversarial Callback Faults
# ---------------------------------------------------------------------------

def _setup_adapter_fixture(
    *,
    reject_all_seeks: bool = False,
    seek_timeout_ms: int = 3000,
):
    pipeline = LocalReleaseGatePipeline(
        private_key_seed_32b=PIPELINE_TEST_SEED,
        key_id=PIPELINE_KEY_ID,
    )
    strobe = build_reference_fixtures()[3]  # Hazard around [1000ms, 2000ms]
    rec, _ = pipeline.ingest_and_screen_frames(
        tenant_id="publisher-01",
        frames_rgb=strobe["frames"],
        fps=strobe["fps"],
    )
    pipeline.submit_editorial_decision(
        tenant_id="publisher-01",
        asset_revision_id=rec.asset_revision_id,
        reviewer_id="editor-01",
        decision="approved",
        notes="Approved with 5000ms intro offset so hazard starts around 5967ms.",
    )
    spec_v1 = RenditionPackagingSpec(
        rendition_set_id="hls-firetv-1080p-v2",
        playlist_manifest_bytes=b"#EXTM3U_FIRETV_V2",
        intro_offset_ms=5000,
        safe_resume_margin_ms=400,
    )
    sidecar_v2, session_v2 = pipeline.package_and_publish_sidecar(
        tenant_id="publisher-01",
        asset_revision_id=rec.asset_revision_id,
        packaging_spec=spec_v1,
    )
    player = AsyncInMemoryExoPlayerDouble(
        position_ms=0, playing=True, reject_all_seeks=reject_all_seeks
    )
    runtime = FireShieldRuntime(
        session_v2,
        sidecar_v2,
        trusted_public_keys=pipeline.trusted_public_keys,
        pre_guard_ms=500,
    )
    adapter = FireShieldExoPlayerAdapter(player, runtime, seek_timeout_ms=seek_timeout_ms)
    return adapter, player, runtime, sidecar_v2, session_v2


def test_p0_p3_async_seek_gate_happy_path_and_in_flight_blanking():
    sdk_status = check_fireos_sdk_and_device_status()
    assert sdk_status["fire_tv_playback_claimed"] is False
    assert len(FIRE_OS_EXOPLAYER_VERIFICATION_CHECKLIST) == 3

    adapter, player, runtime, sidecar_v2, session_v2 = _setup_adapter_fixture()
    hazard_interval = runtime.intervals[0]
    guard_start_ms = hazard_interval.start_ms - 500

    # 1. Safe playback prior to guard
    cmd_safe = adapter.on_playback_tick(1000, now_monotonic_ms=1000)
    assert cmd_safe.action == PlayerActionType.CONTINUE
    assert player.playing is True
    assert player.shutter_blanked is False

    # 2. Pre-guard triggers PAUSE + shutter blanking before first hazard frame
    cmd_guard = adapter.on_playback_tick(guard_start_ms, now_monotonic_ms=5467)
    assert cmd_guard.state == FireShieldState.INTERVENING
    assert cmd_guard.action == PlayerActionType.PAUSE
    assert player.playing is False
    assert player.shutter_blanked is True

    # 3. Viewer confirms bypass seek -> initiates async seek, keeps video PAUSED and BLANKED!
    cmd_seek = adapter.on_viewer_confirm_bypass_seek(now_monotonic_ms=5500)
    assert cmd_seek.action == PlayerActionType.SEEK
    assert adapter.in_flight_seek is not None
    active_token = adapter.in_flight_seek.seek_token
    assert player.playing is False
    assert player.shutter_blanked is True
    # Player position has NOT moved yet because seek is asynchronous!
    assert player.position_ms == 0

    # 4. Intermediate playback tick while seek is in flight MUST stay paused & blanked
    cmd_in_flight_tick = adapter.on_playback_tick(guard_start_ms, now_monotonic_ms=5600)
    assert cmd_in_flight_tick.action == PlayerActionType.HOLD
    assert cmd_in_flight_tick.reason_code == "AWAITING_ASYNC_SEEK_COMPLETION_HOLD"
    assert player.playing is False
    assert player.shutter_blanked is True

    # 5. Decoder completes seek to safe_resume_ms and fires onPositionDiscontinuity callback
    token, landed_ms = player.complete_latest_seek(landing_offset_ms=0)
    assert token == active_token
    assert landed_ms == hazard_interval.safe_resume_ms

    cmd_done = adapter.on_seek_completed_callback(
        seek_token=token,
        actual_position_ms=landed_ms,
        now_monotonic_ms=5750,
        event_id="evt-seek-001",
    )
    assert cmd_done.state == FireShieldState.RESOLVED
    assert cmd_done.action == PlayerActionType.CONTINUE
    assert player.shutter_blanked is False
    assert player.playing is True


def test_p0_async_seek_adversarial_failures_all_pause_and_blank():
    # Case A: Seek rejected by player
    adapter_rej, player_rej, runtime_rej, _, _ = _setup_adapter_fixture(reject_all_seeks=True)
    guard_ms = runtime_rej.intervals[0].start_ms - 500
    adapter_rej.on_playback_tick(guard_ms, now_monotonic_ms=100)
    cmd_rej = adapter_rej.on_viewer_confirm_bypass_seek(now_monotonic_ms=110)
    assert cmd_rej.action == PlayerActionType.ERROR
    assert runtime_rej.state == FireShieldState.ERROR
    assert player_rej.playing is False
    assert player_rej.shutter_blanked is True

    # Case B: In-flight seek timeout on tick
    adapter_to, player_to, runtime_to, _, _ = _setup_adapter_fixture(seek_timeout_ms=1000)
    adapter_to.on_playback_tick(guard_ms, now_monotonic_ms=100)
    adapter_to.on_viewer_confirm_bypass_seek(now_monotonic_ms=200)
    cmd_timeout = adapter_to.on_playback_tick(guard_ms, now_monotonic_ms=1500)
    assert cmd_timeout.action == PlayerActionType.ERROR
    assert runtime_to.state == FireShieldState.ERROR
    assert player_to.playing is False
    assert player_to.shutter_blanked is True

    # Case C: Stale seek token mismatch on callback
    adapter_stale, player_stale, runtime_stale, _, _ = _setup_adapter_fixture()
    adapter_stale.on_playback_tick(guard_ms, now_monotonic_ms=100)
    adapter_stale.on_viewer_confirm_bypass_seek(now_monotonic_ms=200)
    cmd_stale = adapter_stale.on_seek_completed_callback(
        seek_token=999,
        actual_position_ms=runtime_stale.intervals[0].safe_resume_ms,
        now_monotonic_ms=300,
    )
    assert cmd_stale.action == PlayerActionType.ERROR
    assert runtime_stale.state == FireShieldState.ERROR
    assert player_stale.playing is False
    assert player_stale.shutter_blanked is True

    # Case D: Backward / short keyframe landing (< safe_resume_ms)
    adapter_short, player_short, runtime_short, _, _ = _setup_adapter_fixture()
    adapter_short.on_playback_tick(guard_ms, now_monotonic_ms=100)
    adapter_short.on_viewer_confirm_bypass_seek(now_monotonic_ms=200)
    tok, short_ms = player_short.complete_latest_seek(landing_offset_ms=-500)
    cmd_short = adapter_short.on_seek_completed_callback(
        seek_token=tok,
        actual_position_ms=short_ms,
        now_monotonic_ms=300,
    )
    assert cmd_short.action == PlayerActionType.ERROR
    assert runtime_short.state == FireShieldState.ERROR
    assert player_short.playing is False
    assert player_short.shutter_blanked is True

    # Case E: Rendition change mid-seek invalidates seek and fails closed
    adapter_mid, player_mid, runtime_mid, sidecar_v2, session_v2 = _setup_adapter_fixture()
    adapter_mid.on_playback_tick(guard_ms, now_monotonic_ms=100)
    adapter_mid.on_viewer_confirm_bypass_seek(now_monotonic_ms=200)
    cmd_mid = adapter_mid.on_rendition_or_playlist_changed(session_v2, sidecar_v2, now_monotonic_ms=250)
    assert cmd_mid.action in (PlayerActionType.PAUSE, PlayerActionType.ERROR)
    assert player_mid.playing is False
    assert player_mid.shutter_blanked is True

    # Case F: Replayed callback event_id rejected
    adapter_rep, player_rep, runtime_rep, _, _ = _setup_adapter_fixture()
    adapter_rep.on_playback_tick(guard_ms, now_monotonic_ms=100)
    adapter_rep.on_viewer_confirm_bypass_seek(now_monotonic_ms=200)
    tok_rep, ok_ms = player_rep.complete_latest_seek(landing_offset_ms=0)
    cmd_first = adapter_rep.on_seek_completed_callback(
        seek_token=tok_rep,
        actual_position_ms=ok_ms,
        now_monotonic_ms=300,
        event_id="evt-unique-1",
    )
    assert cmd_first.action == PlayerActionType.CONTINUE
    # Replay the exact same callback event_id -> must immediately fail closed, pause, and blank!
    cmd_replay = adapter_rep.on_seek_completed_callback(
        seek_token=tok_rep,
        actual_position_ms=ok_ms,
        now_monotonic_ms=310,
        event_id="evt-unique-1",
    )
    assert cmd_replay.action == PlayerActionType.ERROR
    assert runtime_rep.state == FireShieldState.ERROR
    assert player_rep.playing is False
    assert player_rep.shutter_blanked is True


def test_p2_b2b_key_rotation_revocation_and_multitenant_audit_isolation():
    seed_q3 = hashlib.sha256(b"fireshield-b2b-key-q3-2026").digest()
    seed_q4 = hashlib.sha256(b"fireshield-b2b-key-q4-2026").digest()
    pipeline = LocalReleaseGatePipeline(
        private_key_seed_32b=seed_q3,
        key_id="pub-key-2026-q3",
    )
    strobe = build_reference_fixtures()[3]
    ramp = build_reference_fixtures()[0]

    # Tenant A ingests, approves, and publishes under Q3 key
    rec_a, _ = pipeline.ingest_and_screen_frames(
        tenant_id="tenant-studio-alpha",
        frames_rgb=strobe["frames"],
        fps=strobe["fps"],
    )
    pipeline.submit_editorial_decision(
        tenant_id="tenant-studio-alpha",
        asset_revision_id=rec_a.asset_revision_id,
        reviewer_id="lead-editor-alpha",
        decision="approved",
        notes="Approved Q3 release for Studio Alpha.",
    )
    spec_a = RenditionPackagingSpec(
        rendition_set_id="hls-alpha-1080p",
        playlist_manifest_bytes=b"#EXTM3U\n#EXTINF:4.0,\nalpha0.ts\n",
    )
    sidecar_a_q3, ctx_a = pipeline.package_and_publish_sidecar(
        tenant_id="tenant-studio-alpha",
        asset_revision_id=rec_a.asset_revision_id,
        packaging_spec=spec_a,
    )

    # Rotate active signing key to Q4
    pipeline.rotate_signing_key(
        new_key_id="pub-key-2026-q4",
        new_private_key_seed_32b=seed_q4,
        actor_id="kms-rotation-bot",
    )

    # Tenant B ingests, approves, and publishes under Q4 key
    rec_b, _ = pipeline.ingest_and_screen_frames(
        tenant_id="tenant-network-beta",
        frames_rgb=ramp["frames"],
        fps=ramp["fps"],
    )
    pipeline.submit_editorial_decision(
        tenant_id="tenant-network-beta",
        asset_revision_id=rec_b.asset_revision_id,
        reviewer_id="lead-editor-beta",
        decision="approved",
        notes="Approved zero-finding Q4 release for Network Beta.",
    )
    spec_b = RenditionPackagingSpec(
        rendition_set_id="hls-beta-1080p",
        playlist_manifest_bytes=b"#EXTM3U\n#EXTINF:4.0,\nbeta0.ts\n",
    )
    sidecar_b_q4, ctx_b = pipeline.package_and_publish_sidecar(
        tenant_id="tenant-network-beta",
        asset_revision_id=rec_b.asset_revision_id,
        packaging_spec=spec_b,
    )

    # Both Q3 and Q4 sidecars verify against the rotated key ring
    rt_a = FireShieldRuntime(
        ctx_a,
        sidecar_a_q3,
        trusted_public_keys=pipeline.trusted_public_keys,
        revoked_key_ids=pipeline.revoked_key_ids,
    )
    assert rt_a.state == FireShieldState.ARMED
    assert rt_a.verified is True

    rt_b = FireShieldRuntime(
        ctx_b,
        sidecar_b_q4,
        trusted_public_keys=pipeline.trusted_public_keys,
        revoked_key_ids=pipeline.revoked_key_ids,
    )
    assert rt_b.state == FireShieldState.ARMED
    assert rt_b.verified is True

    # Revoking Q3 key invalidates Tenant A's Q3 sidecar on live key-ring update while Tenant B's Q4 stays ARMED
    pipeline.revoke_signing_key(key_id="pub-key-2026-q3", reason="retired_after_q4_cutover")
    cmd_after_revoke_a = rt_a.update_key_ring(
        pipeline.trusted_public_keys,
        revoked_key_ids=pipeline.revoked_key_ids,
    )
    assert rt_a.state == FireShieldState.ERROR
    assert cmd_after_revoke_a.action == PlayerActionType.ERROR
    assert cmd_after_revoke_a.requires_surface_blanking is True

    cmd_after_revoke_b = rt_b.update_key_ring(
        pipeline.trusted_public_keys,
        revoked_key_ids=pipeline.revoked_key_ids,
    )
    assert rt_b.state == FireShieldState.ARMED
    assert cmd_after_revoke_b.action == PlayerActionType.CONTINUE

    # Multi-tenant audit isolation: Tenant A never sees Tenant B's records or audit events
    alpha_records = pipeline.get_tenant_records("tenant-studio-alpha")
    beta_records = pipeline.get_tenant_records("tenant-network-beta")
    assert len(alpha_records) == 1 and alpha_records[0].tenant_id == "tenant-studio-alpha"
    assert len(beta_records) == 1 and beta_records[0].tenant_id == "tenant-network-beta"

    alpha_audit = pipeline.get_tenant_audit_log("tenant-studio-alpha")
    assert all(e["tenant_id"] in ("tenant-studio-alpha", "*") for e in alpha_audit)
    assert any(e["event_type"] == "SIDECAR_PUBLISHED" for e in alpha_audit)

