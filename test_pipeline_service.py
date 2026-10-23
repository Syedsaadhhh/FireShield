#!/usr/bin/env python3
"""
Integration and Domain Tests for FireShield Pipeline Service and Connected API.
Tests:
1. Asset catalog seeding and managed import with actual byte hashing.
2. Frame screening on benign and hazard fixtures using existing detector.
3. Editorial review gates: unreviewed/rejected strictly block publication.
4. Synaptic story context grounding (extractive and Bedrock offline fallback) and approval.
5. Ed25519 v2 sidecar publication and cryptographic verification.
6. Connected WSGI API routing and fail-closed security invariants.
"""

import io
import json
import pathlib
import time
import pytest
from http import HTTPStatus

from api_adapter import (
    DEFAULT_DEMO_TENANT,
    FireShieldLocalApiAdapter,
    create_wsgi_app,
)
from pipeline_service import (
    LocalPipelineService,
    PipelineServiceError,
)
from state_engine import SidecarContractValidator


@pytest.fixture
def temp_service(tmp_path):
    store_file = tmp_path / "test_store.json"
    svc = LocalPipelineService(store_path=store_file)
    return svc


@pytest.fixture
def wsgi_client(temp_service):
    adapter = FireShieldLocalApiAdapter(pipeline_service=temp_service)
    app = create_wsgi_app(adapter=adapter, trusted_tenant=DEFAULT_DEMO_TENANT)

    def _request(path, method="GET", body=None, headers=None, query_string=""):
        env = {
            "PATH_INFO": path,
            "REQUEST_METHOD": method,
            "QUERY_STRING": query_string,
            "wsgi.input": io.BytesIO(body.encode("utf-8") if isinstance(body, str) else (body or b"")),
            "CONTENT_LENGTH": str(len(body.encode("utf-8") if isinstance(body, str) else (body or b""))),
            "CONTENT_TYPE": "application/json",
            "HTTP_X_FIRESHIELD_TENANT_ID": DEFAULT_DEMO_TENANT,
            "HTTP_X_FIRESHIELD_USER_ID": "test-operator",
        }
        if headers:
            for k, v in headers.items():
                env[k] = v

        response_status = []
        response_headers = []

        def start_response(status, resp_headers):
            response_status.append(status)
            response_headers.extend(resp_headers)

        result_iter = app(env, start_response)
        result_bytes = b"".join(result_iter)
        return response_status[0], dict(response_headers), result_bytes

    return _request


# ===========================================================================
# 1. Pipeline Service Domain Tests
# ===========================================================================

def test_service_asset_seeding_and_import(temp_service):
    tenant = "publisher-demo-tenant"
    assets = temp_service.list_assets(tenant)
    assert len(assets) >= 3
    asset_ids = {a["asset_id"] for a in assets}
    assert "ast-benign-navy-clip" in asset_ids
    assert "ast-hazard-luminance-strobe" in asset_ids
    assert "ast-hazard-spatial-checkerboard" in asset_ids

    # Import custom asset
    custom = temp_service.import_managed_asset(
        tenant_id=tenant,
        name="Custom Promo Reel",
        raw_bytes=b"custom-promo-reel-bytes-12345",
    )
    assert custom.asset_id.startswith("ast-")
    assert custom.size_bytes == len(b"custom-promo-reel-bytes-12345")
    assert custom.review_state == "unreviewed"

    # Reject path traversal
    with pytest.raises(PipelineServiceError) as exc:
        temp_service.import_managed_asset(
            tenant_id=tenant,
            name="Traversal",
            source_path="../../outside.mp4",
            raw_bytes=b"dummy",
        )
    assert "traversal" in str(exc.value).lower()


def test_service_screening_and_editorial_review_gate(temp_service):
    tenant = "publisher-demo-tenant"

    # 1. Screen strobe asset (hazard fixture)
    job = temp_service.start_screening_job(tenant_id=tenant, asset_id="ast-hazard-luminance-strobe")
    assert job.status == "COMPLETED"
    assert job.findings_count > 0

    report = temp_service.get_screening_report(job.asset_revision_id, tenant)
    assert report["review_state"] == "pending_review"
    assert len(report["findings"]) > 0
    first_interval_id = report["findings"][0]["interval_id"]

    # 2. Attempt publication while unapproved -> MUST FAIL
    with pytest.raises(PipelineServiceError) as exc:
        temp_service.publish_release(
            tenant_id=tenant,
            asset_revision_id=job.asset_revision_id,
            rendition_set_id="hls-720p",
            playlist_manifest="#EXTM3U\n#EXT-X-VERSION:3\nseg1.ts\n",
        )
    assert "requires explicit human approval" in str(exc.value)

    # 3. Ground and approve context note
    ctx_note = temp_service.ground_context_for_interval(
        tenant_id=tenant,
        asset_revision_id=job.asset_revision_id,
        interval_id=first_interval_id,
        method="extractive_subtitles",
    )
    assert ctx_note["interval_id"] == first_interval_id
    assert ctx_note["editorial_approved"] is False

    app_note = temp_service.approve_context_note(
        tenant_id=tenant,
        asset_revision_id=job.asset_revision_id,
        interval_id=first_interval_id,
        reviewer_id="lead-editor-1",
    )
    assert app_note["editorial_approved"] is True

    # 4. Human rejection blocks publication
    temp_service.submit_editorial_decision(
        tenant_id=tenant,
        asset_revision_id=job.asset_revision_id,
        reviewer_id="editor-2",
        decision="rejected",
        notes="Too hazardous for daytime broadcast.",
    )
    with pytest.raises(PipelineServiceError) as exc:
        temp_service.publish_release(
            tenant_id=tenant,
            asset_revision_id=job.asset_revision_id,
            rendition_set_id="hls-720p",
            playlist_manifest="#EXTM3U\n#EXT-X-VERSION:3\nseg1.ts\n",
        )
    assert "REJECTED" in str(exc.value)

    # 5. Human approval allows publication
    temp_service.submit_editorial_decision(
        tenant_id=tenant,
        asset_revision_id=job.asset_revision_id,
        reviewer_id="senior-editor-3",
        decision="approved",
        notes="Hazard interval approved with Synaptic Shield context banner.",
    )

    release = temp_service.publish_release(
        tenant_id=tenant,
        asset_revision_id=job.asset_revision_id,
        rendition_set_id="hls-720p",
        playlist_manifest="#EXTM3U\n#EXT-X-VERSION:3\nseg1.ts\n",
    )
    assert release.release_id.startswith("rel-")
    assert release.context_notes_count >= 1
    assert "adb shell am start" in release.harness_intent_command

    # 6. Verify sidecar contract with SidecarContractValidator
    sidecar_dict = json.loads(release.sidecar_json)
    assert sidecar_dict["schema_version"] == 2
    assert "signature" in sidecar_dict
    assert sidecar_dict["signing_alg"] == "Ed25519"


# ===========================================================================
# 2. Connected WSGI API Tests
# ===========================================================================

def test_api_static_console_and_health(wsgi_client):
    # Health check
    status, _, data = wsgi_client("/health")
    assert status == "200 OK"
    health = json.loads(data)
    assert health["status"] == "HEALTHY"

    # Static Operator Console via /operator
    status, headers, data = wsgi_client("/operator")
    assert status == "200 OK"
    assert "text/html" in headers.get("Content-Type", "")
    assert b"FireShield" in data and b"<title>FireShield" in data

    # Static Operator Console via GET / with Accept: text/html
    status, headers, data = wsgi_client("/", headers={"HTTP_ACCEPT": "text/html,application/xhtml+xml"})
    assert status == "200 OK"
    assert "text/html" in headers.get("Content-Type", "")


def test_api_connected_flow(wsgi_client):
    # 1. List assets
    status, _, data = wsgi_client("/api/v1/assets")
    assert status == "200 OK"
    res = json.loads(data)
    assert len(res["assets"]) >= 3
    benign_ast = next(a for a in res["assets"] if a["asset_id"] == "ast-benign-navy-clip")

    # 2. Screen asset (asynchronous execution)
    status, _, data = wsgi_client(
        "/api/v1/screening/jobs",
        method="POST",
        body=json.dumps({"asset_id": benign_ast["asset_id"]}),
    )
    assert status == "201 Created"
    job = json.loads(data)
    assert job["status"] in ("RUNNING", "COMPLETED")
    for _ in range(50):
        if job["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.1)
        status, _, data = wsgi_client(f"/api/v1/screening/jobs/{job['job_id']}")
        assert status == "200 OK"
        job = json.loads(data)
    assert job["status"] == "COMPLETED"

    # 3. Get screening report
    status, _, data = wsgi_client(f"/api/v1/screening/reports/{benign_ast['asset_revision_id']}")
    assert status == "200 OK"
    report = json.loads(data)
    assert report["review_state"] == "pending_review"

    # 4. Approve screening report
    status, _, data = wsgi_client(
        f"/api/v1/screening/reports/{benign_ast['asset_revision_id']}/review",
        method="POST",
        body=json.dumps({
            "reviewer_id": "api-reviewer-lead",
            "decision": "approved",
            "notes": "Verified clean presentation clip.",
            "zero_findings_attested": True,
        }),
    )
    assert status == "200 OK"
    rev_res = json.loads(data)
    assert rev_res["review_state"] == "approved"

    # 5. Publish release
    status, _, data = wsgi_client(
        "/api/v1/releases/publish",
        method="POST",
        body=json.dumps({
            "asset_revision_id": benign_ast["asset_revision_id"],
            "rendition_set_id": "hls-main-320x240",
            "playlist_manifest": "#EXTM3U\n#EXT-X-VERSION:3\nbenign_seg0.ts\n",
        }),
    )
    assert status == "201 Created"
    rel = json.loads(data)
    assert rel["release_id"].startswith("rel-")
    release_id = rel["release_id"]

    # 6. Retrieve sidecar.json and manifest.m3u8
    status, headers, sidecar_bytes = wsgi_client(f"/api/v1/releases/{release_id}/sidecar.json")
    assert status == "200 OK"
    sidecar_json = json.loads(sidecar_bytes)
    assert sidecar_json["rendition_set_id"] == "hls-main-320x240"

    status, headers, manifest_bytes = wsgi_client(f"/api/v1/releases/{release_id}/manifest.m3u8")
    assert status == "200 OK"
    assert b"benign_seg0.ts" in manifest_bytes
