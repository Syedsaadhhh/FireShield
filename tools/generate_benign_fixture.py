"""Generate the publisher-owned benign runtime fixture; development keys are fixtures only."""
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from state_engine import build_signed_v2_sidecar_json, derive_ed25519_public_key_hex, SidecarContractValidator, PlaybackSessionContext

assets = ROOT / "fireos/app/src/main/assets/hls"
duration = 120
segment = assets / "benign_seg0.ts"
subprocess.run([
    "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
    f"color=c=#0B132B:s=320x240:d={duration}:r=24",
    "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}",
    "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
    "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level", "3.0",
    "-g", "48", "-c:a", "aac", "-b:a", "64k", "-f", "mpegts", str(segment)
], check=True)
cues = ["FireShield caption test: publisher-owned synthetic media.",
        "Captions and audio selection should survive an interruption.",
        "This calm test clip contains no flashing images.",
        "Playback diagnostics below report the actual verification result."]
vtt = "WEBVTT\n\n"
for i, start in enumerate(range(0, duration, 5)):
    end = start + 5
    vtt += f"{start//60:02d}:{start%60:02d}.000 --> {end//60:02d}:{end%60:02d}.000\n{cues[i % len(cues)]}\n\n"
(assets / "subtitles_en.vtt").write_text(vtt, encoding="utf-8", newline="\n")
variant = f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:{duration}\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:{duration:.6f},\nbenign_seg0.ts\n#EXT-X-ENDLIST\n"
(assets / "benign_variant.m3u8").write_text(variant, encoding="utf-8", newline="\n")
(assets / "subtitles_en.m3u8").write_text(variant.replace("benign_seg0.ts", "subtitles_en.vtt"), encoding="utf-8", newline="\n")
(assets / "benign_master.m3u8").write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="en",URI="subtitles_en.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=264000,RESOLUTION=320x240,SUBTITLES="subs"\nbenign_variant.m3u8\n', encoding="utf-8", newline="\n")
asset_sha = "sha256:" + hashlib.sha256(segment.read_bytes()).hexdigest()
playlist_sha = "sha256:" + hashlib.sha256((assets / "benign_variant.m3u8").read_bytes()).hexdigest()
seed = hashlib.sha256(b"fireshield-run4-development-fixture-key-only").digest()
key_id = "fixture-ed25519-dev-only-run4"
public_key = derive_ed25519_public_key_hex(seed)
tenant = "tenant-synthetic-fixture"
rendition = "hls-benign-h264-320x240"
detector = "fireshield-screening-proto:0.3.0-sdr-bt709-2dfft"
provenance = {
    "provenance": "Locally generated constant navy H.264/AAC test clip; silent stereo track; WebVTT captions",
    "license": "Apache-2.0 publisher-owned synthetic fixture",
    "fixture_id": "benign_calm_presentation_with_subtitles", "fixture_only": True,
    "duration_ms": duration * 1000, "presentation_mode": "standard",
    "tenant_id": tenant, "asset_revision_id": asset_sha, "rendition_set_id": rendition,
    "rendition_playlist_sha256": playlist_sha, "signing_alg": "Ed25519",
    "key_id": key_id, "public_key_hex": public_key, "detector_version": detector,
    "subtitles_track": "subtitles_en.vtt", "master_playlist": "benign_master.m3u8"
}
(assets / "PROVENANCE_BENIGN.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
sidecar = build_signed_v2_sidecar_json(tenant_id=tenant, asset_revision_id=asset_sha,
    rendition_set_id=rendition, rendition_playlist_sha256=playlist_sha,
    detector_version=detector, review_state="approved", intervals=[], key_id=key_id,
    private_key_seed_32b=seed, zero_findings_attested=True)
(assets / "benign_sidecar_v2.json").write_text(sidecar, encoding="utf-8")
SidecarContractValidator.validate(sidecar, session=PlaybackSessionContext(
    tenant_id=tenant, asset_revision_id=asset_sha, rendition_set_id=rendition,
    rendition_playlist_sha256=playlist_sha), trusted_public_keys={key_id: public_key},
    allow_legacy_v1_hmac=False)
print(json.dumps({"duration_s": duration, "bytes": segment.stat().st_size,
    "asset_sha": asset_sha, "playlist_sha": playlist_sha, "fixture_only": True}))
