# Reproduce and interpret verification

Use Python 3.12, FFmpeg on PATH, JDK 17, and Android SDK platform 36.1 for the checked-in build. Install Python dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m pytest -q`. Native checks: `cd fireos` followed by `bash gradlew testDebugUnitTest assembleDebug assembleRelease --console=plain` (Windows: `gradlew.bat`). CI runs these independently and retains reports/APKs as artifacts associated with the source SHA. CI compilation does not establish emulator or device behavior.

The pre-review `e90eb7e` push reports 58 Python tests, 22 JVM tests, and 11 emulator scenarios. Raw runtime evidence is maintained outside git. Treat these as the original team's reported results unless the corresponding artifacts are supplied or the commands are rerun. Historical reports used a 55-second recovery deadline, not the app's three-second default.

## Receipt boundary

The CLI requires a full v1 envelope including its digest; missing digests, duplicate keys, unknown fields, invalid types, oversized input, and inconsistent scenario mappings are rejected. A digest is consistency checking, not sender authentication. The format is constrained sorted-key JSON with ASCII-escaped strings and integer-only numeric fields. It is **not** general RFC 8785/JCS. Publisher sidecar signing bytes have not been changed. A shared Python/JVM fixture covers Unicode (including supplementary characters), slash-containing track IDs, and nulls.

`python retest_harness.py --receipt-file fireos/app/src/test/resources/receipt-v1-unicode.json` is a portable model smoke test. Results explicitly identify `REFERENCE_MODEL_ONLY`. `DETECTION_PASS` means the model detected the seeded failure class. `MODEL_PASS` means benign model inputs matched. Neither means a native player was fixed.

## Emulator run

Install the debug APK on a dedicated Android TV emulator and expose platform-tools on PATH:

```sh
python tools/verify_android_tv.py --serial emulator-5556
```

Use `--adb /absolute/path/to/adb` when PATH does not provide it. No hardcoded workstation path or TCP connection is used. Each run gets a new output directory under `evidence/`; `--output-dir` can override it. Existing completed run directories are rejected to preserve evidence.

The default recovery deadline is 3000 ms. To reproduce the historical slow software-decoder configuration, explicitly pass `--recovery-timeout-ms 55000`. Report this setting with results; a pass at 55 seconds is not evidence of acceptable recovery latency at three seconds.

The runner resets this app's local fixture checkpoint and force-stops it. `force_stop_relaunch` is precisely that, **not** an OS low-memory process-death test. Home/re-entry is a separate scenario. A real background-process-kill test with PID evidence remains to be added.

The application now logs `evidence=PLAYER_SELECTION outputVerified=false`. Track selection and first video-frame readiness do not prove caption rendering, audible output, timing correctness, or zero-frame exposure. Screenshots are captured but are not automatically asserted against a caption-output oracle. Python receipt retesting remains a bounded model, not native replay.

## Release trust boundary

The launcher must remain exported for TV navigation. Intent overrides (including trusted public keys, sidecars, media identity and timeouts) are now read only for a debuggable app; release builds use packaged configuration. Debug builds are diagnostic harnesses, not trusted production applications. A production integration needs host-controlled publisher trust and authenticated policy distribution.

Checkpoint persistence no longer falls back to a non-atomic overwrite if rename fails; it preserves the prior file and raises an I/O failure. Graceful low-storage recovery and multi-profile persistence remain follow-up work.

## Remaining integration gates

- Independent caption/audio output checks and a native failing-before/passing-after regression.
- Semantic language/role restoration beyond the current English fixture and exact format IDs.
- Trusted media-byte inventory beyond playlist identity; policy expiry and rollback protection.
- Authenticated tenant boundaries: local record filtering alone is not a service authorization system.
- AWS readiness currently checks a subset of resources; an ACTIVE state machine is not proof of deployed Batch/Lambda/media workers or a complete cloud flow.
- Current emulator rerun, physical Fire OS validation before deployment, latency and false-hold measurement, and customer integration evidence.

The full market/research audit remains outside git, following repository instructions. These changes repair concrete correctness, trust and reproducibility gaps; they do not expand UI or claim a production deployment.
