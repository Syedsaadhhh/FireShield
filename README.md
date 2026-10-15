# FireShield — Version-Bound Hazard Intervention Contract, B2B Release Gate, & Native Fire OS / Android TV Harness

> **Status:** `VERIFIED LOCAL` (`58 passed` Python tests + `22 passed` Kotlin JUnit tests) & `BUILT NATIVE` (`app-debug.apk` compiled for Fire OS / Android TV Media3 ExoPlayer, SHA-256: `8934409D7DE5F4B9C404B6869DE8A78C7B2E7923A782899A95BE8CB6E03185F2`).  
> **`RUN ON SIMULATOR OR DEVICE`:** **VERIFIED RUNTIME** on Android TV Emulator (`FireShield_TV_API28`, Android 9 API 28 x86 via WHPX QEMU on port 5556; note: [Devpost FAQ](https://amazonappdev2026.devpost.com/details/faqs) explicitly permits an Android TV Emulator for Fire TV track submission testing). All 11 runtime scenarios passed (`nominal_playback`, `process_death_recovery`, `background_foreground`, `dropped_subtitles`, `dropped_audio`, `stale_playlist`, `presentation_mismatch`, `seek_overshoot`, `readiness_timeout`, `corrupt_checkpoint`, `stale_asset_revision`).  
> **`AWS DEPLOYED`:** Blocked (no live AWS credentials or provisioned cloud resources; dry-run planner and 6-tier readiness inspector verified locally).  
> **Iteration Cadence:** This repository represents one foundational layer in a multi-iteration B2B product build—not a disposable single-app MVP, not a certified medical device, and not broadcast regulatory certification (HardingFPA/Ofcom/ITU-R BT.1702-3).  
> See [`BUILD_STATUS.md`](BUILD_STATUS.md), [`RESUME_STATE.md`](RESUME_STATE.md), [`AWS_SETUP.md`](AWS_SETUP.md) for complete scope boundaries, preserved B2B backlog, and risk registers.

---

## 1. B2B Problem Scope & First-Class Architectural Boundaries

**FireShield** is a version-bound visual-hazard release contract and player intervention layer for **streaming publishers and multi-tenant media platforms that control their own Fire TV / Android TV player and catalog**.

1. **Publisher-Controlled Scope & Multi-Tenant Isolation:** A Fire TV application cannot inspect or modify protected frames inside third-party apps (such as Prime Video or Netflix) due to OS isolation, DRM, and private GPU buffers. FireShield operates strictly on media authorized, packaged, and played by the publisher's own player, enforcing strict `tenant_id` isolation across release records (`get_tenant_records`), sidecar validation, and structured audit trails (`get_tenant_audit_log`, `FireShieldAuditSink`).
2. **Asymmetric Ed25519 Sidecar Contract (`schema_version: 2`) with Key Rotation & Revocation:** Client devices do not hash gigabytes of DRM-protected video buffers in memory, nor do they hold shared symmetric secrets. The release gate binds `tenant_id`, `asset_revision_id`, `rendition_set_id`, `rendition_playlist_sha256`, `signing_alg: "Ed25519"`, and `key_id` into a canonical JSON digest (`manifest_digest`) signed with an Ed25519 private key. Clients verify the contract using a rotatable `trusted_public_keys` ring and enforce explicit `revoked_key_ids` revocation (`update_key_ring` / `updateKeyRing`), rejecting downgrades to `schema_version: 1` (HMAC) by default.
3. **Asynchronous, Fail-Closed Player Gate & Checkpoint Continuity:** Hardware decoders complete seeks asynchronously (`Player.Listener.onPositionDiscontinuity`). Both [`firetv_player_adapter.py`](firetv_player_adapter.py) and [`FireShieldExoPlayerBridge.kt`](fireos/app/src/main/java/com/fireshield/fireos/FireShieldExoPlayerBridge.kt) keep playback paused and the shutter surface blanked while an async seek is in flight, unblanking only after `onPositionDiscontinuity(DISCONTINUITY_REASON_SEEK)` confirms `actual_position_ms >= safe_resume_ms` with matching `seek_token`, active rendition identity, and within timeout. On checkpoint recovery (`ContinuityCheckpoint.kt`, `RecoveryVerifier.kt`, `RecoveryReadinessGate.kt`), expected presentation policies (captions, subtitle track, audio track, presentation mode) are checked against actual observed tracks; any dropped track, intro cut drift, or position overshoot fails closed and emits a canonical JSON incident receipt with a SHA-256 integrity digest (`computeReceiptDigest`).
4. **Human-in-the-Loop Release Gate & Benign Media Defaults:** Candidate intervals from offline screening are emitted in `review_state="pending_review"`. No synthetic confidence score auto-publishes an asset; a human editor must explicitly record `"approved"` or `"rejected"`. Hazard screening fixtures are strictly headless; the runtime default media fixture is a calm 120-second 0Hz baseline stream (`benign_master.m3u8`, `benign_variant.m3u8`, `benign_seg0.ts`) with WebVTT subtitles (`subtitles_en.vtt`) bound by signed Ed25519 contract (`benign_sidecar_v2.json`).

---

## 2. Repository Structure

| File / Directory | Purpose & Verification Tier |
| :--- | :--- |
| [`state_engine.py`](state_engine.py) | **P0 (`VERIFIED LOCAL`):** `SidecarContractValidator` & `FireShieldRuntime` enforcing `schema_version: 2` Ed25519 public-key verification, key rotation/revocation (`revoked_key_ids`, `update_key_ring`), downgrade rejection (`allow_legacy_v1_hmac=False`), interval margin checks, and fail-closed state transitions. |
| [`continuity_engine.py`](continuity_engine.py) | **P0/Continuity (`VERIFIED LOCAL`):** Versioned atomic checkpointing, recovery verification against observed player tracks, canonical incident receipt generation with SHA-256 integrity digest (`receipt_digest`), privacy guarantees, and deterministic retest simulation. |
| [`retest_harness.py`](retest_harness.py) | **P0/Continuity CLI (`VERIFIED LOCAL`):** Bounded retest CLI accepting native or synthetic incident receipts, validating `receipt_digest` integrity, and executing deterministic retest scenarios distinguishing `DETECTION_PASS` from `PLAYER_FIX_PASS`. |
| [`firetv_player_adapter.py`](firetv_player_adapter.py) | **P0/P3 (`VERIFIED LOCAL`):** Asynchronous `FireShieldExoPlayerAdapter` and `AsyncInMemoryExoPlayerDouble` enforcing in-flight seek shutter blanking until `on_seek_completed_callback` verification. |
| [`test_compliance.py`](test_compliance.py) | **P0 Tests (`VERIFIED LOCAL`):** 39 unit/adversarial tests covering Ed25519 v2 contracts, v1 downgrade rejection, key rotation, tampered payloads, and asynchronous player seek failure modes. |
| [`test_continuity.py`](test_continuity.py) | **P0/Continuity Tests (`VERIFIED LOCAL`):** 12 tests covering atomic checkpoint persistence, tamper rejection, active recovery verification, dropped caption/audio detection, cut shift detection, privacy receipt sanitization, hostile-string rejection, receipt digest verification, presentation mode fault selection, and three-way deterministic ablation benchmark. |
| [`screening_analyzer.py`](screening_analyzer.py) | **P1 (`VERIFIED LOCAL`):** SDR BT.709 screening prototype (`fireshield-screening-proto:0.3.0-sdr-bt709-2dfft`) with multi-orientation (`0°`, `90°`, `±45°`) slice probing + 2D FFT spectral-concentration spatial detector, headless `ffmpeg` decode, and 8-fixture benchmark (`TP=5, TN=3, FP=0, FN=0`). |
| [`release_pipeline.py`](release_pipeline.py) | **P2 (`VERIFIED LOCAL`):** `LocalReleaseGatePipeline` (Ed25519 v2 sidecar signing, `rotate_signing_key`, `revoke_signing_key`, tenant-isolated `get_tenant_audit_log`, and timeline mapping), 6-tier `check_aws_deployment_readiness()`, and `AwsCloudReleaseGateDryRunPlanner`. |
| [`verification_pipeline.json`](verification_pipeline.json) | **P2 (`VERIFIED LOCAL`):** Parameterized AWS Step Functions ASL definition with AWS Batch screening, `waitForTaskToken` human approval, explicit `rejected` terminal state, and MediaConvert `.sync` packaging. |
| [`.env.example`](.env.example) & [`AWS_SETUP.md`](AWS_SETUP.md) | **P2 (`VERIFIED LOCAL`):** Non-secret environment template, IAM least-privilege role scope, SSO workflow, and cost guardrails. |
| [`test_screening_and_pipeline.py`](test_screening_and_pipeline.py) | **P1–P3 Tests (`VERIFIED LOCAL`):** 7 integration test suites covering the 8-fixture 2D FFT benchmark, `ffmpeg` decode, v2 release pipeline, B2B key rotation/revocation & multi-tenant audit isolation, 6-tier AWS readiness probe, and async ExoPlayer contract double. |
| [`fireos/`](fireos/app/build.gradle.kts) | **Native Fire OS / Android TV Module (`BUILT NATIVE` + `VERIFIED RUNTIME`):** Compilable Android/Kotlin Media3 ExoPlayer module ([`FireShieldContract.kt`](fireos/app/src/main/java/com/fireshield/fireos/FireShieldContract.kt), [`ContinuityCheckpoint.kt`](fireos/app/src/main/java/com/fireshield/fireos/ContinuityCheckpoint.kt), [`RecoveryVerifier.kt`](fireos/app/src/main/java/com/fireshield/fireos/RecoveryVerifier.kt), [`RecoveryReadinessGate.kt`](fireos/app/src/main/java/com/fireshield/fireos/RecoveryReadinessGate.kt), [`SelectedTrackIdentity.kt`](fireos/app/src/main/java/com/fireshield/fireos/SelectedTrackIdentity.kt), [`FireShieldExoPlayerBridge.kt`](fireos/app/src/main/java/com/fireshield/fireos/FireShieldExoPlayerBridge.kt), [`MainActivity.kt`](fireos/app/src/main/java/com/fireshield/fireos/MainActivity.kt)), 22 JUnit tests, and publisher-owned benign 120s HLS stream ([`assets/hls/`](fireos/app/src/main/assets/hls/PROVENANCE_BENIGN.json)). |
| [`tools/verify_android_tv.py`](tools/verify_android_tv.py) | **Android TV Runtime Verification Runner:** Automated ADB test orchestrator executing 11 end-to-end scenarios on `emulator-5556`, capturing screencaps, logcat traces, native incident receipts, and piping receipts to `retest_harness.py`. |
| [`BUILD_STATUS.md`](BUILD_STATUS.md) | **Engineering Ledger:** Five-tier verification matrix, exact commands, APK SHA-256, 11 emulator scenario results, preserved multi-iteration B2B product backlog, and non-secret AWS handoff checklist. |

---

## 3. Exact Verification Commands

### 3.0 Set Up a Fresh Clone

Prerequisites: Python (the verification host used 3.14.4), FFmpeg on PATH for the full screening suite, and JDK 17 plus an Android SDK for the native build. Install the Android SDK platform/build tools declared in `fireos/app/build.gradle.kts`.

```powershell
git clone https://github.com/Syedsaadhhh/FireShield.git
cd FireShield
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest test_compliance.py test_continuity.py test_screening_and_pipeline.py -q
```

For the commands below, activate this environment or replace `python` with `.\.venv\Scripts\python.exe`. Set `JAVA_HOME` to your JDK 17 installation and `ANDROID_HOME` to your Android SDK; host-specific paths in the measured commands are examples. From the repository root, the portable native command is `.\fireos\gradlew.bat -p fireos testDebugUnitTest assembleDebug --console=plain`.

Create/start an Android TV AVD before runtime verification, install `fireos/app/build/outputs/apk/debug/app-debug.apk`, and pass its actual serial from `adb devices` to the runner. Generated receipts and evidence are local outputs, produced by that runner; the retest examples below require those outputs.

### 3.1 Run All Python Unit & Integration Tests (`58 passed`)
```powershell
python -m pytest test_compliance.py test_continuity.py test_screening_and_pipeline.py -q --basetemp=tmp_pytest_run4
```
*(Exit code: 0, 58 passed in 41.63s)*

### 3.2 Run P1 8-Fixture Screening Benchmark (`TP=5, TN=3, FP=0, FN=0`)
```powershell
python screening_analyzer.py
```

### 3.3 Run P2 AWS Readiness Check & Ed25519 Release Gate Dry Run
```powershell
python release_pipeline.py
```

### 3.4 Build Native Fire OS / Android TV APK & Run Kotlin JUnit4 Tests (`22 passed`)
```powershell
$env:JAVA_HOME="C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot"
& "C:\Users\ATEC\Desktop\FireShield\fireos\gradlew.bat" -p "C:\Users\ATEC\Desktop\FireShield\fireos" testDebugUnitTest assembleDebug --console=plain
```
*(Compiled APK: `fireos\app\build\outputs\apk\debug\app-debug.apk`, SHA-256: `8934409D7DE5F4B9C404B6869DE8A78C7B2E7923A782899A95BE8CB6E03185F2`)*

### 3.5 Execute Full Android TV Emulator Runtime Verification Suite (`11 of 11 Passed`)
```powershell
python tools/verify_android_tv.py --serial emulator-5556
```
*(Output: `ALL RUNTIME SCENARIOS PASSED`, results recorded in `evidence/2026-10-15-run4/runtime/runtime_results.json`)*

### 3.6 Bounded Incident Receipt Retest Execution
```powershell
python retest_harness.py --receipt-file evidence/2026-10-15-run4/runtime/drop_subtitles_receipt.json
python retest_harness.py --receipt-file evidence/2026-10-15-run4/runtime/drop_audio_receipt.json
python retest_harness.py --receipt-file evidence/2026-10-15-run4/runtime/cut_drift_receipt.json
python retest_harness.py --receipt-file evidence/2026-10-15-run4/runtime/presentation_mismatch_receipt.json
python retest_harness.py --receipt-file evidence/2026-10-15-run4/runtime/seek_overshoot_receipt.json
```
*(Status: `DETECTION_PASS`, exit code: 0)*
