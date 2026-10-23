"""Install a console handoff ZIP into an installed debug app on a dedicated emulator."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--serial', default='emulator-5554')
    opts = parser.parse_args()
    if not opts.serial.startswith('emulator-'):
        parser.error('Use a dedicated emulator serial')
    adb_bin = shutil.which('adb')
    if not adb_bin:
        parser.error('Android SDK platform-tools/adb must be on PATH')
    required = {'content.ts', 'media.m3u8', 'context.vtt', 'native-release.json'}
    with tempfile.TemporaryDirectory(prefix='fireshield-install-') as directory:
        folder = Path(directory)
        with zipfile.ZipFile(opts.package) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not required.issubset(names) or set(names) - required - {'README.txt'}:
                raise ValueError('Unexpected handoff archive contents')
            if sum(i.file_size for i in archive.infolist()) > 32 * 1024 * 1024:
                raise ValueError('Handoff archive exceeds 32 MB')
            for name in required:
                (folder / name).write_bytes(archive.read(name))
        release = json.loads((folder / 'native-release.json').read_text(encoding='utf-8'))
        for name, field in [('content.ts', 'asset_revision_id'), ('media.m3u8', 'rendition_playlist_sha256')]:
            actual = 'sha256:' + hashlib.sha256((folder / name).read_bytes()).hexdigest()
            if actual != release[field]:
                raise ValueError('Handoff media identity mismatch: ' + name)
        def adb(*args):
            return subprocess.run([adb_bin, '-s', opts.serial, *args], check=True, timeout=90)
        package_name = 'com.fireshield.fireos'
        adb('shell', 'am', 'force-stop', package_name)
        adb('shell', 'run-as', package_name, 'rm', '-rf', 'files/fireshield_checkpoints')
        adb('shell', 'run-as', package_name, 'mkdir', '-p', 'files/fireshield-demo')
        for name in sorted(required):
            remote = '/data/local/tmp/fireshield-' + name
            adb('push', str(folder / name), remote)
            adb('shell', 'chmod', '644', remote)
            adb('shell', 'run-as', package_name, 'cp', remote, 'files/fireshield-demo/' + name)
        adb('shell', 'am', 'start', '-W', '-n', package_name + '/.MainActivity',
            '--es', 'fireshield.release_file', '/data/data/' + package_name + '/files/fireshield-demo/native-release.json',
            '--el', 'fireshield.pre_guard_ms', '1000', '--el', 'fireshield.seek_timeout_ms', '10000')
        print('Exact-media debug package installed. Use the native Bypass Seek control to reach the calm tail.')


if __name__ == '__main__':
    main()
