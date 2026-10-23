"""Package owned reference frames for an exact-media, headless-to-native drill.

Never opens a video window. The package contains a flashing test interval and
must only be played behind the native publisher gate on a dedicated emulator.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

import numpy as np

from screening_analyzer import build_reference_fixtures, find_ffmpeg_executable

ROOT = pathlib.Path(__file__).resolve().parent
APP_FILES = "/data/data/com.fireshield.fireos/files/fireshield-demo"
VTT = """WEBVTT

arrival
00:00.000 --> 00:01.000
The crew receives an emergency alert.

warning
00:01.000 --> 00:02.000
An emergency warning strobe is active.

shelter
00:02.000 --> 00:30.000
The crew reaches a sheltered corridor.
"""


def prepare_package(service, tenant_id: str, reference_id: str = "ast-hazard-luminance-strobe"):
    """Create a fresh catalog revision from the actual encoded HLS segment."""
    if reference_id != "ast-hazard-luminance-strobe":
        raise ValueError("Only the owned luminance reference has a packaged native drill")
    service.get_asset(reference_id, tenant_id)  # enforce tenant visibility
    ffmpeg = find_ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to package and screen actual media")
    fixture = next(f for f in build_reference_fixtures()
                   if f["fixture_id"] == "ref_pos_7p5hz_luminance_strobe")
    # A long calm tail allows native bypass, checkpoint and fault verification.
    frames = np.concatenate((fixture["frames"], np.full((810, 64, 64, 3), 20, dtype=np.uint8)))
    identity = hashlib.sha256(frames.tobytes() + b"hls-drill-v1").hexdigest()[:20]
    folder = ROOT / "runtime_data" / "packaged_demo" / identity
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "media.m3u8"
    segment = folder / "content.ts"
    # Rebuild rather than trusting an old cache or stale encoded bytes.
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "rawvideo",
        "-pix_fmt", "rgb24", "-s", "64x64", "-r", "30", "-i", "pipe:0",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "30",
        "-vf", "scale=320:240:flags=neighbor", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-g", "900", "-sc_threshold", "0", "-c:a", "aac",
        "-f", "hls", "-hls_time", "30", "-hls_list_size", "0", "-hls_flags", "single_file",
        "-hls_segment_filename", str(segment), str(manifest)],
        input=frames.tobytes(), capture_output=True, check=True, timeout=45)
    (folder / "context.vtt").write_text(VTT, encoding="utf-8", newline="\n")
    asset = service.import_managed_asset(tenant_id=tenant_id,
        name="Packaged emergency scene | actual encoded media",
        source_path=str(segment.relative_to(ROOT)), subtitles_vtt=VTT)
    asset.duration_ms, asset.fps, asset.width, asset.height = 30000, 30.0, 320, 240
    service._save_to_disk()
    return asset


def packaging_for_asset(asset):
    """Resolve only our generated single-segment package, verifying its bytes."""
    if not asset.source_path:
        return None
    source = (ROOT / asset.source_path).resolve()
    package_root = (ROOT / "runtime_data/packaged_demo").resolve()
    if not source.is_relative_to(package_root) or source.name != "content.ts":
        return None
    actual = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != asset.asset_revision_id:
        raise ValueError("Packaged segment changed after import; rebuild and re-screen")
    manifest = (source.parent / "media.m3u8").read_text(encoding="utf-8")
    entries = [line.strip() for line in manifest.splitlines() if line.strip() and not line.startswith("#")]
    if entries != ["content.ts"]:
        raise ValueError("Unexpected packaged playlist; rebuild the owned reference")
    return {"playlist_manifest": manifest, "media_uri": "file://" + APP_FILES + "/media.m3u8",
            "source_mapping_verified": True,
            "scope": "Actual encoded segment screened; package must be installed in the debug emulator before playback"}


def export_native_release(release, asset, destination: pathlib.Path):
    """Export public verification data and exact media. No private key leaves the service."""
    import shutil
    packaging = packaging_for_asset(asset)
    if not packaging or packaging["playlist_manifest"] != release.playlist_manifest:
        raise ValueError("Release does not match this exact packaged playlist")
    if release.asset_revision_id != asset.asset_revision_id:
        raise ValueError("Release source revision does not match packaged media")
    destination.mkdir(parents=True, exist_ok=True)
    source = ROOT / asset.source_path
    for filename in ("content.ts", "media.m3u8", "context.vtt"):
        shutil.copyfile(source.parent / filename, destination / filename)
    data = {"release_id": release.release_id, "tenant_id": release.tenant_id,
            "asset_revision_id": release.asset_revision_id, "rendition_set_id": release.rendition_set_id,
            "rendition_playlist_sha256": release.rendition_playlist_sha256,
            "media_uri": "file://" + APP_FILES + "/media.m3u8",
            "subtitle_uri": "file://" + APP_FILES + "/context.vtt",
            "sidecar_json": release.sidecar_json,
            "public_verification_metadata": {"key_id": release.key_id, "public_key_hex": release.public_key_hex}}
    (destination / "native-release.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
