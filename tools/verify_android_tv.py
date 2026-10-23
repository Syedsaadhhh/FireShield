"""Verify the debug harness on a dedicated Android TV emulator. Never targets a physical device."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PKG = "com.fireshield.fireos"
ACTIVITY = PKG + "/.MainActivity"
parser = argparse.ArgumentParser()
parser.add_argument("--serial", default="emulator-5556")
args = parser.parse_args()
if not args.serial.startswith("emulator-"):
    raise SystemExit("This runner is restricted to emulator serials.")
ADB = Path(r"C:\Users\ATEC\AppData\Local\Android\Sdk\platform-tools\adb.exe")
subprocess.run([str(ADB), "connect", "127.0.0.1:5557"], capture_output=True)
EVIDENCE = ROOT / "evidence/2026-10-15-run4/runtime"
EVIDENCE.mkdir(parents=True, exist_ok=True)
results = []

def adb(*arguments, check=True, timeout=90):
    p = subprocess.run([str(ADB), "-s", args.serial, *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if ("device" in p.stderr and "not found" in p.stderr) or "daemon started" in p.stdout or "daemon started" in p.stderr:
        subprocess.run([str(ADB), "connect", "127.0.0.1:5557"], capture_output=True)
        p = subprocess.run([str(ADB), "-s", args.serial, *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("ADB failed: " + " ".join(arguments) + "\n" + p.stdout + p.stderr)
    return p.stdout + p.stderr

def logs():
    return adb("logcat", "-d", "-t", "1000", "-v", "raw", "-s", "FireTrace:I", "FireTraceReceipt:W", "AndroidRuntime:E")

def clear_logs():
    for _ in range(3):
        out = adb("logcat", "-c", check=False)
        if "failed to clear" not in out:
            return
        time.sleep(0.5)

def checkpoint():
    text = adb("shell", "run-as", PKG, "cat", "files/fireshield_checkpoints/continuity_checkpoint.json", check=False)
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
            if isinstance(obj, dict) and "integrity_digest" in obj:
                return obj
        except ValueError:
            continue
    return None

RECOVERY_TIMEOUT_MS = 55000

def wait_for(predicate, timeout=90, description="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError("Timed out waiting for " + description)

def capture(name):
    (EVIDENCE / (name + ".log")).write_text(logs(), encoding="utf-8")
    adb("shell", "screencap", "-p", "/sdcard/fireshield-verification.png", check=False)
    adb("pull", "/sdcard/fireshield-verification.png", str(EVIDENCE / (name + ".png")), check=False)

def launch():
    adb("shell", "am", "start", "-W", "-n", ACTIVITY,
        "--el", "fireshield.seek_timeout_ms", str(RECOVERY_TIMEOUT_MS))

def fresh():
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "run-as", PKG, "rm", "-rf", "files/fireshield_checkpoints", check=False)
    clear_logs()
    launch()
    return wait_for(lambda: (cp if (cp := checkpoint()) and cp["position_ms"] >= 1000
        and cp.get("selected_audio_track_id") and cp.get("selected_subtitle_track_id") else None),
        timeout=75, description="playing fixture with selected caption/audio identities")

def action(name):
    adb("shell", "am", "start", "-W", "--activity-single-top", "-n", ACTIVITY,
        "--es", "fireshield.test_action", name)

def receipt_from_log():
    text = logs()
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
            if isinstance(obj, dict) and "receipt_digest" in obj:
                return obj
        except ValueError:
            continue
    return None

def validate_native_receipt(name):
    receipt = wait_for(receipt_from_log, description="native incident receipt")
    path = EVIDENCE / (name + "_receipt.json")
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    p = subprocess.run([sys.executable, str(ROOT / "retest_harness.py"), "--receipt-file", str(path)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    (EVIDENCE / (name + "_retest.txt")).write_text(p.stdout + p.stderr, encoding="utf-8")
    if p.returncode or "DETECTION_PASS" not in p.stdout:
        raise RuntimeError("Native receipt could not complete its bounded model retest: " + p.stdout + p.stderr)
    return {"mismatches": receipt["mismatch_fields"], "retest_fixture": receipt["retest_fixture_id"],
        "model_retest": "DETECTION_PASS"}

def record(name, work):
    print("RUN " + name, flush=True)
    try:
        detail = work()
        capture(name)
        results.append({"scenario": name, "status": "PASS", "detail": detail})
        print("PASS " + name + " " + json.dumps(detail), flush=True)
    except Exception as exc:
        capture(name + "_failure")
        results.append({"scenario": name, "status": "FAIL", "detail": str(exc)})
        raise
    finally:
        (EVIDENCE / "runtime_results.json").write_text(json.dumps({
            "serial": args.serial, "platform": "Android TV emulator",
            "recovery_timeout_ms": RECOVERY_TIMEOUT_MS, "results": results}, indent=2), encoding="utf-8")

def nominal():
    cp = fresh()
    return {"position_ms": cp["position_ms"], "subtitle_id": cp["selected_subtitle_track_id"],
        "audio_id": cp["selected_audio_track_id"]}

def process_death():
    cp = fresh()
    adb("shell", "am", "force-stop", PKG)
    clear_logs()
    launch()
    text = wait_for(lambda: (t if "RECOVERY_VERIFIED" in (t := logs()) else None), description="process-death recovery verification")
    if "UNEXPECTED_SEEK_CALLBACK" in text:
        raise RuntimeError("Recovery was misrouted into hazard bypass.")
    return {"saved_position_ms": cp["position_ms"], "verification": "RECOVERY_VERIFIED"}

def background():
    fresh()
    clear_logs()
    adb("shell", "input", "keyevent", "3")
    time.sleep(8)
    launch()
    wait_for(lambda: "RECOVERY_VERIFIED" in logs(), timeout=75, description="background/foreground recovery")
    return {"verification": "RECOVERY_VERIFIED"}

def fault(name, expected_field):
    fresh()
    clear_logs()
    action(name)
    wait_for(lambda: "DIVERGENCE DETECTED" in logs() or receipt_from_log(), description=name + " receipt")
    detail = validate_native_receipt(name)
    if expected_field not in detail["mismatches"]:
        raise RuntimeError("Wrong mismatch was reported: " + json.dumps(detail))
    cp1 = checkpoint()
    time.sleep(2)
    if checkpoint() != cp1:
        raise RuntimeError("Checkpoint changed while divergence should be latched.")
    return detail | {"checkpoint_frozen": True}

def readiness_timeout():
    fresh()
    clear_logs()
    action("timeout")
    wait_for(lambda: "RECOVERY_BLOCKED reason=RECOVERY_READINESS_TIMEOUT" in logs(),
        timeout=70, description="fail-closed readiness timeout")
    if "RECOVERY_VERIFIED" in logs():
        raise RuntimeError("Timeout incorrectly released playback.")
    time.sleep(2)
    if "RECOVERY_VERIFIED" in logs():
        raise RuntimeError("Late callback improperly verified recovery after timeout.")
    return {"blocked": "RECOVERY_READINESS_TIMEOUT", "fail_closed_latched": True}

def seek_overshoot():
    fresh()
    adb("shell", "am", "force-stop", PKG)
    clear_logs()
    # Launch with debug seek offset +3000ms
    adb("shell", "am", "start", "-W", "-n", ACTIVITY,
        "--el", "fireshield.seek_timeout_ms", str(RECOVERY_TIMEOUT_MS),
        "--el", "fireshield.test_seek_offset_ms", "3000")
    wait_for(lambda: "DIVERGENCE DETECTED" in logs() or receipt_from_log(), description="seek_overshoot divergence")
    detail = validate_native_receipt("seek_overshoot")
    if "position_ms" not in detail["mismatches"]:
        raise RuntimeError("Position mismatch was not reported: " + json.dumps(detail))
    return detail | {"overshoot_detected": True, "offset_ms": 3000}

def invalid_checkpoint(stale=False):
    cp = fresh()
    adb("shell", "am", "force-stop", PKG)
    if stale:
        cp["asset_revision_id"] = "sha256:" + "0" * 64
        payload = {k: v for k, v in cp.items() if k != "integrity_digest"}
        cp["integrity_digest"] = "sha256:" + hashlib.sha256(json.dumps(payload,
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        reason = "STALE_ASSET_REVISION"
    else:
        cp["position_ms"] += 10000
        reason = "CORRUPT_CHECKPOINT"
    path = EVIDENCE / (reason.lower() + ".json")
    path.write_text(json.dumps(cp), encoding="utf-8")
    adb("push", str(path), "/data/local/tmp/fireshield-checkpoint.json")
    adb("shell", "run-as", PKG, "cp", "/data/local/tmp/fireshield-checkpoint.json",
        "files/fireshield_checkpoints/continuity_checkpoint.json")
    clear_logs()
    launch()
    wait_for(lambda: ("RECOVERY_BLOCKED reason=" + reason) in logs(), description=reason)
    return {"blocked": reason}

try:
    record("nominal_playback", nominal)
    record("process_death_recovery", process_death)
    record("background_foreground", background)
    record("dropped_subtitles", lambda: fault("drop_subtitles", "captions_enabled"))
    record("dropped_audio", lambda: fault("drop_audio", "selected_audio_track_id"))
    record("stale_playlist", lambda: fault("cut_drift", "rendition_playlist_sha256"))
    record("presentation_mismatch", lambda: fault("presentation_mismatch", "presentation_mode"))
    record("seek_overshoot", seek_overshoot)
    record("readiness_timeout", readiness_timeout)
    record("corrupt_checkpoint", lambda: invalid_checkpoint(False))
    record("stale_asset_revision", lambda: invalid_checkpoint(True))
    print("ALL RUNTIME SCENARIOS PASSED", flush=True)
except Exception as exc:
    print("RUNTIME FAILURE: " + str(exc), flush=True)
    raise SystemExit(1)
