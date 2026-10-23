# FireShield: Product Specification & Capability Matrix

**Version**: 0.4.0-preview  
**Date**: October 2026  
**Status**: Connected Three-Part Editorial & Player Safety Prototype  

---

## 1. Executive Product Overview

FireShield is an experimental prototype for deterministic photosensitive hazard screening, version-bound editorial release gating, and playback continuity verification. It bridges three historically disconnected disciplines in streaming television:

1. **Deterministic Frame-by-Frame Pre-Broadcast Screening**: Mathematical detection of photosensitive seizure triggers (SDR BT.709 luminance flashes, saturated red transitions, and 2D spatial periodic patterns). Not broadcast or medical certified.
2. **Synaptic Shield Context-Bound Editorial Release Gating**: Version-bound cryptographic packaging where human clearance editors review findings, approve subtitle-grounded narrative context, and publish asymmetric **Ed25519 v2 sidecar contracts** bound to the exact output rendition playlist SHA-256.
3. **FireTrace Recovery & Incident Forensics**: Post-incident root-cause investigation for TV client playback disruptions, featuring deterministic retest simulation, EviRCA advisory diagnosis, and version-bound continuity preservation.

---

## 2. Target User Personas & Workflows

### Persona A: Standards & Practices (S&P) Clearance Editor
* **Primary Need**: Identify candidate photosensitive hazard intervals with frame accuracy, understand narrative context around cuts, and record an immutable clearance sign-off.
* **Workflow in FireShield**:
  1. Inspects catalog asset in the **Screening & Clearance Suite**.
  2. Reviews candidate hazard intervals on the multi-track visual timeline.
  3. Inspects the extracted subtitle dialogue around the hazard.
  4. Authorizes or adjusts the Synaptic Shield context note (e.g. narrative warning banner or auto-skip cue).
  5. Records explicit human sign-off (`approved` or `rejected`) with reviewer credentials.

### Persona B: Video Compression & Packaging Engineer
* **Primary Need**: Ensure output renditions (HLS/DASH), ad splices, and stream modifications maintain cryptographic integrity without desyncing hazard metadata.
* **Workflow in FireShield**:
  1. Configures rendition packaging specifications (trim start, intro bumpers, rendition set IDs).
  2. Publishes the **Ed25519 v2 sidecar manifest**.
  3. Validates that the manifest digest is cryptographically bound to the rendition playlist hash (`rendition_playlist_sha256`).
  4. Exports the sidecar and runnable intent command for TV delivery.

### Persona C: Platform SRE & TV Quality Engineer
* **Primary Need**: Investigate client-side playback divergences, track dropouts, or recovery latches reported by Android TV devices in the field.
* **Workflow in FireShield**:
  1. Ingests native incident receipts in the **FireTrace Incident Lab**.
  2. Inspects cryptographic receipt digests and mismatch fields (e.g., dropped caption track, cut drift, position overshoot).
  3. Executes deterministic retests in the retest harness.
  4. Reads the EviRCA advisory diagnosis for labeled hypotheses and allowable next checks.

---

## 3. Verified Architecture & Invariants

| Layer | Component | Status | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **Analyzer** | SDR BT.709 Luminance, Saturated Red, 2D FFT Spatial Grating | **VERIFIED** | 85 passed Python tests (`test_screening_and_pipeline.py`, `tests/test_run7_defects.py`) |
| **Editorial Gate** | Human Approval Gate (`approved` / `rejected`), Version Parity | **VERIFIED** | Controlled publication failure verified when unapproved (`test_pipeline_service.py`) |
| **Cryptography** | Asymmetric Ed25519 v2 Signatures & Digest Binding | **VERIFIED** | Validated via `SidecarContractValidator` and `state_engine.py` |
| **Continuity** | Version-Bound Checkpoints & FireTrace Recovery Gate | **VERIFIED** | 23 passed Kotlin unit tests (`fireos/app/src/test/...`) |
| **Native TV Player** | Fire OS / Media3 ExoPlayer Harness (`MainActivity.kt`) | **BUILD VERIFIED** | Debug APK built: `app-debug.apk` (`AC11FAD6...`), unit test exit 0 |
| **TV Hardware** | Physical Fire TV / Android TV Emulator Device Run | **PENDING** | Host platform-tools installed; 0 active AVDs attached (`adb devices = 0`) |

---

## 4. Explicit Non-Claims & Capability Boundaries

FireShield maintains complete transparency regarding experimental boundaries:
1. **Not Broadcast or Medical Certified**: FireShield does NOT claim official Ofcom, ITU-R BT.1702 formal certification, or Harding FPA commercial certification.
2. **SDR BT.709 Only**: Transfer curve linearization is implemented for standard dynamic range sRGB and BT.709. HDR10, HLG, and ITU-R BT.2100 PQ transfer curves are explicitly categorized as unimplemented.
3. **No Unbounded Cloud Spend**: AWS Bedrock Converse integration is designed with offline fallback (`model_invoked=false`). Bedrock credentials are parked; no cloud fees or unauthorized external calls are made.
4. **No Synthetic Video Fabrication**: Corrupted, invalid, or missing video streams fail closed with explicit HTTP 400/503 errors; the system never substitutes clean synthetic frames for broken customer media.
