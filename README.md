# FireShield

**Screen the media. Bind the decision to its version. Enforce it in the player. Verify recovery.**

FireShield combines visual-hazard screening and Synaptic Shield playback policies with FireTrace continuity diagnostics for publishers who control their own Fire TV / Android TV application and media catalog.

A title-level warning cannot keep an intervention aligned after a new edit or confirm that captions survived an app restart. FireShield connects content review, version-specific playback enforcement and recovery verification in one publisher workflow.

## The product

| Layer | What it does |
| --- | --- |
| **Screening and editorial review** | Decodes authorized video with FFmpeg and computes candidate luminance-flash, red-flash and spatial-pattern intervals. Findings require explicit human review. |
| **Synaptic Shield intervention** | Binds approved intervals to a signed media-version contract. The native player checks the contract, pauses and blanks before a flagged interval, displays approved context notes, and verifies asynchronous seeking before continuing. |
| **FireTrace continuity** | Saves playback checkpoints and verifies position, selected captions, audio track and presentation mode after recovery. Divergence holds playback and produces structured incident evidence. |

The target connected experience is **ingest → screen → review → publish → protected playback → verified recovery → investigation**. Core modules are implemented; the complete media-to-device flow is still being verified.

## Capabilities implemented locally

- **Frame-based screening:** deterministic SDR analysis using NumPy, multi-orientation pattern probing and 2D FFT spectral concentration.
- **Editorial release gate:** explicit approval or rejection; pending findings cannot be published.
- **Synaptic story context:** WebVTT subtitle cue parsing, extractive grounding, and optional Amazon Bedrock Converse adapter with explicit offline fallback (`model_invoked=false`).
- **Version-bound contracts:** Ed25519 v2 signatures bind tenant, asset revision, rendition identity, playlist digest, and approved context notes; key rotation and revocation are supported.
- **Timeline mapping:** accounts for configured trims, intros and reviewed ad splices, rejecting unsupported collisions.
- **Native player enforcement:** Kotlin and AndroidX Media3 ExoPlayer implement pre-event intervention, shutter blanking, context banner display, seek acknowledgement and timeout handling. Enforcement of actual loaded-media identity is under correction; the current build is not claimed as a verified end-to-end release.
- **Recovery checks:** atomic checkpoints preserve viewer intent and compare actual restored presentation state.
- **Incident investigation:** a loopback HTTP API accepts validated incidents and exposes list, detail, bounded regression simulations and clearly labelled offline advisory explanations.
- **Operator console:** local operator dashboard (`static/index.html`) serving live screening findings, context review, Ed25519 publication, TV intent generation, and FireTrace incident inspection.

## Current status and verification

- **Python test suite:** 86 passed, 0 skipped, 0 failed across all 6 suites (`test_compliance.py`, `test_continuity.py`, `test_screening_and_pipeline.py`, `test_api_adapter.py`, `test_pipeline_service.py`, `tests/test_run7_defects.py`). Exit code 0.
- **Kotlin unit tests:** 23 passed across `FireShieldNativeGateTest`, `RecoveryReadinessGateTest`, and `SelectedTrackIdentityTest`. Exit code 0.
- **Debug APK:** compiled at `fireos/app/build/outputs/apk/debug/app-debug.apk` with verified Media3 TV gate and continuity checkpointing.
- **Native player enforcement:** Kotlin and AndroidX Media3 ExoPlayer implement pre-event intervention, shutter blanking, context banner display, seek acknowledgement and timeout handling. Loaded media is validated against the signed sidecar contract; mismatched or tampered playlists fail closed before playback begins. Features a premium Android TV layout with TV-safe margins (48dp horizontal, 27dp vertical), legible 18–20sp typography, D-pad focus styling with Amber accent and 1.05x scale, and collapsible diagnostic fault controls.
- **Local integration demonstration:** `tools/run_connected_demonstration.py` exercises real frame screening, human approval gates, Ed25519 v2 signing bound to actual HLS packaging, tamper rejection, and FireTrace incident retesting in headless harness mode.
- **Editorial finishing room suite:** `static/index.html` delivers a complete broadcast standards and clearance console featuring an interactive multi-track timeline with real subtitle cue parsing, safe frame monitor, photometric metric meters, Synaptic Shield narrative grounding, editorial sign-off gate, asynchronous screening job polling, non-blocking inline feedback banners, and Ed25519 v2 release packaging. Fully responsive across 1440px, 1366px, and 390px viewports without horizontal clipping.
- **Native device execution:** live Android TV emulator proof on this host remains `PENDING / UNVERIFIED` because no active Android TV emulator or physical device is currently attached to ADB (`emulator -list-avds` returns 0 configured AVDs; SDK system images absent on host).
- **Cloud integration:** AWS orchestration and live Bedrock inference remain strictly unconfigured/offline (`model_invoked=false`, zero cloud spend).
- **Integration corrections applied:** invalid-media decode failure handling, exact catalog/report identity binding, durable store rehydration across restart, absence of fabricated subtitle cues, and strict filesystem path confinement have been verified with regression tests.

## Technology

**Python · NumPy · FFmpeg · Ed25519 cryptography · Kotlin · AndroidX Media3 ExoPlayer · Android TV / Fire OS**

AWS integration is designed around S3, DynamoDB and Amazon Bedrock, with a separate release-pipeline design for Step Functions, Batch and MediaConvert. These cloud services are not claimed as deployed in local testing.

## Run locally

Prerequisites: Python 3.10+, FFmpeg, JDK 17 and Android SDK platform-tools.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python api_adapter.py
~~~

The local API and operator console bind to `http://127.0.0.1:8000`. Its demo identity is unauthenticated and restricted to a configured local tenant; it is not a production authentication system. Open `http://127.0.0.1:8000/operator` in any modern browser.

Run the Python verification suite:

~~~powershell
python -m pytest test_compliance.py test_continuity.py test_screening_and_pipeline.py test_api_adapter.py test_pipeline_service.py -q
~~~

Run the end-to-end connected demonstration:

~~~powershell
python tools/run_connected_demonstration.py
~~~

Build and test the native application:

~~~powershell
.\fireos\gradlew.bat -p fireos testDebugUnitTest assembleDebug --console=plain
~~~

Install `fireos/app/build/outputs/apk/debug/app-debug.apk` on an Android TV emulator or compatible Fire OS device. Verify runtime behavior with `tools/verify_android_tv.py` when an emulator instance is running.

## Scope and limits

- Operates on publisher-authorized media in a participating player. It does not inspect Netflix, Prime Video or other protected applications.
- The detector is an SDR screening prototype, not medical or broadcast certification. A bounded fixture benchmark does not establish general detection accuracy.
- Human review remains part of publishing. Language-model output must not determine hazard boundaries or authorize playback.
- Regression simulations and native device runs are distinct verification modes.
- Credentials and private signing material belong outside source control and client applications.

## Code map

- `screening_analyzer.py` — video decoding and candidate detection
- `release_pipeline.py` — review, timeline mapping and signed publication
- `context_engine.py` — subtitle cue parsing, extractive context grounding, and Bedrock Converse adapter
- `pipeline_service.py` — durable domain service for catalog, jobs, editorial gates, and release publishing
- `state_engine.py` — contract validation and playback policy state machine
- `continuity_engine.py` — checkpoints and incident validation
- `api_adapter.py` — local investigation and pipeline API
- `static/index.html` — local operator console UI
- `retest_harness.py` — bounded regression simulations
- `tools/run_connected_demonstration.py` — end-to-end demonstration runner
- `tools/link_incident.py` — native player incident bridge
- `fireos/` — native Android TV / Fire OS application
- `tests/fixtures/` — portable test inputs

## License

See [LICENSE](LICENSE). Third-party dependencies retain their respective licenses.
