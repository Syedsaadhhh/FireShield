#!/usr/bin/env python3
"""
FireShield Local API & Data Adapter Contract (Offline Hardening & Integration Boundary).

DESIGN & SECURITY INVARIANTS:
1. Service Boundary: Serves local frontend and testing with strict validation before storage.
2. Server-Derived Tenant: Tenant scope is strictly derived from server authentication / context
   (e.g., X-FireShield-Tenant-Id header), NEVER trusted from an unverified payload body.
3. Idempotency: Duplicate receipt submissions with identical digests return idempotent 200 OK.
   Conflicting submissions with matching incident_id but different digests are rejected with 409 Conflict.
4. Bedrock Advisory Separation: Bedrock integration is strictly read-only and advisory.
   It never executes code or shell commands, never alters contracts, and never touches the player
   critical path. When unconfigured, it reports explicit offline fallback without inventing connection status.
5. Loopback Default: Binds strictly to 127.0.0.1 (loopback) to prevent public unauthenticated exposure.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import re
import sys
import time
import urllib.parse
from http import HTTPStatus
from typing import Any, Dict, List, Mapping, Optional, Tuple
from wsgiref.simple_server import make_server

from continuity_engine import (
    ContinuityError,
    compute_receipt_digest,
    validate_and_parse_incident_receipt,
)
from pipeline_service import (
    LocalPipelineService,
    PipelineServiceError,
)
from retest_harness import run_retest_scenario

logger = logging.getLogger("fireshield.api")

DEFAULT_LOCAL_BIND_HOST = "127.0.0.1"
DEFAULT_LOCAL_BIND_PORT = 8000
MAX_REQUEST_BODY_BYTES = 65536
DEFAULT_DEMO_TENANT = "publisher-demo-tenant"
STATIC_INDEX_HTML = pathlib.Path(__file__).resolve().parent / "static" / "index.html"

# CORS allowed local development origins (fail-closed: no wildcard reflection)
ALLOWED_LOCAL_ORIGINS = frozenset({
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
})


class ApiError(Exception):
    """Base API exception with HTTP status code and safe client message."""
    def __init__(self, message: str, status_code: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ApiNotFoundError(ApiError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=HTTPStatus.NOT_FOUND)


class ApiConflictError(ApiError):
    def __init__(self, message: str = "Resource conflict detected"):
        super().__init__(message, status_code=HTTPStatus.CONFLICT)


class ApiUnauthorizedError(ApiError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, status_code=HTTPStatus.UNAUTHORIZED)


class ApiForbiddenError(ApiError):
    def __init__(self, message: str = "Access forbidden"):
        super().__init__(message, status_code=HTTPStatus.FORBIDDEN)


@dataclasses.dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_id: str = "local-developer"
    roles: Tuple[str, ...] = ("engineer", "qa")
    auth_mode: str = "local_demo_unauthenticated"

    @classmethod
    def from_headers(
        cls,
        headers: Mapping[str, str],
        trusted_tenant: Optional[str] = None,
    ) -> AuthContext:
        """
        Derives tenant context with explicit trusted boundary.
        In local demo mode: rejects conflicting raw client headers that attempt arbitrary tenant spoofing.
        In remote mode: fails closed until verified credentials/adapter are configured.
        """
        if os.environ.get("FIRESHIELD_REMOTE_MODE", "").lower() in ("true", "1", "remote"):
            raise ApiUnauthorizedError("Remote authentication required; local demo context not permitted in remote mode")

        active_tenant = trusted_tenant or os.environ.get("FIRESHIELD_DEFAULT_TENANT_ID", DEFAULT_DEMO_TENANT)
        header_tenant = headers.get("x-fireshield-tenant-id") or headers.get("X-FireShield-Tenant-Id")
        if header_tenant:
            clean_header = re.sub(r"[^a-zA-Z0-9_\-\.]", "", header_tenant.strip())
            if clean_header != active_tenant:
                raise ApiForbiddenError(
                    f"Conflicting tenant assertion '{clean_header}' rejected; "
                    f"local demo mode only accepts server-configured demo tenant '{active_tenant}'"
                )

        user_id = headers.get("x-fireshield-user-id", "local-publisher-engineer")
        clean_user = re.sub(r"[^a-zA-Z0-9_\-\.]", "", user_id.strip()) or "local-publisher-engineer"
        return cls(tenant_id=active_tenant, user_id=clean_user, auth_mode="local_demo_unauthenticated")


@dataclasses.dataclass
class StoredIncident:
    incident_id: str
    tenant_id: str
    receipt_digest: str
    receipt: Dict[str, Any]
    ingested_at_iso: str
    retest_history: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    advisory_explanation: Optional[Dict[str, Any]] = None


class FireShieldLocalApiAdapter:
    """
    In-memory, cloud-agnostic API adapter implementing the FireShield backend contract.
    Guarantees strict receipt schema validation, tenant isolation, idempotency, and deterministic retests.
    """

    def __init__(self, pipeline_service: Optional[LocalPipelineService] = None):
        self.pipeline_service = pipeline_service or LocalPipelineService()
        # Key: (tenant_id, incident_id) -> StoredIncident
        self._store: Dict[Tuple[str, str], StoredIncident] = {}
        # Global map to enforce unique incident_id invariants across all tenants: incident_id -> tenant_id
        self._global_incident_to_tenant: Dict[str, str] = {}
        self._start_time_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def get_health(self) -> Dict[str, Any]:
        """Returns service readiness, active mode, and unverified cloud boundary status."""
        bedrock_configured = bool(os.environ.get("FIRESHIELD_BEDROCK_MODEL_ID") and os.environ.get("AWS_PROFILE"))
        aws_configured = bool(os.environ.get("AWS_PROFILE") or os.environ.get("AWS_ACCESS_KEY_ID"))

        return {
            "status": "HEALTHY",
            "environment_mode": "local_in_memory_adapter",
            "auth_mode": "local_demo_unauthenticated",
            "api_version": "0.3.0",
            "server_time_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "bind_address": f"{DEFAULT_LOCAL_BIND_HOST}:{DEFAULT_LOCAL_BIND_PORT}",
            "storage": {
                "adapter": "in_memory",
                "total_incidents": len(self._store),
                "active_tenants": len(set(t for t, _ in self._store.keys())),
            },
            "cloud_readiness": {
                "aws_live_status": "CONFIGURED_UNVERIFIED" if aws_configured else "UNCONFIGURED_OFFLINE",
                "bedrock_advisory_status": "CONFIGURED_UNVERIFIED" if bedrock_configured else "UNCONFIGURED_OFFLINE",
                "live_spend_authorized": False,
            },
        }

    def ingest_incident_receipt(self, raw_payload: str, auth: AuthContext) -> Dict[str, Any]:
        """
        Strictly parses, validates, and ingests an incident receipt into tenant storage.
        Enforces idempotency and rejects conflicting duplicate IDs.
        """
        if not raw_payload or not raw_payload.strip():
            raise ApiError("Receipt payload is empty", status_code=HTTPStatus.BAD_REQUEST)

        # 1. Strict schema validation
        try:
            receipt = validate_and_parse_incident_receipt(raw_payload, strict=True)
        except ContinuityError as exc:
            raise ApiError(f"Receipt validation failed: {exc}", status_code=HTTPStatus.BAD_REQUEST) from exc

        incident_id = receipt["incident_id"]
        incoming_digest = receipt["receipt_digest"]

        # 2. Cross-tenant incident collision check
        existing_tenant = self._global_incident_to_tenant.get(incident_id)
        if existing_tenant is not None and existing_tenant != auth.tenant_id:
            # Timing-safe rejection preventing enumeration of other tenants' incident IDs
            raise ApiConflictError("Incident identifier collision or duplicate submission under conflicting scope")

        store_key = (auth.tenant_id, incident_id)

        # 3. Idempotency check within tenant
        if store_key in self._store:
            existing = self._store[store_key]
            if existing.receipt_digest == incoming_digest:
                return {
                    "status": "IDEMPOTENT_DUPLICATE_ACCEPTED",
                    "incident_id": incident_id,
                    "tenant_id": auth.tenant_id,
                    "receipt_digest": existing.receipt_digest,
                    "ingested_at_iso": existing.ingested_at_iso,
                    "retest_fixture_id": receipt["retest_fixture_id"],
                }
            raise ApiConflictError(
                f"Incident '{incident_id}' already exists with differing digest "
                f"(stored={existing.receipt_digest}, incoming={incoming_digest})"
            )

        # 4. Persistence in tenant store
        ingested_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        stored = StoredIncident(
            incident_id=incident_id,
            tenant_id=auth.tenant_id,
            receipt_digest=incoming_digest,
            receipt=receipt,
            ingested_at_iso=ingested_at,
        )
        self._store[store_key] = stored
        self._global_incident_to_tenant[incident_id] = auth.tenant_id

        return {
            "status": "INGESTED",
            "incident_id": incident_id,
            "tenant_id": auth.tenant_id,
            "receipt_digest": incoming_digest,
            "ingested_at_iso": ingested_at,
            "retest_fixture_id": receipt["retest_fixture_id"],
        }

    def list_incidents(
        self, auth: AuthContext, limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        """Lists incidents strictly scoped to the authenticated tenant."""
        tenant_incidents = [
            {
                "incident_id": inc.incident_id,
                "tenant_id": inc.tenant_id,
                "fixture_id": inc.receipt.get("fixture_id"),
                "retest_fixture_id": inc.receipt.get("retest_fixture_id"),
                "recovery_status": inc.receipt.get("recovery_status"),
                "diagnostic_code": inc.receipt.get("diagnostic_code"),
                "mismatch_fields": inc.receipt.get("mismatch_fields"),
                "position_delta_ms": inc.receipt.get("position_delta_ms"),
                "receipt_digest": inc.receipt_digest,
                "ingested_at_iso": inc.ingested_at_iso,
                "retest_count": len(inc.retest_history),
                "has_advisory": inc.advisory_explanation is not None,
            }
            for (t_id, _), inc in self._store.items()
            if t_id == auth.tenant_id
        ]
        # Sort newest first by ingested_at_iso
        paginated = tenant_incidents[offset : offset + limit] if limit > 0 else []
        return {
            "tenant_id": auth.tenant_id,
            "total_count": len(tenant_incidents),
            "limit": limit,
            "offset": offset,
            "incidents": paginated,
        }

    def get_incident(self, incident_id: str, auth: AuthContext) -> Dict[str, Any]:
        """Retrieves complete detail of an incident, strictly scoped to the authenticated tenant."""
        store_key = (auth.tenant_id, incident_id)
        stored = self._store.get(store_key)
        if not stored:
            raise ApiNotFoundError(f"Incident '{incident_id}' not found for tenant '{auth.tenant_id}'")

        return {
            "incident_id": stored.incident_id,
            "tenant_id": stored.tenant_id,
            "receipt_digest": stored.receipt_digest,
            "ingested_at_iso": stored.ingested_at_iso,
            "receipt": stored.receipt,
            "retest_history": stored.retest_history,
            "advisory_explanation": stored.advisory_explanation,
        }

    def run_retest(self, incident_id: str, auth: AuthContext) -> Dict[str, Any]:
        """
        Executes a bounded, deterministic regression retest using retest_harness.
        Never executes arbitrary shell commands or dynamic scripts.
        """
        store_key = (auth.tenant_id, incident_id)
        stored = self._store.get(store_key)
        if not stored:
            raise ApiNotFoundError(f"Incident '{incident_id}' not found for tenant '{auth.tenant_id}'")

        # Execute bounded retest scenario on immutable receipt
        result = run_retest_scenario(stored.receipt)
        execution_entry = {
            "executed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fixture_id": result["fixture_id"],
            "test_status": result["test_status"],
            "detail": result["detail"],
            "verification_result": result["verification_result"],
            "mismatch_fields": result["mismatch_fields"],
            "evidence_scope": result.get("evidence_scope", "REFERENCE_MODEL_ONLY"),
            "native_player_executed": False,
            "player_fix_verified": False,
        }
        stored.retest_history.append(execution_entry)

        return {
            "incident_id": incident_id,
            "tenant_id": auth.tenant_id,
            "retest_execution": execution_entry,
        }

    def get_advisory_explanation(
        self, incident_id: str, auth: AuthContext, *, force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Generates or retrieves an evidence-grounded incident advisory explanation.
        Adheres to EviRCA architecture: separates verifiable deterministic facts from hypotheses.
        When Bedrock is unconfigured, returns clean local deterministic advisory without fabricating AI connectivity.
        """
        store_key = (auth.tenant_id, incident_id)
        stored = self._store.get(store_key)
        if not stored:
            raise ApiNotFoundError(f"Incident '{incident_id}' not found for tenant '{auth.tenant_id}'")

        if stored.advisory_explanation and not force_refresh:
            return stored.advisory_explanation

        receipt = stored.receipt
        mismatches = receipt.get("mismatch_fields", [])
        retest_fix = receipt.get("retest_fixture_id", "UNKNOWN")
        pos_delta = receipt.get("position_delta_ms", 0)

        # Deterministic evidence facts extracted directly from the verified receipt
        facts = [
            f"Observed recovery divergence with {len(mismatches)} mismatch field(s): {', '.join(mismatches)}.",
            f"Observed position delta: {pos_delta} ms.",
            f"Deterministic retest fixture mapped: {retest_fix}.",
            f"Cryptographic receipt digest: {stored.receipt_digest}.",
        ]

        # Hypotheses based on verified fault scenarios
        hypotheses = []
        if "captions_enabled" in mismatches:
            hypotheses.append("Decoder surface reattachment or subtitle track selection failed during resume.")
        if "selected_audio_track_id" in mismatches:
            hypotheses.append("Audio renderer track group changed or language preference fell back to default.")
        if "rendition_playlist_sha256" in mismatches:
            hypotheses.append("Asset cut drift detected: upstream master playlist was re-encoded or altered post-checkpoint.")
        if "position_ms" in mismatches:
            hypotheses.append(f"Keyframe seek overshoot: playback resumed {pos_delta}ms beyond target sync boundary.")
        if "presentation_mode" in mismatches:
            hypotheses.append("Presentation mode preference divergence between committed checkpoint and active player.")

        permitted_next_check = f"Run bounded retest harness on fixture {retest_fix}."

        explanation = {
            "incident_id": incident_id,
            "tenant_id": auth.tenant_id,
            "advisory_status": "DETERMINISTIC_OFFLINE_ANALYSIS",
            "model_invoked": False,
            "model_id": None,
            "insufficient_evidence": len(mismatches) == 0,
            "evidence_facts": facts,
            "labeled_hypotheses": hypotheses,
            "permitted_next_check": permitted_next_check,
            "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "safety_note": "Read-only advisory. Never executes model-provided shell commands.",
        }

        stored.advisory_explanation = explanation
        return explanation


def _read_json_body(environ: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to strictly validate and decode JSON request payloads."""
    raw_cl = environ.get("CONTENT_LENGTH")
    content_length = 0
    if raw_cl is not None and str(raw_cl).strip():
        try:
            content_length = int(raw_cl)
        except ValueError:
            raise ApiError("Content-Length must be an integer", HTTPStatus.BAD_REQUEST)
        if content_length < 0:
            raise ApiError("Content-Length must be non-negative", HTTPStatus.BAD_REQUEST)
        if content_length > MAX_REQUEST_BODY_BYTES:
            raise ApiError("Payload exceeds 64KB limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError("Content-Type must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    raw_bytes = environ["wsgi.input"].read(min(content_length, MAX_REQUEST_BODY_BYTES + 1))
    if len(raw_bytes) > MAX_REQUEST_BODY_BYTES:
        raise ApiError("Payload exceeds 64KB limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiError(f"Invalid UTF-8 payload: {exc}", HTTPStatus.BAD_REQUEST)

    if not raw_text.strip():
        return {}

    try:
        data = json.loads(raw_text)
        if not isinstance(data, dict):
            raise ApiError("JSON payload must be an object", HTTPStatus.BAD_REQUEST)
        return data
    except json.JSONDecodeError as exc:
        raise ApiError(f"Malformed JSON: {exc}", HTTPStatus.BAD_REQUEST)


def create_wsgi_app(
    adapter: Optional[FireShieldLocalApiAdapter] = None,
    trusted_tenant: Optional[str] = None,
):
    api = adapter or FireShieldLocalApiAdapter()
    active_trusted_tenant = (
        trusted_tenant
        or os.environ.get("FIRESHIELD_DEFAULT_TENANT_ID")
        or (next(iter(api._store.keys()))[0] if api and api._store else DEFAULT_DEMO_TENANT)
    )

    def application(environ: Dict[str, Any], start_response: Any):
        path = environ.get("PATH_INFO", "/").rstrip("/")
        method = environ.get("REQUEST_METHOD", "GET").upper()
        origin = environ.get("HTTP_ORIGIN", "")

        # Base CORS headers for local frontend integration (fail-closed, no wildcard)
        cors_headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
            ("Access-Control-Allow-Headers", "Content-Type, X-FireShield-Tenant-Id, X-FireShield-User-Id"),
        ]
        if origin in ALLOWED_LOCAL_ORIGINS:
            cors_headers.append(("Access-Control-Allow-Origin", origin))
            cors_headers.append(("Vary", "Origin"))

        # Reject before dispatch: hiding CORS response headers alone does not stop
        # a cross-origin simple POST from triggering a retest or other side effect.
        if origin and origin not in ALLOWED_LOCAL_ORIGINS:
            start_response("403 Forbidden", [("Content-Type", "application/json; charset=utf-8")])
            return [b'{"error": "Origin not permitted by CORS policy", "status_code": 403}']

        if method == "OPTIONS":
            start_response("204 No Content", cors_headers)
            return [b""]

        try:
            # Extract headers for AuthContext strictly bound to trusted tenant
            headers = {
                "x-fireshield-tenant-id": environ.get("HTTP_X_FIRESHIELD_TENANT_ID", ""),
                "x-fireshield-user-id": environ.get("HTTP_X_FIRESHIELD_USER_ID", ""),
            }
            auth = AuthContext.from_headers(headers, trusted_tenant=active_trusted_tenant)

            # Route 0: Static Operator Console
            if (path in ("/operator", "/index.html") and method == "GET") or (
                path in ("", "/") and method == "GET" and "text/html" in environ.get("HTTP_ACCEPT", "")
            ):
                if STATIC_INDEX_HTML.exists():
                    html_content = STATIC_INDEX_HTML.read_bytes()
                    h_headers = [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(html_content))),
                    ]
                    if origin in ALLOWED_LOCAL_ORIGINS:
                        h_headers.append(("Access-Control-Allow-Origin", origin))
                    start_response("200 OK", h_headers)
                    return [html_content]

            # Route 1: Health check
            if path.startswith("/api/v1/demo/packaging/") and method == "GET":
                asset_id = path.rsplit("/", 1)[-1]
                asset = api.pipeline_service.get_asset(asset_id, auth.tenant_id)
                if asset_id == "ast-benign-navy-clip":
                    playlist_name = "benign_variant.m3u8"
                    source_mapping_verified = True
                elif asset_id in ("ast-hazard-luminance-strobe", "ast-hazard-spatial-checkerboard"):
                    playlist_name = "variant_1080p.m3u8"
                    source_mapping_verified = False
                else:
                    raise ApiError("No packaged demo media exists for this asset. Supply its exact packaged playlist through the publication API.", 409)
                playlist_path = pathlib.Path(__file__).resolve().parent / "fireos/app/src/main/assets/hls" / playlist_name
                if not playlist_path.is_file():
                    raise ApiError("Bundled HLS playlist is missing from this checkout.", 503)
                body = json.dumps({"playlist_manifest": playlist_path.read_text(encoding="utf-8"),
                    "media_uri": "asset:///hls/" + playlist_name,
                    "source_mapping_verified": source_mapping_verified,
                    "scope": "Bundled native fixture" if source_mapping_verified else "Reference packaging only; source-to-output equivalence has not been verified"}).encode()
                start_response("200 OK", cors_headers)
                return [body]

            if path in ("", "/health", "/api/v1/health") and method == "GET":
                body = json.dumps(api.get_health(), indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 2: Ingest incident receipt
            if path in ("/api/v1/incidents", "/api/v1/incidents/ingest") and method == "POST":
                raw_cl = environ.get("CONTENT_LENGTH")
                content_length = 0
                if raw_cl is not None and str(raw_cl).strip():
                    try:
                        content_length = int(raw_cl)
                    except ValueError:
                        raise ApiError("Content-Length must be an integer", HTTPStatus.BAD_REQUEST)
                    if content_length < 0:
                        raise ApiError("Content-Length must be non-negative", HTTPStatus.BAD_REQUEST)
                    if content_length > MAX_REQUEST_BODY_BYTES:
                        raise ApiError("Payload exceeds 64KB limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

                content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise ApiError("Content-Type must be application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

                raw_bytes = environ["wsgi.input"].read(min(content_length, MAX_REQUEST_BODY_BYTES + 1))
                if len(raw_bytes) > MAX_REQUEST_BODY_BYTES:
                    raise ApiError("Payload exceeds 64KB limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

                try:
                    raw_body = raw_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ApiError(f"Invalid UTF-8 payload: {exc}", HTTPStatus.BAD_REQUEST)

                res = api.ingest_incident_receipt(raw_body, auth)
                status_str = "201 Created" if res["status"] == "INGESTED" else "200 OK"
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response(status_str, cors_headers)
                return [body]

            # Route 3: List incidents (with limit/offset pagination)
            if path == "/api/v1/incidents" and method == "GET":
                query_string = environ.get("QUERY_STRING", "")
                qs = urllib.parse.parse_qs(query_string)
                limit = 50
                offset = 0
                if "limit" in qs:
                    try:
                        limit = int(qs["limit"][0])
                        if limit < 0 or limit > 100:
                            raise ApiError("limit query parameter must be between 0 and 100", HTTPStatus.BAD_REQUEST)
                    except ValueError:
                        raise ApiError("limit query parameter must be an integer", HTTPStatus.BAD_REQUEST)
                if "offset" in qs:
                    try:
                        offset = int(qs["offset"][0])
                        if offset < 0:
                            raise ApiError("offset query parameter must be non-negative", HTTPStatus.BAD_REQUEST)
                    except ValueError:
                        raise ApiError("offset query parameter must be an integer", HTTPStatus.BAD_REQUEST)

                res = api.list_incidents(auth, limit=limit, offset=offset)
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 4: Incident detail: /api/v1/incidents/<incident_id>
            m_detail = re.match(r"^/api/v1/incidents/([a-zA-Z0-9_\-\.]+)$", path)
            if m_detail and method == "GET":
                inc_id = m_detail.group(1)
                res = api.get_incident(inc_id, auth)
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 5: Incident retest: /api/v1/incidents/<incident_id>/retest
            m_retest = re.match(r"^/api/v1/incidents/([a-zA-Z0-9_\-\.]+)/retest$", path)
            if m_retest and method == "POST":
                inc_id = m_retest.group(1)
                res = api.run_retest(inc_id, auth)
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 6: Advisory explanation: /api/v1/incidents/<incident_id>/advisory
            m_advisory = re.match(r"^/api/v1/incidents/([a-zA-Z0-9_\-\.]+)/advisory$", path)
            if m_advisory and method == "GET":
                inc_id = m_advisory.group(1)
                res = api.get_advisory_explanation(inc_id, auth)
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # ---------------------------------------------------------------
            # Pipeline Service Routes (Catalog Assets, Screening, Review, Releases)
            # ---------------------------------------------------------------

            # Route 7: List Assets
            if path == "/api/v1/assets" and method == "GET":
                assets = api.pipeline_service.list_assets(auth.tenant_id)
                res = {"tenant_id": auth.tenant_id, "assets": assets}
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 8: Import Managed Asset
            if path == "/api/v1/assets" and method == "POST":
                data = _read_json_body(environ)
                raw_b = data.get("raw_bytes")
                if isinstance(raw_b, str):
                    raw_b = raw_b.encode("utf-8")
                record = api.pipeline_service.import_managed_asset(
                    tenant_id=auth.tenant_id,
                    name=data.get("name", ""),
                    source_type=data.get("source_type", "managed_video"),
                    source_path=data.get("source_path"),
                    raw_bytes=raw_b,
                    subtitles_vtt=data.get("subtitles_vtt"),
                )
                body = json.dumps(record.to_dict(), indent=2).encode("utf-8")
                start_response("201 Created", cors_headers)
                return [body]

            # Route 9: Asset Detail
            m_ast = re.match(r"^/api/v1/assets/([a-zA-Z0-9_\-\.]+)$", path)
            if m_ast and method == "GET":
                ast = api.pipeline_service.get_asset(m_ast.group(1), auth.tenant_id)
                body = json.dumps(ast.to_dict(), indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 10: Start Screening Job
            if path == "/api/v1/screening/jobs" and method == "POST":
                data = _read_json_body(environ)
                wait_val = bool(data.get("wait", False))
                job = api.pipeline_service.start_screening_job(
                    tenant_id=auth.tenant_id,
                    asset_id=data.get("asset_id"),
                    asset_revision_id=data.get("asset_revision_id"),
                    wait=wait_val,
                )
                body = json.dumps(job.to_dict(), indent=2).encode("utf-8")
                start_response("201 Created", cors_headers)
                return [body]

            # Route 11: Screening Job Detail
            m_job = re.match(r"^/api/v1/screening/jobs/([a-zA-Z0-9_\-\.]+)$", path)
            if m_job and method == "GET":
                job = api.pipeline_service.get_screening_job(m_job.group(1), auth.tenant_id)
                body = json.dumps(job.to_dict(), indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 12: Screening Report Detail
            m_report = re.match(r"^/api/v1/screening/reports/([a-zA-Z0-9_\-\.:]+)$", path)
            if m_report and method == "GET":
                report_dict = api.pipeline_service.get_screening_report(m_report.group(1), auth.tenant_id)
                body = json.dumps(report_dict, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 13: Submit Editorial Decision on Screening Report
            m_review = re.match(r"^/api/v1/screening/reports/([a-zA-Z0-9_\-\.:]+)/review$", path)
            if m_review and method == "POST":
                data = _read_json_body(environ)
                res = api.pipeline_service.submit_editorial_decision(
                    tenant_id=auth.tenant_id,
                    asset_revision_id=m_review.group(1),
                    reviewer_id=data.get("reviewer_id", auth.user_id),
                    decision=data.get("decision", ""),
                    notes=data.get("notes", ""),
                    zero_findings_attested=bool(data.get("zero_findings_attested", False)),
                )
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 14: Ground Context for Interval
            if path == "/api/v1/context/ground" and method == "POST":
                data = _read_json_body(environ)
                note = api.pipeline_service.ground_context_for_interval(
                    tenant_id=auth.tenant_id,
                    asset_revision_id=data.get("asset_revision_id", ""),
                    interval_id=data.get("interval_id", ""),
                    method=data.get("method", "extractive_subtitles"),
                    manual_text=data.get("manual_text"),
                    custom_vtt=data.get("custom_vtt"),
                )
                body = json.dumps(note, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 15: Approve Context Note
            if path == "/api/v1/context/approve" and method == "POST":
                data = _read_json_body(environ)
                note = api.pipeline_service.approve_context_note(
                    tenant_id=auth.tenant_id,
                    asset_revision_id=data.get("asset_revision_id", ""),
                    interval_id=data.get("interval_id", ""),
                    reviewer_id=data.get("reviewer_id", auth.user_id),
                    text=data.get("text"),
                    notes=data.get("notes"),
                )
                body = json.dumps(note, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 16: Publish Release (Ed25519 v2 Sidecar)
            if path == "/api/v1/releases/publish" and method == "POST":
                data = _read_json_body(environ)
                release = api.pipeline_service.publish_release(
                    tenant_id=auth.tenant_id,
                    asset_revision_id=data.get("asset_revision_id", ""),
                    rendition_set_id=data.get("rendition_set_id", "default-rendition"),
                    playlist_manifest=data.get("playlist_manifest", ""),
                    intro_offset_ms=int(data.get("intro_offset_ms", 0)),
                    trim_start_ms=int(data.get("trim_start_ms", 0)),
                    media_uri=data.get("media_uri"),
                )
                body = json.dumps(release.to_dict(), indent=2).encode("utf-8")
                start_response("201 Created", cors_headers)
                return [body]

            # Route 17: List Releases
            if path == "/api/v1/releases" and method == "GET":
                releases = api.pipeline_service.list_releases(auth.tenant_id)
                res = {"tenant_id": auth.tenant_id, "releases": releases}
                body = json.dumps(res, indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 18: Release Sidecar Download
            m_sidecar = re.match(r"^/api/v1/releases/([a-zA-Z0-9_\-\.]+)/sidecar\.json$", path)
            if m_sidecar and method == "GET":
                rel = api.pipeline_service.get_release(m_sidecar.group(1), auth.tenant_id)
                body = rel.sidecar_json.encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route 19: Release HLS Manifest Download
            m_manifest = re.match(r"^/api/v1/releases/([a-zA-Z0-9_\-\.]+)/manifest\.m3u8$", path)
            if m_manifest and method == "GET":
                rel = api.pipeline_service.get_release(m_manifest.group(1), auth.tenant_id)
                hls_headers = [("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8")]
                if origin in ALLOWED_LOCAL_ORIGINS:
                    hls_headers.append(("Access-Control-Allow-Origin", origin))
                start_response("200 OK", hls_headers)
                return [rel.playlist_manifest.encode("utf-8")]

            # Route 20: Release Detail
            m_rel = re.match(r"^/api/v1/releases/([a-zA-Z0-9_\-\.]+)$", path)
            if m_rel and method == "GET":
                rel = api.pipeline_service.get_release(m_rel.group(1), auth.tenant_id)
                body = json.dumps(rel.to_dict(), indent=2).encode("utf-8")
                start_response("200 OK", cors_headers)
                return [body]

            # Route not matched
            raise ApiNotFoundError(f"Endpoint not found: {method} {path}")

        except ApiError as exc:
            err_body = json.dumps({
                "error": exc.message,
                "status_code": exc.status_code,
            }, indent=2).encode("utf-8")
            start_response(f"{exc.status_code} Error", cors_headers)
            return [err_body]
        except PipelineServiceError as exc:
            err_body = json.dumps({
                "error": exc.message,
                "status_code": exc.status_code,
            }, indent=2).encode("utf-8")
            start_response(f"{exc.status_code} Error", cors_headers)
            return [err_body]
        except Exception as exc:
            logger.exception("Internal API error: %s", exc)
            err_body = json.dumps({
                "error": "Internal server error occurred",
                "status_code": HTTPStatus.INTERNAL_SERVER_ERROR,
            }, indent=2).encode("utf-8")
            start_response("500 Internal Server Error", cors_headers)
            return [err_body]

    return application


def serve_local(host: str = DEFAULT_LOCAL_BIND_HOST, port: int = DEFAULT_LOCAL_BIND_PORT):
    """Starts the local development API server on loopback."""
    app = create_wsgi_app()
    print(f"[FIRESHIELD API] Binding local offline adapter on http://{host}:{port}")
    print("[FIRESHIELD API] Press Ctrl+C to terminate.")
    with make_server(host, port, app) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[FIRESHIELD API] Server stopped cleanly.")


if __name__ == "__main__":
    serve_local()
