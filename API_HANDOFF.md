# FireShield — Frontend Integration & Local API Handoff Contract

**Document:** `API_HANDOFF.md`  
**Date:** 20 October 2026 (Asia/Karachi)  
**Target Audience:** Frontend Teammate (Investigation Dashboard & TV Companion App)  
**Service Endpoint:** `http://127.0.0.1:8000` (Local Offline Loopback Adapter)  
**Implementation Source:** [`api_adapter.py`](api_adapter.py), [`pipeline_service.py`](pipeline_service.py), [`context_engine.py`](context_engine.py)  
**Verification Suites:** [`test_api_adapter.py`](test_api_adapter.py), [`test_pipeline_service.py`](test_pipeline_service.py), [`tests/test_run7_defects.py`](tests/test_run7_defects.py) (`85 passed Python, 23 passed Kotlin`)

---

## 1. Architectural Overview & Security Invariants

The FireShield API adapter provides a clean, isolated service boundary connecting all three prototype subsystems:
1. **Asset Screening & Editorial Review:** Ingests video frames, executes deterministic SDR luminance/red/2D spatial frequency analysis, enforces human review approval gates, and strictly blocks unreviewed or rejected media.
2. **Synaptic Story Context & Publication:** Extracts subtitle cue IDs around hazard intervals, provides offline fallback (`model_invoked=false`) or Bedrock Converse grounding, and binds approved notes under an Ed25519 v2 sidecar manifest.
3. **FireTrace Recovery & Incident Investigation:** Validates native TV player incident receipts, enforces tenant scoping and digest-locked idempotency, and runs bounded deterministic retest simulations.
4. **Local Demo Identity Boundary (`auth_mode: "local_demo_unauthenticated"`):** Operates on loopback bound to a server-configured demo tenant (default: `publisher-demo-tenant`). Arbitrary client headers asserting a conflicting tenant are rejected with `403 Forbidden`.
5. **Fail-Closed Origin Boundary:** CORS requests are restricted to explicit local development origins (`http://localhost:3000`, `http://localhost:5173`, `http://localhost:8000`, `http://localhost:8080`, and 127.0.0.1 equivalents). Wildcard (`*`) reflection is disabled.
6. **Zero Arbitrary Execution:** Bounded retests execute only allowlisted simulation scenarios mapped to known fixture IDs (`DROPPED_CAPTION_FAULT_V1`, etc.). Model-generated shell commands are strictly prohibited.

---

## 2. Quick Start for Frontend Development

Launch the local API server and operator console:
```powershell
python api_adapter.py
```
Output:
```
[FIRESHIELD API] Binding local offline adapter on http://127.0.0.1:8000
[FIRESHIELD API] Press Ctrl+C to terminate.
```

The operator console UI is available immediately at:
- `http://127.0.0.1:8000/operator` (or `http://127.0.0.1:8000/`)

CORS is pre-configured to accept requests from standard frontend development ports:
- `http://localhost:3000`
- `http://localhost:5173` (Vite)
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5173`

---

## 3. Standard Request Headers

| Header Name | Type | Description | Default (Local) |
| :--- | :--- | :--- | :--- |
| `Content-Type` | `string` | Must be `application/json; charset=utf-8` | Required for POST |
| `X-FireShield-Tenant-Id` | `string` | Scopes all reads and writes to the publisher tenant | `publisher-demo-tenant` |
| `X-FireShield-User-Id` | `string` | Identifies the engineer or operator triggering actions | `local-publisher-engineer` |

---

## 4. REST Endpoints Specification

### 4.1 Health & Environment Readiness
- **Endpoint:** `GET /health` (or `/api/v1/health`)
- **Description:** Returns server readiness, active environment mode, storage statistics, and cloud connection flags.
- **Response `200 OK`:**
```json
{
  "status": "HEALTHY",
  "environment_mode": "local_in_memory_adapter",
  "auth_mode": "local_demo_unauthenticated",
  "api_version": "0.3.0",
  "server_time_iso": "2026-10-20T03:00:00Z",
  "bind_address": "127.0.0.1:8000",
  "storage": {
    "adapter": "in_memory",
    "total_incidents": 1,
    "active_tenants": 1
  },
  "cloud_readiness": {
    "aws_live_status": "UNCONFIGURED_OFFLINE",
    "bedrock_advisory_status": "UNCONFIGURED_OFFLINE",
    "live_spend_authorized": false
  }
}
```

---

### 4.2 Asset Catalog Management
- **`GET /api/v1/assets`**: Lists all catalog assets in the authenticated tenant.
  - **Response `200 OK`:**
    ```json
    {
      "tenant_id": "publisher-demo-tenant",
      "assets": [
        {
          "asset_id": "ast-benign-navy-clip",
          "tenant_id": "publisher-demo-tenant",
          "name": "Publisher Benign Navy Presentation Clip (Standard HLS)",
          "asset_revision_id": "sha256:...",
          "source_type": "synthetic_fixture",
          "size_bytes": 1048576,
          "duration_ms": 120000,
          "fps": 24.0,
          "width": 320,
          "height": 240,
          "has_screening": false,
          "review_state": "unreviewed"
        }
      ]
    }
    ```
- **`POST /api/v1/assets`**: Registers a managed asset from raw bytes or managed workspace file.
  - **Request Body:**
    ```json
    {
      "name": "My Promo Clip",
      "source_type": "managed_video",
      "source_path": "data/catalog/clip.mp4",
      "subtitles_vtt": "WEBVTT\n..."
    }
    ```
  - **Response `201 Created`:** Asset record object.
- **`GET /api/v1/assets/{asset_id}`**: Retrieves asset details.

---

### 4.3 Deterministic Screening & Editorial Review Gate
- **`POST /api/v1/screening/jobs`**: Starts deterministic frame screening on an asset.
  - **Request Body:**
    ```json
    { "asset_id": "ast-hazard-luminance-strobe" }
    ```
  - **Response `201 Created`:**
    ```json
    {
      "job_id": "job-...",
      "tenant_id": "publisher-demo-tenant",
      "asset_id": "ast-hazard-luminance-strobe",
      "asset_revision_id": "sha256:...",
      "status": "COMPLETED",
      "findings_count": 1,
      "detector_version": "fireshield-screening-proto:0.3.0-sdr-bt709-2dfft"
    }
    ```
- **`GET /api/v1/screening/reports/{asset_revision_id}`**: Retrieves the full screening report, candidate hazard findings, and attached context notes.
- **`POST /api/v1/screening/reports/{asset_revision_id}/review`**: Submits human editorial decision (`approved` or `rejected`).
  - **Request Body:**
    ```json
    {
      "reviewer_id": "editor-lead-01",
      "decision": "approved",
      "notes": "Verified safe with context banner."
    }
    ```
  - **Response `200 OK`:** Updated screening report.

---

### 4.4 Synaptic Story Context Grounding
- **`POST /api/v1/context/ground`**: Extracts subtitle cues around a hazard interval and generates candidate narrative context.
  - **Request Body:**
    ```json
    {
      "asset_revision_id": "sha256:...",
      "interval_id": "finding-001",
      "method": "extractive_subtitles"
    }
    ```
  - **Response `200 OK`:**
    ```json
    {
      "note_id": "note-finding-001",
      "interval_id": "finding-001",
      "text": "Warning: high frequency emergency warning strobe active.",
      "source_cue_ids": ["cue-001"],
      "generation_method": "extractive_subtitles",
      "editorial_approved": false,
      "model_invoked": false
    }
    ```
- **`POST /api/v1/context/approve`**: Human editor approves the candidate note for publication.
  - **Request Body:**
    ```json
    {
      "asset_revision_id": "sha256:...",
      "interval_id": "finding-001",
      "reviewer_id": "editor-chief"
    }
    ```
  - **Response `200 OK`:** Updated context note with `editorial_approved: true`.

---

### 4.5 Version-Bound Publication & TV Handoff
- **`POST /api/v1/releases/publish`**: Packages and publishes an Ed25519 v2 signed sidecar manifest.
  - **Validation Invariant:** Requires screening `review_state == "approved"`. `unreviewed` or `rejected` assets fail closed with `400 Bad Request`.
  - **Request Body:**
    ```json
    {
      "asset_revision_id": "sha256:...",
      "rendition_set_id": "hls-main-320x240",
      "playlist_manifest": "#EXTM3U\n...",
      "media_uri": "asset:///hls/benign_variant.m3u8"
    }
    ```
  - **Response `201 Created`:**
    ```json
    {
      "release_id": "rel-...",
      "manifest_digest": "sha256:...",
      "key_id": "pub-01-ed25519-2026",
      "signing_alg": "Ed25519",
      "public_key_hex": "207a9096...",
      "harness_intent_command": "adb shell am start -n com.fireshield.fireos/.MainActivity ...",
      "context_notes_count": 1
    }
    ```
- **`GET /api/v1/releases/{release_id}/sidecar.json`**: Downloads raw signed sidecar manifest (`Content-Type: application/json`).
- **`GET /api/v1/releases/{release_id}/manifest.m3u8`**: Downloads rendition playlist (`Content-Type: application/vnd.apple.mpegurl`).

---

### 4.6 FireTrace Incident Telemetry & Retest
- **`POST /api/v1/incidents`**: Ingests strict cryptographic incident receipts.
  - **Response `201 Created` (New)** or `200 OK` (Idempotent duplicate). Duplicate IDs with altered digests return `409 Conflict`.
- **`GET /api/v1/incidents`**: Lists incidents for the authenticated tenant with pagination (`?limit=50&offset=0`).
- **`GET /api/v1/incidents/{incident_id}`**: Retrieves complete incident receipt, observed vs. expected state, and retest history.
- **`POST /api/v1/incidents/{incident_id}/retest`**: Runs bounded retest simulation using `retest_harness`.
  - **Response `200 OK`:**
    ```json
    {
      "incident_id": "inc-tv-demo-4491",
      "retest_execution": {
        "fixture_id": "DROPPED_CAPTION_FAULT_V1",
        "test_status": "DETECTION_PASS",
        "verification_result": "DIVERGENCE_DETECTED",
        "detail": "Verifier successfully caught dropped captions (expected=True, actual=False)."
      }
    }
    ```
- **`GET /api/v1/incidents/{incident_id}/advisory`**: Returns EviRCA deterministic advisory separating verifiable facts from hypotheses.

---

## 5. Error Responses Reference

All error responses strictly adhere to:
```json
{
  "error": "<Safe descriptive error message>",
  "status_code": 400
}
```

| HTTP Status | Error Type | Cause |
| :--- | :--- | :--- |
| `400 Bad Request` | `ApiError` / `PipelineServiceError` | Schema violation, unapproved release publication, malformed JSON |
| `403 Forbidden` | `ApiForbiddenError` | Conflicting tenant header, unauthorized CORS origin |
| `404 Not Found` | `ApiNotFoundError` | Incident, asset, job, or release not found in tenant scope |
| `409 Conflict` | `ApiConflictError` | Duplicate `incident_id` with differing payload digest |
| `413 Payload Too Large` | `ApiError` | Request body exceeds 64KB limit |
| `415 Unsupported Media Type` | `ApiError` | Missing or non-`application/json` Content-Type on POST |
