#!/usr/bin/env python3
"""
Unit and Integration Tests for FireShield Local API & Data Adapter.
Portable fixtures referenced relative to tests/fixtures.
Covers honest identity boundaries, fail-closed CORS, bounded HTTP/schema validation,
and legacy v1 digest compatibility.
"""

import io
import json
import pathlib
import pytest

from api_adapter import (
    ApiConflictError,
    ApiError,
    ApiForbiddenError,
    ApiNotFoundError,
    AuthContext,
    FireShieldLocalApiAdapter,
    create_wsgi_app,
)
from continuity_engine import compute_receipt_digest, validate_and_parse_incident_receipt

FIXTURES_DIR = pathlib.Path(__file__).parent / "tests" / "fixtures"


@pytest.fixture
def api():
    return FireShieldLocalApiAdapter()


@pytest.fixture
def sample_receipt_str():
    receipt_path = FIXTURES_DIR / "drop_subtitles_receipt.json"
    assert receipt_path.exists(), f"drop_subtitles_receipt.json must exist at {receipt_path}"
    return receipt_path.read_text(encoding="utf-8")


@pytest.fixture
def audio_receipt_str():
    receipt_path = FIXTURES_DIR / "drop_audio_receipt.json"
    assert receipt_path.exists(), f"drop_audio_receipt.json must exist at {receipt_path}"
    return receipt_path.read_text(encoding="utf-8")


def test_health_check(api):
    health = api.get_health()
    assert health["status"] == "HEALTHY"
    assert health["environment_mode"] == "local_in_memory_adapter"
    assert health["auth_mode"] == "local_demo_unauthenticated"
    assert "127.0.0.1:8000" in health["bind_address"]
    assert health["storage"]["total_incidents"] == 0
    assert health["cloud_readiness"]["live_spend_authorized"] is False


def test_ingest_and_idempotency(api, sample_receipt_str):
    auth_a = AuthContext(tenant_id="tenant-alpha")

    # Initial ingestion
    res1 = api.ingest_incident_receipt(sample_receipt_str, auth_a)
    assert res1["status"] == "INGESTED"
    assert res1["tenant_id"] == "tenant-alpha"
    incident_id = res1["incident_id"]

    # Duplicate ingestion: exact same payload must be idempotent
    res2 = api.ingest_incident_receipt(sample_receipt_str, auth_a)
    assert res2["status"] == "IDEMPOTENT_DUPLICATE_ACCEPTED"
    assert res2["incident_id"] == incident_id
    assert res2["receipt_digest"] == res1["receipt_digest"]

    # Verify storage count
    health = api.get_health()
    assert health["storage"]["total_incidents"] == 1


def test_ingest_conflicting_duplicate_rejected(api, sample_receipt_str):
    auth_a = AuthContext(tenant_id="tenant-alpha")
    api.ingest_incident_receipt(sample_receipt_str, auth_a)

    # Modify receipt content and recompute digest under the same incident_id
    data = json.loads(sample_receipt_str)
    data["position_delta_ms"] = 999
    data["receipt_digest"] = compute_receipt_digest(data)

    with pytest.raises(ApiConflictError) as exc:
        api.ingest_incident_receipt(json.dumps(data), auth_a)
    assert "already exists with differing digest" in str(exc.value)


def test_cross_tenant_isolation(api, sample_receipt_str, audio_receipt_str):
    auth_alpha = AuthContext(tenant_id="tenant-alpha")
    auth_beta = AuthContext(tenant_id="tenant-beta")

    # Ingest incident 1 under Alpha, incident 2 under Beta
    res_alpha = api.ingest_incident_receipt(sample_receipt_str, auth_alpha)
    res_beta = api.ingest_incident_receipt(audio_receipt_str, auth_beta)

    inc_id_alpha = res_alpha["incident_id"]
    inc_id_beta = res_beta["incident_id"]

    # List incidents for Alpha
    list_alpha = api.list_incidents(auth_alpha)
    assert list_alpha["total_count"] == 1
    assert list_alpha["incidents"][0]["incident_id"] == inc_id_alpha

    # List incidents for Beta
    list_beta = api.list_incidents(auth_beta)
    assert list_beta["total_count"] == 1
    assert list_beta["incidents"][0]["incident_id"] == inc_id_beta

    # Cross-tenant get must fail with 404
    with pytest.raises(ApiNotFoundError):
        api.get_incident(inc_id_beta, auth_alpha)

    with pytest.raises(ApiNotFoundError):
        api.get_incident(inc_id_alpha, auth_beta)

    # Cross-tenant collision attempt: Beta tries to ingest an incident with Alpha's incident_id
    with pytest.raises(ApiConflictError):
        api.ingest_incident_receipt(sample_receipt_str, auth_beta)


def test_bounded_retest_execution(api, sample_receipt_str):
    auth = AuthContext(tenant_id="tenant-qa")
    res = api.ingest_incident_receipt(sample_receipt_str, auth)
    inc_id = res["incident_id"]

    retest_out = api.run_retest(inc_id, auth)
    assert retest_out["incident_id"] == inc_id
    execution = retest_out["retest_execution"]
    assert execution["test_status"] in ("PASS", "DETECTION_PASS")
    assert execution["fixture_id"] == "DROPPED_CAPTION_FAULT_V1"

    # Verify retest history recorded
    detail = api.get_incident(inc_id, auth)
    assert len(detail["retest_history"]) == 1
    assert detail["retest_history"][0]["test_status"] == execution["test_status"]


def test_advisory_explanation(api, sample_receipt_str):
    auth = AuthContext(tenant_id="tenant-qa")
    res = api.ingest_incident_receipt(sample_receipt_str, auth)
    inc_id = res["incident_id"]

    advisory = api.get_advisory_explanation(inc_id, auth)
    assert advisory["incident_id"] == inc_id
    assert advisory["advisory_status"] == "DETERMINISTIC_OFFLINE_ANALYSIS"
    assert advisory["model_invoked"] is False
    assert advisory["model_id"] is None
    assert len(advisory["evidence_facts"]) >= 3
    assert len(advisory["labeled_hypotheses"]) >= 1
    assert "DROPPED_CAPTION_FAULT_V1" in advisory["permitted_next_check"]


def test_wsgi_endpoints(sample_receipt_str):
    app = create_wsgi_app(trusted_tenant="ws-tenant")

    def make_request(method, path, body=b"", headers=None, query=""):
        headers = headers or {}
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
        }
        for k, v in headers.items():
            if k.lower() == "content-type":
                environ["CONTENT_TYPE"] = v
            else:
                env_key = "HTTP_" + k.upper().replace("-", "_")
                environ[env_key] = v

        response_status = []
        response_headers = []

        def start_response(status, headers_list):
            response_status.append(status)
            response_headers.extend(headers_list)

        resp_body_chunks = app(environ, start_response)
        resp_body = b"".join(resp_body_chunks).decode("utf-8")
        return response_status[0], dict(response_headers), resp_body

    # 1. Health check
    status, _, body = make_request("GET", "/health")
    assert "200 OK" in status
    assert '"status": "HEALTHY"' in body
    assert '"auth_mode": "local_demo_unauthenticated"' in body

    # 2. Ingest incident
    status, _, body = make_request(
        "POST",
        "/api/v1/incidents",
        body=sample_receipt_str.encode("utf-8"),
        headers={"X-FireShield-Tenant-Id": "ws-tenant", "Content-Type": "application/json"},
    )
    assert "201 Created" in status
    resp_json = json.loads(body)
    inc_id = resp_json["incident_id"]

    # 3. List incidents
    status, _, body = make_request(
        "GET", "/api/v1/incidents", headers={"X-FireShield-Tenant-Id": "ws-tenant"}
    )
    assert "200 OK" in status
    list_json = json.loads(body)
    assert list_json["total_count"] == 1

    # 4. Get incident detail
    status, _, body = make_request(
        "GET", f"/api/v1/incidents/{inc_id}", headers={"X-FireShield-Tenant-Id": "ws-tenant"}
    )
    assert "200 OK" in status
    assert inc_id in body

    # 5. Run retest
    status, _, body = make_request(
        "POST", f"/api/v1/incidents/{inc_id}/retest", headers={"X-FireShield-Tenant-Id": "ws-tenant"}
    )
    assert "200 OK" in status
    assert '"test_status": "DETECTION_PASS"' in body

    # 6. Get advisory
    status, _, body = make_request(
        "GET", f"/api/v1/incidents/{inc_id}/advisory", headers={"X-FireShield-Tenant-Id": "ws-tenant"}
    )
    assert "200 OK" in status
    assert '"advisory_status": "DETERMINISTIC_OFFLINE_ANALYSIS"' in body

    # 7. CORS preflight (allowed origin)
    status, hdrs, _ = make_request(
        "OPTIONS", "/api/v1/incidents", headers={"Origin": "http://localhost:3000"}
    )
    assert "204 No Content" in status
    assert hdrs.get("Access-Control-Allow-Origin") == "http://localhost:3000"

    # 8. CORS preflight (untrusted origin fails closed with 403)
    status, hdrs, _ = make_request(
        "OPTIONS", "/api/v1/incidents", headers={"Origin": "https://attacker.invalid"}
    )
    assert "403 Forbidden" in status
    assert "Access-Control-Allow-Origin" not in hdrs


def test_request_level_spoofed_header_rejected(sample_receipt_str):
    """Scope A: Request-level spoofed header rejected with 403 Forbidden."""
    app = create_wsgi_app(trusted_tenant="publisher-demo-tenant")

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api/v1/incidents",
        "QUERY_STRING": "",
        "wsgi.input": io.BytesIO(b""),
        "CONTENT_LENGTH": "0",
        "HTTP_X_FIRESHIELD_TENANT_ID": "malicious-tenant-spoof",
    }
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response)).decode("utf-8")
    assert "403 Error" in captured["status"]
    err = json.loads(body)
    assert "Conflicting tenant assertion" in err["error"]
    assert err["status_code"] == 403


def test_wsgi_pagination(sample_receipt_str, audio_receipt_str):
    """Scope B: Query string pagination contract (limit, offset bounds)."""
    api = FireShieldLocalApiAdapter()
    auth = AuthContext(tenant_id="pagination-tenant")
    api.ingest_incident_receipt(sample_receipt_str, auth)
    api.ingest_incident_receipt(audio_receipt_str, auth)

    app = create_wsgi_app(api, trusted_tenant="pagination-tenant")

    def query(qs):
        env = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/v1/incidents",
            "QUERY_STRING": qs,
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
            "HTTP_X_FIRESHIELD_TENANT_ID": "pagination-tenant",
        }
        res = {}
        body = b"".join(app(env, lambda s, h: res.update(status=s, headers=dict(h)))).decode("utf-8")
        return res["status"], json.loads(body)

    # limit=0 returns empty incidents
    status, body = query("limit=0&offset=1")
    assert "200 OK" in status
    assert body["limit"] == 0
    assert body["offset"] == 1
    assert len(body["incidents"]) == 0
    assert body["total_count"] == 2

    # limit=1 returns 1 incident
    status, body = query("limit=1&offset=0")
    assert "200 OK" in status
    assert len(body["incidents"]) == 1

    # limit > 100 rejected
    status, body = query("limit=101")
    assert "400 Error" in status

    # negative offset rejected
    status, body = query("offset=-1")
    assert "400 Error" in status


def test_wsgi_content_type_and_payload_hardening(sample_receipt_str):
    """Scope B: Content-Type enforcement (415), negative Content-Length (400), invalid UTF-8 (400)."""
    app = create_wsgi_app(trusted_tenant="demo-tenant")

    def post(payload, content_type=None, content_length=None):
        env = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/v1/incidents",
            "wsgi.input": io.BytesIO(payload),
            "CONTENT_LENGTH": str(len(payload)) if content_length is None else content_length,
            "HTTP_X_FIRESHIELD_TENANT_ID": "demo-tenant",
        }
        if content_type:
            env["CONTENT_TYPE"] = content_type
        res = {}
        body = b"".join(app(env, lambda s, h: res.update(status=s, headers=dict(h)))).decode("utf-8")
        return res["status"], json.loads(body)

    # 415 on text/plain
    status, body = post(b'{"test": 1}', content_type="text/plain")
    assert "415 Error" in status
    assert "application/json" in body["error"]

    # 400 on negative content length
    status, body = post(b'{"test": 1}', content_length="-5")
    assert "400 Error" in status
    assert "non-negative" in body["error"]

    # 400 on invalid UTF-8 bytes
    status, body = post(b"\xff\xfe", content_type="application/json")
    assert "400 Error" in status
    assert "Invalid UTF-8" in body["error"]


def test_schema_validation_null_objects_and_bad_types(sample_receipt_str):
    """Scope C: Strict schema validation rejects null objects and bad enum types with 400."""
    data = json.loads(sample_receipt_str)

    # Null required objects
    null_obj_data = json.loads(sample_receipt_str)
    null_obj_data["environment"] = None
    null_obj_data["receipt_digest"] = compute_receipt_digest(null_obj_data)
    with pytest.raises(Exception) as exc:
        validate_and_parse_incident_receipt(json.dumps(null_obj_data))
    assert "non-null JSON object" in str(exc.value)

    # Wrong status type (list instead of string)
    wrong_type_data = json.loads(sample_receipt_str)
    wrong_type_data["recovery_status"] = []
    wrong_type_data["receipt_digest"] = compute_receipt_digest(wrong_type_data)
    with pytest.raises(Exception) as exc:
        validate_and_parse_incident_receipt(json.dumps(wrong_type_data))
    assert "Invalid recovery_status" in str(exc.value)


def test_legacy_v1_unicode_receipt_acceptance(sample_receipt_str):
    """Scope D: Legacy schema v1 receipts computed with ensure_ascii=True must be accepted."""
    import hashlib
    data = json.loads(sample_receipt_str)
    data["environment"]["app_version"] = "cafe\u00e9"
    # Legacy v1 digest computed with ensure_ascii=True
    legacy_json = json.dumps(
        {k: v for k, v in data.items() if k != "receipt_digest"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    data["receipt_digest"] = f"sha256:{hashlib.sha256(legacy_json.encode('utf-8')).hexdigest()}"

    parsed = validate_and_parse_incident_receipt(json.dumps(data))
    assert parsed["incident_id"] == data["incident_id"]
    assert parsed["environment"]["app_version"] == "cafe\u00e9"


def test_untrusted_origin_cannot_trigger_retest(sample_receipt_str):
    """Reject simple cross-origin POSTs before they can mutate retest history."""
    from api_adapter import DEFAULT_DEMO_TENANT
    api = FireShieldLocalApiAdapter()
    auth = AuthContext(DEFAULT_DEMO_TENANT)
    incident_id = api.ingest_incident_receipt(sample_receipt_str, auth)["incident_id"]
    app = create_wsgi_app(api, trusted_tenant=DEFAULT_DEMO_TENANT)
    captured = {}
    body = b"".join(app({
        "REQUEST_METHOD": "POST",
        "PATH_INFO": f"/api/v1/incidents/{incident_id}/retest",
        "wsgi.input": io.BytesIO(b""),
        "CONTENT_LENGTH": "0",
        "CONTENT_TYPE": "text/plain",
        "HTTP_ORIGIN": "https://untrusted.invalid",
    }, lambda s, h: captured.update(status=s, headers=dict(h))))
    assert captured["status"].startswith("403")
    assert "Access-Control-Allow-Origin" not in captured["headers"]
    assert api.get_incident(incident_id, auth)["retest_history"] == []


@pytest.mark.parametrize("media_type", ["", "text/application/json-invalid", "application/jsonp"])
def test_ingest_requires_exact_json_media_type(sample_receipt_str, media_type):
    app = create_wsgi_app()
    payload = sample_receipt_str.encode("utf-8")
    captured = {}
    b"".join(app({
        "REQUEST_METHOD": "POST", "PATH_INFO": "/api/v1/incidents",
        "wsgi.input": io.BytesIO(payload), "CONTENT_LENGTH": str(len(payload)),
        "CONTENT_TYPE": media_type,
    }, lambda s, h: captured.update(status=s, headers=dict(h))))
    assert captured["status"].startswith("415")
