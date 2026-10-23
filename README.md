# FireShield

**Screen the source. Bind the decision to its edition. Verify the viewing experience after an interruption.**

FireShield connects visual screening, **Synaptic Shield** context-aware player intervention, and **FireTrace** continuity diagnostics for publishers who control their own Fire TV / Android TV application and media catalog.

A title-level warning cannot stay aligned after an edit or confirm that captions survived an app restart. FireShield carries an editorial decision into a signed, version-bound playback contract, then compares the expected recovery state with the player's observed selections.

## The connected prototype

| Layer | Implementation |
| --- | --- |
| **Visual screening** | Python/NumPy SDR luminance, saturated-red and spatial-pattern analysis; headless FFmpeg video decoding; candidate intervals with explicit human approval or rejection |
| **Synaptic Shield** | WebVTT-grounded context notes; human context approval; Ed25519 v2 contracts binding the source revision, tenant, rendition and playlist; native Media3 pause, shutter and asynchronous seek verification |
| **FireTrace** | Atomic playback checkpoints; expected versus observed position, caption/audio selection and presentation checks; bounded incident receipts; local investigation API and reference-model retests |

The operator console connects **catalog → screening → context review → editorial decision → signed publication → incident investigation**. The native Kotlin app contains the actual Media3 ExoPlayer integration. Hazard fixtures are analyzed headlessly; the native recording uses calm publisher-owned synthetic HLS with WebVTT captions.

## Run the operator console

Python 3.10+ is required. The local console needs two Python dependencies, no AWS credentials and no Android SDK:

```sh
python -m pip install -r requirements-demo.txt
python api_adapter.py
```

Open **http://127.0.0.1:8000/operator**. On Windows, `START_FIRESHIELD.bat` starts an already configured environment. Install FFmpeg on PATH for actual-media screening and the packaged media drill; the raw reference-frame cases work without it. Keep hazardous media out of screen recordings.

Local API authentication is a constrained demo tenant, not production identity management. Incidents are held in memory; catalog/review state is persisted under ignored `runtime_data/`. Restarting the service clears ingested incidents. The local signing seed is generated randomly and stays outside Git.

The catalog's **Build actual media drill** action encodes the owned emergency-scene fixture headlessly, registers the exact HLS segment as a new source revision, and keeps flashing content out of the console preview. Screen that new asset, ground and approve its context, commit the editorial decision, then publish. The package is signed against its actual playlist bytes. Download **Native handoff ZIP** after publication, install the debug APK, then run `python tools/install_native_package.py HANDOFF.zip --serial emulator-5554`. The installer checks media hashes, transfers the matching media and public release file, and starts a guarded debug session.

## Verify and build

```sh
python -m pip install -r requirements.txt
python -m pytest -q
python tools/run_connected_demonstration.py

# Windows: JDK 17 and Android SDK installed; JAVA_HOME points to JDK 17
fireos\gradlew.bat -p fireos testDebugUnitTest assembleDebug --console=plain
# macOS/Linux
bash fireos/gradlew -p fireos testDebugUnitTest assembleDebug --console=plain
```

The connected demonstration calculates screening findings, signs reviewed context, rejects a changed playlist identity, and generates a **fresh modeled caption-loss incident tied to that run's release**. It does not replay an old receipt or claim a native device was executed.

Install `fireos/app/build/outputs/apk/debug/app-debug.apk` on an Android TV emulator or compatible Fire OS device. The debug harness supports controlled recovery faults. `tools/verify_android_tv.py --serial emulator-5554` uses a dedicated emulator and writes fresh local evidence. It does not operate on physical devices.

## Verification status

On **23 October 2026**, **111 Python tests passed** on Python 3.12 with FFmpeg available, including the recovered Run 8 API/pipeline tests and the earlier receipt-boundary tests. JavaScript parsing and interactive browser checks are performed separately.

The integrated Run 8 source revision [`ed02cb3`](https://github.com/Syedsaadhhh/FireShield/commit/ed02cb3bff9f55798ecba6e000a0fefe379fed8b) passed **111 Python tests, 25 Kotlin unit tests, and native debug/release builds** in [cloud verification](https://github.com/Syedsaadhhh/FireShield/actions/runs/39959304604). Interactive operator checks completed at 1440px desktop and 390px mobile with no JavaScript errors or document-width overflow.

Its [Android TV recording run](https://github.com/Syedsaadhhh/FireShield/actions/runs/39959304569) also passed all **four native scenarios**: playback, player recreation, deliberately dropped captions, and stale-edition rejection. The downloadable artifact contains the APK, actual emulator captures, receipts, run identity and unit-test report. These are controlled debug-harness scenarios on Android TV API 28; they do not establish physical Fire TV coverage or rendered caption/audio correctness. Artifact retention is seven days, so retain the needed recordings before they expire.

`rescue/run8-20261023` preserves the original recovered laptop source. Main integrates that source with portable build/verification fixes and accurate evidence labels. The laptop's working directory is preserved separately.

## Scope and remaining integration boundary

- Operates inside a participating publisher's player. It cannot inspect or modify Netflix, Prime Video or other protected third-party apps.
- Screening is an experimental SDR detector. A small synthetic benchmark does not establish general accuracy, clinical efficacy, HDR coverage, broadcast compliance or certification.
- Zero findings is not a safety certificate. Context and release decisions remain human-reviewed; language-model output never sets hazard boundaries or authorizes playback.
- The packaged media drill screens the actual encoded segment and signs its matching playlist. `tools/capture_connected_demo.py` exports that exact media and public verification data to a dedicated native emulator, checks a pre-interval context hold and bypass, and investigates a fresh same-revision caption-loss incident. Its latest execution status is established by the workflow artifact. The original raw-frame catalog fixtures retain a separately labelled reference packaging path; they are not source-to-output proofs.
- Native recovery evidence establishes player-selection state and controlled fault detection. Rendered caption/audio correctness, every physical Fire TV model, and a production end-to-end release remain separate verification work.
- Receipt/checkpoint hashes detect changes; they do not authenticate an untrusted device. Sidecar authenticity uses Ed25519 with an independently trusted key. Release builds ignore external debug trust-root overrides.
- AWS orchestration examples and an optional Bedrock adapter are present. **Live AWS/Bedrock remains unverified and is not needed for this demo.** Offline context extraction reports `model_invoked=false`.

## Source map

| Path | Purpose |
| --- | --- |
| `screening_analyzer.py`, `release_pipeline.py` | Frame analysis, editorial decisions and signed release contracts |
| `context_engine.py`, `pipeline_service.py` | Subtitle grounding, catalog, asynchronous screening and local persistence |
| `state_engine.py`, `firetv_player_adapter.py` | Reference contract validation and asynchronous player gate |
| `continuity_engine.py`, `retest_harness.py` | Checkpoints, receipt validation and bounded reference retests |
| `api_adapter.py`, `static/index.html` | Local API and operator console |
| `fireos/` | Native Android TV / Fire OS app, benign media and Kotlin tests |
| `tests/`, `test_*.py`, `tools/` | Portable fixtures, regression checks and demonstration utilities |

See `API_HANDOFF.md` for API details and `AWS_SETUP.md` for optional cloud configuration. Historical engineering ledgers may describe earlier local runs; current verification is identified above and in the commit's workflow artifacts.

## License

Apache License 2.0. See [LICENSE](LICENSE). Preserve dependency licenses and bundled media provenance when redistributing the application.
