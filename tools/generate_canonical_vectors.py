#!/usr/bin/env python3
"""
Generates shared cross-platform canonical JSON test vectors for Python and Kotlin verification.
"""

import json
import hashlib
import pathlib

VECTORS = [
    {
        "name": "slash_and_colon",
        "description": "Forward slash preservation (no \\/ escaping) and colon delimiters",
        "input": {
            "audio_track": "0:1/15",
            "path": "hls/variants/720p/stream.m3u8",
        },
    },
    {
        "name": "backslash_and_quote",
        "description": "Proper escaping of quotes and backslashes per RFC 8259",
        "input": {
            "greeting": 'hello "world"',
            "windows_path": "C:\\Program Files\\FireShield",
        },
    },
    {
        "name": "whitespace_control_escapes",
        "description": "Standard JSON whitespace control character escapes (\\n, \\t, \\r)",
        "input": {
            "escapes": "line1\nline2\ttabbed\r\nreturn",
        },
    },
    {
        "name": "unicode_latin_supplement",
        "description": "Direct UTF-8 emission of Latin-1 supplement accented characters",
        "input": {
            "title": "café",
            "role": "naïve",
            "city": "münchen",
            "resume": "résumé",
        },
    },
    {
        "name": "unicode_cjk",
        "description": "Direct UTF-8 emission of multi-byte CJK ideographs",
        "input": {
            "japanese": "東京都",
            "chinese": "你好世界",
            "korean": "안녕하세요",
        },
    },
    {
        "name": "unicode_rtl",
        "description": "Direct UTF-8 emission of right-to-left Arabic and Hebrew scripts",
        "input": {
            "arabic": "مرحبا بالعالم",
            "hebrew": "שלום עולם",
        },
    },
    {
        "name": "non_bmp_emoji",
        "description": "Direct UTF-8 emission of 4-byte SMP characters (emojis and Gothic letters)",
        "input": {
            "badge": "FireShield 🛡️",
            "tv": "4K TV 📺",
            "ancient_gothic": "𐍈",
        },
    },
    {
        "name": "nested_key_ordering",
        "description": "Lexicographic sorting of keys at all nesting levels without whitespace",
        "input": {
            "z": 100,
            "m": {"c": 3, "b": 2, "a": 1},
            "a": [3, 2, 1],
        },
    },
    {
        "name": "primitives_and_null",
        "description": "Exact JSON serialization for booleans, null, zero, negative int, and floats",
        "input": {
            "bool_true": True,
            "bool_false": False,
            "null_val": None,
            "zero": 0,
            "negative_int": -42,
            "float_val": 3.14159,
        },
    },
    {
        "name": "empty_structures",
        "description": "Empty objects, arrays, and strings with compact delimiters",
        "input": {
            "empty_array": [],
            "empty_object": {},
            "empty_string": "",
        },
    },
    {
        "name": "actual_receipt_structure",
        "description": "Full schema-compliant incident receipt dictionary",
        "input": {
            "receipt_schema_version": 1,
            "incident_id": "inc-vector-001",
            "fixture_id": "DROPPED_CAPTION_FAULT_V1",
            "timestamp_iso": "2026-10-19T00:00:00Z",
            "environment": {
                "app_version": "0.2.0",
                "platform": "FireOS-Android-Leanback",
            },
            "recovery_status": "DIVERGENCE_DETECTED",
            "diagnostic_code": "ERR_MISMATCH_CAPTIONS_ENABLED",
            "mismatch_fields": ["captions_enabled"],
            "position_delta_ms": 342,
            "expected_state": {
                "position_ms": 2090,
                "captions_enabled": True,
                "selected_subtitle_track_id": "1:sub-en",
                "selected_audio_track_id": "0:1/15",
                "presentation_mode": "standard",
                "rendition_playlist_sha256": "sha256:bc13af2d7d20d72951c17c8a5e69e0e6cd660ed0771cd4849a3323603f484230",
            },
            "observed_state": {
                "position_ms": 2432,
                "captions_enabled": False,
                "selected_subtitle_track_id": None,
                "selected_audio_track_id": "0:1/15",
                "presentation_mode": "standard",
                "rendition_playlist_sha256": "sha256:bc13af2d7d20d72951c17c8a5e69e0e6cd660ed0771cd4849a3323603f484230",
            },
            "privacy_attestation": {
                "contains_pii": False,
                "contains_video_buffers": False,
                "contains_auth_tokens": False,
            },
            "retest_fixture_id": "DROPPED_CAPTION_FAULT_V1",
        },
    },
]


def canonicalize(val):
    return json.dumps(val, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main():
    output_vectors = []
    for item in VECTORS:
        canonical_str = canonicalize(item["input"])
        canonical_bytes = canonical_str.encode("utf-8")
        digest = f"sha256:{hashlib.sha256(canonical_bytes).hexdigest()}"
        output_vectors.append({
            "name": item["name"],
            "description": item["description"],
            "input": item["input"],
            "expected_canonical": canonical_str,
            "expected_sha256": digest,
        })

    out_path = pathlib.Path(__file__).parent / "canonical_vectors.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_vectors, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(output_vectors)} vectors to {out_path}")


if __name__ == "__main__":
    main()
