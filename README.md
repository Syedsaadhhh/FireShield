# FireShield

**Screen a release. Intervene on the right edition. Verify what survives an interruption.**

FireShield combines a visual screening prototype, Synaptic Shield's version-bound player intervention, and FireTrace's playback continuity checks in a publisher-controlled Fire TV / Android TV application.

The intended user is a streaming publisher that controls its catalog and player. The goal is to connect editorial decisions to playback behavior, then detect when recovery silently loses captions, audio selection, presentation settings, or the expected cut of the content.

## Three connected layers

| Layer | Implemented mechanism | What it establishes |
| --- | --- | --- |
| Visual screening | Python/NumPy SDR luminance, saturated-red, and spatial-pattern analysis; headless FFmpeg decoding; explicit editorial approval | Candidate intervals for review, bound to a source revision |
| Synaptic Shield | Ed25519-signed sidecar; tenant, asset, rendition and playlist binding; asynchronous Media3 seek callbacks; paused and blanked surface during uncertain transitions | A player acts on the contract for the selected edition and checks the seek landing before releasing its shutter |
| FireTrace | Atomic playback checkpoints; expected versus observed player-selection comparison; bounded incident receipts and reference retests | Dropped captions/audio, changed cuts, position drift and presentation mismatch can be detected rather than silently accepted |

The native app uses Kotlin, Android Media3 ExoPlayer, local HLS, and WebVTT. The reference engine uses Python, NumPy and cryptography. The included sample media is publisher-owned synthetic material; hazardous screening fixtures are analyzed headlessly.

## Current source and verification

This checkout preserves the recoverable GitHub native/reference implementation and integrates the `codex/recovery-evidence-alignment` fixes. On **23 October 2026**, **81 Python tests passed** on Python 3.12 with FFmpeg available. These are reference/integration results, not a fresh claim that the native player has passed on a device.

Later Antigravity work on the home laptop, including the completed Run 8 reported by the team, has **not been recovered or verified in this checkout**. Reconcile that workspace with this revision when it becomes available; do not replace its uncommitted files with this snapshot.

The cloud workflows build the native APK, run Kotlin tests, and attempt an actual Android TV emulator capture. A workflow file is a reproducibility route, not evidence that a run succeeded. Consult [Actions](https://github.com/Syedsaadhhh/FireShield/actions) and its exact commit's artifacts for results, logs, APK and recordings. The capture contains a real native debug harness, controlled fault injection, and player-selection evidence. It does not prove decoded subtitle/audio output or physical Fire TV behavior.

Older counts and device claims in `BUILD_STATUS.md` and `RESUME_STATE.md` describe historical local work; they are not current verification for this checkout.

## Run the reference tests

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

Install FFmpeg on PATH for the decode test. `requirements-dev.txt` records the dependency versions used by the source-alignment review; `requirements.txt` is the general development dependency list.

## Build and run the TV app

Use JDK 17 and the Android SDK. The Gradle wrapper declares the build tool versions.

```sh
# Windows
fireos\gradlew.bat -p fireos testDebugUnitTest assembleDebug --console=plain
# macOS/Linux
bash fireos/gradlew -p fireos testDebugUnitTest assembleDebug --console=plain
adb install -r fireos/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.fireshield.fireos/.MainActivity
```

For a dedicated Android TV emulator, `python tools/verify_android_tv.py --serial emulator-5554` runs the bounded debug scenarios and writes local evidence. Confirm your emulator serial first. `tools/capture_cloud_demo.py` records native playback, recovery, a dropped-caption fault and a stale-edition fault; GitHub's **Build and record Android TV demo** workflow provides a cloud route without a locally installed SDK.

## Scope

- FireShield operates inside a participating publisher's player. It does not inspect or modify Netflix, Prime Video or other protected third-party apps.
- The screening engine is an SDR research prototype. Its small synthetic benchmark does not establish clinical efficacy, universal detection, broadcast compliance, HDR coverage or certification.
- Checkpoint/receipt hashes detect accidental changes; they are not signatures proving a trustworthy device. Sidecar authenticity uses Ed25519 with an independently trusted public key.
- Player selection and successful model retests do not prove rendered captions, audible audio or a repaired native player. Receipts preserve that distinction.
- AWS orchestration and configuration examples are included. **A live AWS/Bedrock deployment is not established.** Local verification does not require AWS credentials.

## Source map

`screening_analyzer.py` and `release_pipeline.py` handle screening and release contracts. `state_engine.py` and `firetv_player_adapter.py` implement the reference player gate. `continuity_engine.py` and `retest_harness.py` handle continuity and bounded reference retests. `fireos/` contains the actual native TV app and tests. `tools/` contains fixture and emulator utilities. `AWS_SETUP.md`, `.env.example` and `verification_pipeline.json` describe optional cloud setup.

## License

Apache License 2.0. See [LICENSE](LICENSE). Preserve dependency licenses and media provenance when redistributing the app.
