"""Verify one encoded source through screening, native context gate and FireTrace.

Dedicated debug emulator only. Screenshots start only after a pre-interval
pause and blank have been verified. The source fixture is never previewed.
"""
from contextlib import contextmanager
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packaged_demo import APP_FILES, export_native_release, packaging_for_asset, prepare_package
from pipeline_service import LocalPipelineService, PipelineServiceError
from state_engine import PlaybackSessionContext, SidecarContractValidator
import verify_android_tv as tv


@contextmanager
def record_native_clip(out, serial):
    remote = '/sdcard/connected_scene.mp4'
    recorder = subprocess.Popen([tv.ADB, '-s', serial, 'shell', 'screenrecord',
        '--size', '1280x720', '--bit-rate', '1500000', '--time-limit', '60', remote],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(2)
        yield
    finally:
        tv.adb('shell', 'pkill', '-INT', 'screenrecord', check=False)
        try:
            recorder.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            recorder.terminate()
            recorder.communicate(timeout=5)
        tv.adb('pull', remote, str(out / 'connected_scene.mp4'), check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', default='emulator-5554')
    parser.add_argument('--output', type=Path, default=Path('evidence/connected-native'))
    opts = parser.parse_args()
    if not opts.serial.startswith('emulator-'):
        parser.error('A dedicated emulator serial is required')
    tv.ADB = shutil.which('adb')
    if not tv.ADB:
        parser.error('adb is required')
    tv.args = SimpleNamespace(serial=opts.serial)
    out = opts.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tv.EVIDENCE = out
    tenant = 'publisher-demo-tenant'
    service = LocalPipelineService(store_path=out / 'controlled-review-store.json')
    asset = prepare_package(service, tenant)
    job = service.start_screening_job(tenant_id=tenant, asset_id=asset.asset_id)
    if job.status != 'COMPLETED':
        raise RuntimeError(job.error_message)
    report = service.get_screening_report(asset.asset_revision_id, tenant)
    assert report['findings'], 'Encoded reference must produce a screening candidate'
    packaging = packaging_for_asset(asset)
    args = dict(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
                rendition_set_id='connected-owned-hls', playlist_manifest=packaging['playlist_manifest'],
                media_uri=packaging['media_uri'])
    try:
        service.publish_release(**args)
        raise AssertionError('Unreviewed publication was allowed')
    except PipelineServiceError:
        pass
    # This controlled harness acts as the reviewer; normal console review is explicit.
    for finding in report['findings']:
        service.ground_context_for_interval(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
                                           interval_id=finding['interval_id'])
        service.approve_context_note(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
                                    interval_id=finding['interval_id'], reviewer_id='controlled-demo-editor')
    service.submit_editorial_decision(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
        reviewer_id='controlled-demo-editor', decision='approved', notes='Owned fixture in a dedicated emulator drill.')
    release = service.publish_release(**args)
    SidecarContractValidator.validate(release.sidecar_json,
        PlaybackSessionContext(tenant_id=tenant, asset_revision_id=asset.asset_revision_id,
            rendition_set_id=release.rendition_set_id, rendition_playlist_sha256=release.rendition_playlist_sha256,
            timebase='presentation_ms'), trusted_public_keys={release.key_id: release.public_key_hex})
    package = out / 'package'
    export_native_release(release, asset, package)
    (out / 'screening_report.json').write_text(json.dumps(report, indent=2))
    tv.adb('shell', 'am', 'force-stop', tv.PKG)
    tv.adb('shell', 'run-as', tv.PKG, 'rm', '-rf', 'files/fireshield_checkpoints')
    tv.adb('shell', 'run-as', tv.PKG, 'mkdir', '-p', 'files/fireshield-demo')
    for filename in ('content.ts', 'media.m3u8', 'context.vtt', 'native-release.json'):
        remote = '/data/local/tmp/fireshield-' + filename
        tv.adb('push', str(package / filename), remote)
        tv.adb('shell', 'chmod', '644', remote)
        tv.adb('shell', 'run-as', tv.PKG, 'cp', remote, 'files/fireshield-demo/' + filename)
    tv.clear_logs()
    tv.adb('shell', 'am', 'start', '-W', '-n', tv.ACTIVITY,
        '--es', 'fireshield.release_file', APP_FILES + '/native-release.json',
        '--el', 'fireshield.pre_guard_ms', '1000', '--el', 'fireshield.seek_timeout_ms', '10000')

    def snapshot():
        tv.action('snapshot')
        raw = tv.adb('logcat', '-d', '-t', '300', '-v', 'raw', '-s', 'FireShieldDemo:I')
        for line in reversed(raw.splitlines()):
            try:
                value = json.loads(line)
                if isinstance(value, dict) and 'surface_blanked' in value:
                    return value
            except ValueError:
                continue
        return None

    held = tv.wait_for(lambda: (s if (s := snapshot()) and s.get('state') == 'INTERVENING' else None),
                       timeout=45, description='native signed-context intervention')
    assert held['asset_revision_id'] == asset.asset_revision_id
    assert held['surface_blanked'] is True and held['is_playing'] is False
    assert held['position_ms'] < min(f['start_ms'] for f in report['findings'])
    assert 'emergency' in held['status'].lower(), 'Signed context must reach the native view'
    # Only capture after the native gate has proven that the source is blanked.
    with record_native_clip(out, opts.serial):
        tv.capture('connected_context_hold')
        (out / 'context_hold_snapshot.json').write_text(json.dumps(held, indent=2))
        time.sleep(3)
        tv.action('bypass')
        cp = tv.wait_for(lambda: (c if (c := tv.checkpoint()) and c['position_ms'] >= 2300
            and c.get('selected_subtitle_track_id') and c.get('selected_audio_track_id') else None),
            timeout=35, description='verified bypass to calm tail with selected tracks')
        assert cp['asset_revision_id'] == asset.asset_revision_id
        tv.capture('connected_safe_tail')
        time.sleep(3)
        tv.clear_logs()
        tv.action('drop_subtitles')
        receipt = tv.wait_for(tv.receipt_from_log, timeout=30, description='same-source native caption-loss receipt')
        assert 'captions_enabled' in receipt['mismatch_fields']
        # Receipt v1 carries edition identity inside expected/observed states.
        # The captured checkpoint above supplies its source-revision linkage.
        assert receipt['expected_state']['rendition_playlist_sha256'] == release.rendition_playlist_sha256
        assert receipt['observed_state']['rendition_playlist_sha256'] == release.rendition_playlist_sha256
        detail = tv.validate_native_receipt('connected_caption_loss')
        fault = snapshot()
        assert fault['surface_blanked'] is True and fault['is_playing'] is False
        time.sleep(3)
        tv.capture('connected_caption_loss')
        (out / 'fault_snapshot.json').write_text(json.dumps(fault, indent=2))
    manifest = {'status': 'PASS', 'platform': 'Android TV API 28 emulator',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'asset_revision_id': asset.asset_revision_id, 'release_id': release.release_id,
        'rendition_playlist_sha256': release.rendition_playlist_sha256,
        'encoded_frames_screened': report['total_frames'], 'candidate_count': len(report['findings']),
        'signed_context_reached_native': True, 'pre_interval_hold': held,
        'receipt_linkage': 'Expected and observed playlist identities match the release; checkpoint binds the source revision',
        'native_bypass_checkpoint': cp, 'native_receipt_retest': detail,
        'output_verified': False, 'scope': 'Controlled debug emulator. Player selection verification, not physical-device or rendered-output certification.',
        'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in package.iterdir() if p.is_file()}}
    (out / 'connected_result.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    main()
