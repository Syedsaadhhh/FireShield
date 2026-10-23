#!/usr/bin/env python3
"""
Regression tests reproducing the 9 Run 6 integration defects identified in coordinator review.
Must be run before and after applying Phase A corrections.
"""

import json
import pathlib
import pytest
import time
from http import HTTPStatus

from pipeline_service import (
    LocalPipelineService,
    PipelineServiceError,
)

TENANT_ID = "publisher-demo-tenant"


def test_invalid_media_decode_fails_closed(tmp_path):
    """
    Defect 1: Invalid media bytes or decode failure must result in FAILED status,
    NOT silent substitution of benign reference frames resulting in false COMPLETED / 0 findings.
    """
    store_file = tmp_path / "test_store_invalid_media.json"
    svc = LocalPipelineService(store_path=store_file)

    asset = svc.import_managed_asset(
        tenant_id=TENANT_ID,
        name="Corrupted Video Test",
        source_type="managed_video",
        raw_bytes=b"not-video-decode-must-fail-corrupted-header-data",
    )
    job = svc.start_screening_job(tenant_id=TENANT_ID, asset_id=asset.asset_id)

    # Job must report FAILED status, never a false clean COMPLETED
    assert job.status == "FAILED"
    assert job.findings_count == 0
    assert job.error_message is not None
    assert "decode" in job.error_message.lower() or "ffmpeg" in job.error_message.lower() or "unsupported" in job.error_message.lower()

    # Attempting to retrieve a report for a failed job must fail closed
    with pytest.raises(PipelineServiceError) as exc:
        svc.get_screening_report(asset.asset_revision_id, TENANT_ID)
    assert exc.value.status_code == 404


def test_asset_revision_id_parity(tmp_path):
    """
    Defect 2: Catalog/job revision must match report.asset_revision_id bit-for-bit.
    Detector output must be bound directly to the bytes analyzed, not a double-hashed string.
    """
    store_file = tmp_path / "test_store_rev_parity.json"
    svc = LocalPipelineService(store_path=store_file)

    job = svc.start_screening_job(tenant_id=TENANT_ID, asset_id="ast-hazard-luminance-strobe")
    assert job.status == "COMPLETED"

    report = svc.get_screening_report(job.asset_revision_id, TENANT_ID)
    assert job.asset_revision_id == report["asset_revision_id"], (
        f"Mismatch between job/catalog revision ({job.asset_revision_id}) and report revision ({report['asset_revision_id']})"
    )


def test_store_survives_restart(tmp_path):
    """
    Defect 3: Local store must rehydrate reports, reviews, context notes,
    and release-pipeline state across service restarts.
    """
    store_file = tmp_path / "test_store_restart.json"
    svc1 = LocalPipelineService(store_path=store_file)

    # 1. Screen strobe asset
    job = svc1.start_screening_job(tenant_id=TENANT_ID, asset_id="ast-hazard-luminance-strobe")
    assert job.status == "COMPLETED"
    rev_id = job.asset_revision_id

    # 2. Ground and approve context note
    rep1 = svc1.get_screening_report(rev_id, TENANT_ID)
    first_int_id = rep1["findings"][0]["interval_id"]
    svc1.ground_context_for_interval(
        tenant_id=TENANT_ID,
        asset_revision_id=rev_id,
        interval_id=first_int_id,
        method="extractive_subtitles",
    )
    svc1.approve_context_note(
        tenant_id=TENANT_ID,
        asset_revision_id=rev_id,
        interval_id=first_int_id,
        reviewer_id="editor-persist",
    )

    # 3. Submit human editorial approval
    svc1.submit_editorial_decision(
        tenant_id=TENANT_ID,
        asset_revision_id=rev_id,
        reviewer_id="editor-persist",
        decision="approved",
        notes="Verified persistent approval",
    )

    # 4. Restart service from the exact same store file
    svc2 = LocalPipelineService(store_path=store_file)

    # Report must exist and retain review state and context notes
    rep2 = svc2.get_screening_report(rev_id, TENANT_ID)
    assert rep2["review_state"] == "approved"
    assert len(rep2.get("context_notes", [])) >= 1
    assert rep2["context_notes"][0]["editorial_approved"] is True

    # Publication must succeed immediately on svc2 without re-screening
    release = svc2.publish_release(
        tenant_id=TENANT_ID,
        asset_revision_id=rev_id,
        rendition_set_id="hls-persisted-demo",
        playlist_manifest="#EXTM3U\n#EXT-X-VERSION:3\nseg1.ts\n",
    )
    assert release.release_id.startswith("rel-")
    assert release.context_notes_count >= 1


def test_no_subtitles_does_not_fabricate_context(tmp_path):
    """
    Defect 4: Assets lacking subtitles must report context unavailable,
    NEVER synthesize invented text ("system overload", "power bridge", etc.).
    """
    store_file = tmp_path / "test_store_no_subs.json"
    svc = LocalPipelineService(store_path=store_file)

    # Screen strobe asset
    job = svc.start_screening_job(tenant_id=TENANT_ID, asset_id="ast-hazard-luminance-strobe")
    rev_id = job.asset_revision_id
    rep = svc.get_screening_report(rev_id, TENANT_ID)
    first_int_id = rep["findings"][0]["interval_id"]

    # Clear subtitles
    ast = svc.get_asset("ast-hazard-luminance-strobe", TENANT_ID)
    ast.subtitles_vtt = ""

    # Grounding without cues must fail closed (raise 404 or return error), NEVER invent fiction
    with pytest.raises(PipelineServiceError) as exc:
        svc.ground_context_for_interval(
            tenant_id=TENANT_ID,
            asset_revision_id=rev_id,
            interval_id=first_int_id,
            method="extractive_subtitles",
        )
    assert exc.value.status_code == 404
    assert "no subtitle" in str(exc.value).lower() or "unavailable" in str(exc.value).lower()


def test_safe_path_traversal_and_confinement(tmp_path):
    """
    Defect 8: Path handling must reject drive letters, UNC paths, absolute paths,
    and directory escapes beyond managed storage.
    """
    store_file = tmp_path / "test_store_paths.json"
    svc = LocalPipelineService(store_path=store_file)

    invalid_paths = [
        "C:\\Windows\\System32\\cmd.exe",
        "D:\\movies\\strobe.mp4",
        "\\\\192.168.1.100\\share\\video.mp4",
        "/etc/shadow",
        "data/catalog/../../../../windows/system.ini",
    ]

    for bad_path in invalid_paths:
        with pytest.raises(PipelineServiceError) as exc:
            svc.import_managed_asset(
                tenant_id=TENANT_ID,
                name="Attack Test",
                source_path=bad_path,
                raw_bytes=b"dummy",
            )
        assert exc.value.status_code == 400, f"Expected 400 for path: {bad_path}"
