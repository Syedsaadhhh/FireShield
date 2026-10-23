"""Record actual Android TV emulator output. Requires the dedicated debug fixture app."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import signal
import subprocess
import time
from types import SimpleNamespace

import verify_android_tv as tv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='emulator-5554')
    parser.add_argument('--output', type=Path, default=Path('evidence/cloud-demo'))
    opts = parser.parse_args()
    if not opts.serial.startswith('emulator-'):
        parser.error('Only a dedicated emulator may be used.')
    tv.ADB = shutil.which('adb')
    if not tv.ADB:
        parser.error('adb is required')
    tv.args = SimpleNamespace(serial=opts.serial)
    tv.EVIDENCE = opts.output.resolve()
    tv.EVIDENCE.mkdir(parents=True, exist_ok=True)
    tv.RECOVERY_TIMEOUT_MS = 10000
    tv.results.clear()
    manifest = {
        'platform': 'Android TV emulator',
        'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'device_fingerprint': tv.adb('shell', 'getprop', 'ro.build.fingerprint').strip(),
        'scope': 'Actual native debug harness; controlled fault injection; selection verification, not rendered-output certification',
        'recovery_timeout_ms': tv.RECOVERY_TIMEOUT_MS,
        'clips': [],
    }
    failed = False
    scenarios = [
        ('native_playback', None, None),
        ('native_recovery', 'recreate_player', None),
        ('native_caption_fault', 'drop_subtitles', 'captions_enabled'),
        ('native_stale_edition', 'cut_drift', 'rendition_playlist_sha256'),
    ]
    for name, action, field in scenarios:
        recorder = None
        remote = '/sdcard/' + name + '.mp4'
        try:
            tv.fresh()
            tv.clear_logs()
            recorder = subprocess.Popen([tv.ADB, '-s', opts.serial, 'shell', 'screenrecord',
                '--size', '1280x720', '--bit-rate', '1500000', '--time-limit', '45', remote],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(2)
            if action:
                tv.action(action)
                if field:
                    receipt = tv.wait_for(tv.receipt_from_log, timeout=30, description=name)
                    if field not in receipt['mismatch_fields']:
                        raise RuntimeError('Expected native mismatch was not detected')
                    detail = tv.validate_native_receipt(name)
                else:
                    tv.wait_for(lambda: 'RECOVERY_VERIFIED' in tv.logs(), timeout=30,
                                description='native recovery verification')
                    detail = {'recovery': 'PLAYER_SELECTION_MATCH', 'output_verified': False}
            else:
                detail = {'playback': 'native Media3 ExoPlayer', 'output_verified': False}
            time.sleep(8)
            tv.capture(name)
            tv.results.append({'scenario': name, 'status': 'PASS', 'detail': detail})
        except Exception as exc:
            failed = True
            tv.results.append({'scenario': name, 'status': 'FAIL', 'detail': str(exc)})
            tv.capture(name + '_failure')
        finally:
            if recorder is not None:
                tv.adb('shell', 'pkill', '-INT', 'screenrecord', check=False)
                try:
                    recorder.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    recorder.terminate()
                    recorder.communicate(timeout=5)
                tv.adb('pull', remote, str(tv.EVIDENCE / (name + '.mp4')), check=False)
                clip = tv.EVIDENCE / (name + '.mp4')
                if clip.exists() and clip.stat().st_size:
                    manifest['clips'].append({'file': clip.name, 'sha256': hashlib.sha256(clip.read_bytes()).hexdigest()})
                else:
                    failed = True
            (tv.EVIDENCE / 'runtime_results.json').write_text(json.dumps(tv.results, indent=2))
            (tv.EVIDENCE / 'run_manifest.json').write_text(json.dumps(manifest, indent=2))
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
