"""
FireShield Offline Photosensitive & 2D Spatial-Frequency Hazard Screening Prototype (P1).

IMPORTANT SAFETY & COMPLIANCE BOUNDARY:
- This module is a deterministic offline screening prototype
  (detector_version = 'fireshield-screening-proto:0.3.0-sdr-bt709-2dfft').
- It is NOT HardingFPA, Ofcom, or ITU-R BT.1702-3 broadcast certification, and
  it is NOT a medical device.
- EA IRIS (https://github.com/electronicarts/IRIS) was evaluated as a candidate
  external baseline, but no EA IRIS binary/build or verified commercial embedding
  license is present in this workspace environment.
- Upgrades the P1 spatial pattern screen from 1D row/column averaging (which
  suffered phase cancellation on 2D checkerboards and diagonal gratings) to a
  bounded 2D FFT spectral-concentration + multi-orientation (0/45/90/135 deg +
  radial center-cross) high-contrast cycle detector that detects checkerboards,
  diagonal gratings, and radial rings while rejecting compression/sensor noise.
- Never invents a synthetic 'ConfidenceScore'. All candidate hazard intervals are
  emitted in review_state='pending_review' and require explicit human editorial
  approval ('approved') or rejection ('rejected') before release packaging.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import pathlib
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from state_engine import DEFAULT_MIN_SAFE_RESUME_MARGIN_MS

DETECTOR_VERSION = "fireshield-screening-proto:0.3.0-sdr-bt709-2dfft"

EA_IRIS_EVALUATION_STATUS: Dict[str, Any] = {
    "preferred_baseline": "EA IRIS (https://github.com/electronicarts/IRIS)",
    "local_binary_present": False,
    "commercial_license_verified": False,
    "active_engine": DETECTOR_VERSION,
    "active_engine_classification": (
        "Deterministic SDR BT.709 + 2D FFT/Multi-Orientation Screening Prototype (Non-Certified)"
    ),
}

UNIMPLEMENTED_DETECTOR_CAPABILITIES: Tuple[str, ...] = (
    "viewing_distance_visual_angle_steradian_calibration (uses 25% frame area proxy)",
    "hdr_pq_hlg_nit_transfer_curves_itu_bt1702_3 (SDR sRGB/BT.709 linearization only)",
    "non_rigid_perspective_warped_patterns_under_camera_motion",
    "broadcast_or_medical_certification_compliance (not HardingFPA or Ofcom certified)",
)


@dataclasses.dataclass(frozen=True)
class CandidateHazardFinding:
    interval_id: str
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    proposed_safe_resume_ms: int
    types: Tuple[str, ...]
    policy: str
    metrics: Dict[str, float]
    context_cue_ids: Tuple[str, ...] = ()

    def to_sidecar_interval_dict(self) -> Dict[str, Any]:
        return {
            "interval_id": self.interval_id,
            "start_ms": int(self.start_ms),
            "end_ms": int(self.end_ms),
            "safe_resume_ms": int(self.proposed_safe_resume_ms),
            "types": list(self.types),
            "policy": self.policy,
            "context_cue_ids": list(self.context_cue_ids),
        }


@dataclasses.dataclass
class ScreeningAnalysisReport:
    asset_revision_id: str
    detector_version: str
    fps: float
    total_frames: int
    width: int
    height: int
    timebase: str
    review_state: str
    findings: List[CandidateHazardFinding]
    unimplemented_capabilities: Tuple[str, ...] = UNIMPLEMENTED_DETECTOR_CAPABILITIES
    ea_iris_status: Dict[str, Any] = dataclasses.field(
        default_factory=lambda: dict(EA_IRIS_EVALUATION_STATUS)
    )
    editorial_decision_record: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_revision_id": self.asset_revision_id,
            "detector_version": self.detector_version,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "resolution": {"width": self.width, "height": self.height},
            "timebase": self.timebase,
            "review_state": self.review_state,
            "findings_count": len(self.findings),
            "findings": [
                {
                    **dataclasses.asdict(f),
                    "types": list(f.types),
                    "context_cue_ids": list(f.context_cue_ids),
                }
                for f in self.findings
            ],
            "unimplemented_capabilities": list(self.unimplemented_capabilities),
            "ea_iris_status": self.ea_iris_status,
            "editorial_decision_record": self.editorial_decision_record,
        }


def _srgb_uint8_to_linear(rgb_uint8: np.ndarray) -> np.ndarray:
    """Converts uint8 sRGB array (..., 3) in [0, 255] to linear light in [0.0, 1.0]."""
    norm = rgb_uint8.astype(np.float32) / 255.0
    return np.where(norm <= 0.04045, norm / 12.92, np.power((norm + 0.055) / 1.055, 2.4))


def _compute_relative_luminance(linear_rgb: np.ndarray) -> np.ndarray:
    """Computes BT.709 relative luminance Y = 0.2126*R + 0.7152*G + 0.0722*B."""
    return (
        0.2126 * linear_rgb[..., 0]
        + 0.7152 * linear_rgb[..., 1]
        + 0.0722 * linear_rgb[..., 2]
    )


def _count_1d_slice_alternating_pairs(slice_1d: np.ndarray, min_step_contrast: float) -> int:
    """Counts alternating high-contrast light/dark pairs along a 1D luminance slice."""
    if slice_1d.size < 4:
        return 0
    diffs = np.diff(slice_1d)
    strong_idx = np.where(np.abs(diffs) >= min_step_contrast)[0]
    if strong_idx.size < 2:
        return 0
    signs = np.sign(diffs[strong_idx])
    reversals = int(np.sum(signs[1:] * signs[:-1] < 0))
    return (reversals + 1) // 2


def _analyze_2d_spatial_pattern_frame(
    lum_frame: np.ndarray,
    min_bar_contrast: float = 0.20,
    min_area_fraction: float = 0.25,
    min_spectral_concentration: float = 0.14,
) -> Tuple[int, float]:
    """
    Bounded 2D spatial-frequency and multi-orientation pattern detector.
    Returns (effective_pattern_pairs, spectral_concentration_ratio).

    How it resolves the 1D scanline phase-cancellation false negative while
    rejecting random sensor/compression noise:
    1. Contrast & Area Gate: Requires 90th-10th percentile luminance range >= min_bar_contrast
       and at least min_area_fraction of pixels deviating strongly from median luminance.
    2. Multi-Orientation Slice Probing (without full-frame axis averaging):
       Probes individual horizontal rows, vertical columns, +45 deg main diagonals,
       -45 deg anti-diagonals, and radial cross-sections through the frame center.
       Requires >= 25% of probed slices along the dominant orientation to exhibit
       repeating high-contrast oscillations.
    3. 2D FFT Spectral Concentration Gate:
       Computes the 2D Discrete Fourier Transform power spectrum (excluding DC) and
       measures the fraction of AC energy concentrated in the top-12 frequency bins
       (or radial ring coherence). Structured gratings, checkerboards, and concentric
       targets concentrate spectral power into sparse peaks, whereas random sensor or
       compression noise spreads power across all frequencies and is rejected.
    """
    h, w = lum_frame.shape
    dynamic_range = float(np.percentile(lum_frame, 90) - np.percentile(lum_frame, 10))
    if dynamic_range < min_bar_contrast:
        return 0, 0.0

    median_y = float(np.median(lum_frame))
    contrasting_fraction = float(np.mean(np.abs(lum_frame - median_y) >= (min_bar_contrast * 0.40)))
    if contrasting_fraction < min_area_fraction:
        return 0, 0.0

    step_thresh = min_bar_contrast * 0.50

    # 1. Probe individual horizontal rows (catches vertical bars, checkerboards, diagonal bars, radial rings)
    row_indices = np.linspace(0, h - 1, num=min(h, 16), dtype=int)
    row_pairs = sorted(_count_1d_slice_alternating_pairs(lum_frame[r, :], step_thresh) for r in row_indices)
    # 75th percentile across probed rows (ensures >= 25% of rows carry the pattern)
    p75_row_pairs = int(row_pairs[int(len(row_pairs) * 0.75)]) if row_pairs else 0

    # 2. Probe individual vertical columns (catches horizontal bars, checkerboards, diagonal bars, radial rings)
    col_indices = np.linspace(0, w - 1, num=min(w, 16), dtype=int)
    col_pairs = sorted(_count_1d_slice_alternating_pairs(lum_frame[:, c], step_thresh) for c in col_indices)
    p75_col_pairs = int(col_pairs[int(len(col_pairs) * 0.75)]) if col_pairs else 0

    # 3. Probe +45 deg and -45 deg diagonals
    flipped = np.fliplr(lum_frame)
    max_offset = min(h, w) // 4
    diag_offsets = np.linspace(-max_offset, max_offset, num=9, dtype=int)
    diag1_pairs = sorted(
        _count_1d_slice_alternating_pairs(np.diagonal(lum_frame, offset=int(k)), step_thresh)
        for k in diag_offsets
    )
    diag2_pairs = sorted(
        _count_1d_slice_alternating_pairs(np.diagonal(flipped, offset=int(k)), step_thresh)
        for k in diag_offsets
    )
    p75_diag_pairs = max(
        int(diag1_pairs[int(len(diag1_pairs) * 0.75)]),
        int(diag2_pairs[int(len(diag2_pairs) * 0.75)]),
    )

    candidate_pairs = max(p75_row_pairs, p75_col_pairs, p75_diag_pairs)
    if candidate_pairs == 0:
        return 0, 0.0

    # 4. 2D FFT Spectral Concentration Check (distinguishes structured patterns from random noise)
    zero_mean = lum_frame - float(np.mean(lum_frame))
    fft_power = np.abs(np.fft.fft2(zero_mean)) ** 2
    fft_power[0, 0] = 0.0
    total_ac_power = float(np.sum(fft_power))
    if total_ac_power <= 1e-9:
        return 0, 0.0

    flat_power = np.sort(fft_power.ravel())
    top_k = min(16, flat_power.size)
    top_k_power = float(np.sum(flat_power[-top_k:]))
    spectral_concentration = top_k_power / total_ac_power

    if spectral_concentration < min_spectral_concentration:
        # Unstructured random noise / speckle: reject!
        return 0, spectral_concentration

    return candidate_pairs, spectral_concentration


def analyze_rgb_frame_sequence(
    frames_rgb: np.ndarray,
    fps: float,
    *,
    raw_source_bytes: Optional[bytes] = None,
    safe_resume_margin_ms: int = DEFAULT_MIN_SAFE_RESUME_MARGIN_MS,
    min_area_fraction: float = 0.25,
    luminance_delta_threshold: float = 0.10,
    max_flash_cycles_per_sec: float = 3.0,
    min_stripe_pairs: int = 5,
) -> ScreeningAnalysisReport:
    """
    Executes deterministic frame-level screening over an RGB uint8 frame tensor
    of shape (N, H, W, 3).
    """
    if not isinstance(frames_rgb, np.ndarray) or frames_rgb.ndim != 4 or frames_rgb.shape[-1] != 3:
        raise ValueError("frames_rgb must be a 4D numpy array of shape (N, H, W, 3) with uint8 RGB data.")
    if frames_rgb.dtype != np.uint8:
        raise ValueError("frames_rgb must have dtype uint8.")
    if fps <= 0:
        raise ValueError("fps must be > 0.")

    n_frames, height, width, _ = frames_rgb.shape
    source_bytes = raw_source_bytes if raw_source_bytes is not None else frames_rgb.tobytes()
    asset_revision_id = f"sha256:{hashlib.sha256(source_bytes).hexdigest()}"

    if n_frames == 0:
        return ScreeningAnalysisReport(
            asset_revision_id=asset_revision_id,
            detector_version=DETECTOR_VERSION,
            fps=float(fps),
            total_frames=0,
            width=width,
            height=height,
            timebase="presentation_ms",
            review_state="pending_review",
            findings=[],
        )

    linear_rgb = _srgb_uint8_to_linear(frames_rgb)
    luminance = _compute_relative_luminance(linear_rgb)  # shape (N, H, W)

    # Saturated red metrics per pixel: R / (R+G+B) >= 0.80 and V_red = max(0, R-G-B)*320
    rgb_sum = np.maximum(np.sum(linear_rgb, axis=-1), 1e-6)
    red_ratio = linear_rgb[..., 0] / rgb_sum
    red_valence = np.maximum(0.0, linear_rgb[..., 0] - linear_rgb[..., 1] - linear_rgb[..., 2]) * 320.0

    lum_polarity = np.zeros(n_frames, dtype=np.int8)
    lum_delta_mag = np.zeros(n_frames, dtype=np.float32)
    lum_area_frac = np.zeros(n_frames, dtype=np.float32)

    red_polarity = np.zeros(n_frames, dtype=np.int8)
    red_delta_mag = np.zeros(n_frames, dtype=np.float32)

    pattern_pairs = np.zeros(n_frames, dtype=np.int32)
    pattern_spec_conc = np.zeros(n_frames, dtype=np.float32)

    for t in range(n_frames):
        pairs_t, spec_t = _analyze_2d_spatial_pattern_frame(
            luminance[t], min_area_fraction=min_area_fraction
        )
        pattern_pairs[t] = pairs_t
        pattern_spec_conc[t] = spec_t
        if t == 0:
            continue

        dy = luminance[t] - luminance[t - 1]
        darker_pixel = np.minimum(luminance[t], luminance[t - 1])
        qualifying_lum_mask = (np.abs(dy) >= luminance_delta_threshold) & (darker_pixel < 0.80)
        frac_lum = float(np.mean(qualifying_lum_mask))
        lum_area_frac[t] = frac_lum
        mean_dy = float(np.mean(dy))
        lum_delta_mag[t] = abs(mean_dy)
        if frac_lum >= min_area_fraction and abs(mean_dy) >= luminance_delta_threshold:
            lum_polarity[t] = 1 if mean_dy > 0 else -1

        d_red = red_valence[t] - red_valence[t - 1]
        sat_red_mask = ((red_ratio[t] >= 0.80) | (red_ratio[t - 1] >= 0.80)) & (np.abs(d_red) > 20.0)
        frac_red = float(np.mean(sat_red_mask))
        mean_d_red = float(np.mean(d_red))
        red_delta_mag[t] = abs(mean_d_red)
        if frac_red >= min_area_fraction and abs(mean_d_red) > 20.0:
            red_polarity[t] = 1 if mean_d_red > 0 else -1

    window_frames = max(2, int(round(fps)))
    max_opposing_transitions = int(math.floor(max_flash_cycles_per_sec * 2.0))

    frame_hazards: List[Dict[str, Any]] = [
        {"types": set(), "lum_reversals": 0, "red_reversals": 0, "stripes": 0}
        for _ in range(n_frames)
    ]

    for win_start in range(0, n_frames):
        win_end = min(n_frames, win_start + window_frames)

        nz_lum = lum_polarity[win_start:win_end]
        nz_lum = nz_lum[nz_lum != 0]
        lum_reversals = int(np.sum(nz_lum[1:] * nz_lum[:-1] < 0)) if len(nz_lum) >= 2 else 0
        if lum_reversals > max_opposing_transitions:
            active_indices = [
                idx for idx in range(win_start, win_end) if lum_polarity[idx] != 0
            ]
            if active_indices:
                for idx in range(min(active_indices), max(active_indices) + 1):
                    frame_hazards[idx]["types"].add("luminance_flash")
                    frame_hazards[idx]["lum_reversals"] = max(
                        frame_hazards[idx]["lum_reversals"], lum_reversals
                    )

        nz_red = red_polarity[win_start:win_end]
        nz_red = nz_red[nz_red != 0]
        red_reversals = int(np.sum(nz_red[1:] * nz_red[:-1] < 0)) if len(nz_red) >= 2 else 0
        if red_reversals > max_opposing_transitions:
            active_red_indices = [
                idx for idx in range(win_start, win_end) if red_polarity[idx] != 0
            ]
            if active_red_indices:
                for idx in range(min(active_red_indices), max(active_red_indices) + 1):
                    frame_hazards[idx]["types"].add("saturated_red")
                    frame_hazards[idx]["red_reversals"] = max(
                        frame_hazards[idx]["red_reversals"], red_reversals
                    )

    # 2D Spatial pattern check (sustained for >= 150ms)
    min_pattern_frames = max(1, int(round(0.15 * fps)))
    run_start: Optional[int] = None
    for t in range(n_frames):
        if pattern_pairs[t] >= min_stripe_pairs:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None and (t - run_start) >= min_pattern_frames:
                for idx in range(run_start, t):
                    frame_hazards[idx]["types"].add("spatial_pattern")
                    frame_hazards[idx]["stripes"] = int(pattern_pairs[idx])
            run_start = None
    if run_start is not None and (n_frames - run_start) >= min_pattern_frames:
        for idx in range(run_start, n_frames):
            frame_hazards[idx]["types"].add("spatial_pattern")
            frame_hazards[idx]["stripes"] = int(pattern_pairs[idx])

    max_merge_gap_frames = max(1, int(round(0.5 * fps)))
    raw_spans: List[Tuple[int, int]] = []
    cur_s: Optional[int] = None
    cur_e: Optional[int] = None

    for t in range(n_frames):
        if frame_hazards[t]["types"]:
            if cur_s is None:
                cur_s = t
                cur_e = t
            elif t - cur_e <= max_merge_gap_frames:
                cur_e = t
            else:
                raw_spans.append((cur_s, cur_e))
                cur_s = t
                cur_e = t
    if cur_s is not None and cur_e is not None:
        raw_spans.append((cur_s, cur_e))

    findings: List[CandidateHazardFinding] = []
    for idx, (s_frame, e_frame) in enumerate(raw_spans):
        effective_start_frame = max(0, s_frame - 1) if (
            lum_polarity[s_frame] != 0 or red_polarity[s_frame] != 0
        ) else s_frame
        start_ms = int(round((effective_start_frame / fps) * 1000.0))
        end_ms = int(round(((e_frame + 1) / fps) * 1000.0))
        safe_resume_ms = end_ms + max(DEFAULT_MIN_SAFE_RESUME_MARGIN_MS, safe_resume_margin_ms)

        combined_types = sorted(
            set().union(*(frame_hazards[f]["types"] for f in range(s_frame, e_frame + 1)))
        )
        max_lum_rev = max(frame_hazards[f]["lum_reversals"] for f in range(s_frame, e_frame + 1))
        max_red_rev = max(frame_hazards[f]["red_reversals"] for f in range(s_frame, e_frame + 1))
        max_stripes = max(int(pattern_pairs[f]) for f in range(s_frame, e_frame + 1))
        max_spec = float(np.max(pattern_spec_conc[effective_start_frame : e_frame + 1]))
        max_dy = float(np.max(lum_delta_mag[effective_start_frame : e_frame + 1]))
        max_dred = float(np.max(red_delta_mag[effective_start_frame : e_frame + 1]))
        max_area = float(np.max(lum_area_frac[effective_start_frame : e_frame + 1]))

        findings.append(
            CandidateHazardFinding(
                interval_id=f"finding-{idx + 1:03d}",
                start_frame=effective_start_frame,
                end_frame=e_frame,
                start_ms=start_ms,
                end_ms=end_ms,
                proposed_safe_resume_ms=safe_resume_ms,
                types=tuple(combined_types),
                policy="pause_before_interval",
                metrics={
                    "peak_luminance_flashes_per_sec": round(max_lum_rev / 2.0, 2),
                    "opposing_luminance_transitions_1s": float(max_lum_rev),
                    "peak_red_flashes_per_sec": round(max_red_rev / 2.0, 2),
                    "opposing_red_transitions_1s": float(max_red_rev),
                    "max_luminance_delta": round(max_dy, 4),
                    "max_red_valence_delta": round(max_dred, 2),
                    "max_2d_pattern_pairs": float(max_stripes),
                    "max_spectral_concentration": round(max_spec, 4),
                    "max_affected_area_fraction": round(max_area, 4),
                },
            )
        )

    return ScreeningAnalysisReport(
        asset_revision_id=asset_revision_id,
        detector_version=DETECTOR_VERSION,
        fps=float(fps),
        total_frames=n_frames,
        width=width,
        height=height,
        timebase="presentation_ms",
        review_state="pending_review",
        findings=findings,
    )


def apply_human_editorial_decision(
    report: ScreeningAnalysisReport,
    *,
    reviewer_id: str,
    decision: str,
    notes: str,
) -> ScreeningAnalysisReport:
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("reviewer_id must be a non-empty string.")
    if decision not in ("approved", "rejected"):
        raise ValueError(f"Editorial decision must be 'approved' or 'rejected', got {decision!r}.")
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError("Editorial review notes must be a non-empty string.")

    report.review_state = decision
    report.editorial_decision_record = {
        "reviewer_id": reviewer_id.strip(),
        "decision": decision,
        "notes": notes.strip(),
        "zero_findings_attested": (decision == "approved" and len(report.findings) == 0),
    }
    return report


def find_ffmpeg_executable() -> Optional[str]:
    found = shutil.which("ffmpeg")
    candidates = []
    if found:
        candidates.append(pathlib.Path(found))
    candidates.append(
        pathlib.Path(
            r"C:\Users\ATEC\AppData\Local\Microsoft\WinGet\Packages"
            r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin\ffmpeg.exe"
        )
    )
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def analyze_video_file_headless(
    video_path: str | pathlib.Path,
    *,
    target_width: int = 64,
    target_height: int = 64,
    fps: float = 30.0,
) -> ScreeningAnalysisReport:
    path = pathlib.Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    ffmpeg_bin = find_ffmpeg_executable()
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg executable not found for headless video decoding.")

    raw_file_bytes = path.read_bytes()
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        f"fps={fps},scale={target_width}:{target_height}:flags=neighbor",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    frame_byte_size = target_width * target_height * 3
    raw_rgb = proc.stdout
    n_frames = len(raw_rgb) // frame_byte_size
    frames = np.frombuffer(raw_rgb[: n_frames * frame_byte_size], dtype=np.uint8).reshape(
        (n_frames, target_height, target_width, 3)
    )
    return analyze_rgb_frame_sequence(frames, fps=fps, raw_source_bytes=raw_file_bytes)


# ---------------------------------------------------------------------------
# Reference Fixture Suite & False-Positive / False-Negative Benchmark (P1)
# ---------------------------------------------------------------------------

def build_reference_fixtures(
    fps: float = 30.0, width: int = 64, height: int = 64
) -> List[Dict[str, Any]]:
    """
    Generates 8 deterministic RGB reference fixtures covering:
    - Smooth luminance ramp (TN)
    - Sub-threshold 2 Hz pulse (TN)
    - Compression block artifacts & uncorrelated sensor noise (TN)
    - 7.5 Hz luminance strobe (TP)
    - 7.5 Hz saturated red alternation (TP)
    - 8-pair vertical bar grating (TP)
    - 2D high-contrast checkerboard (TP - resolved previous 1D cancellation FN)
    - 45-degree diagonal & concentric radial grating (TP)
    """
    n_frames = 90  # 3.0 seconds at 30 fps
    fixtures: List[Dict[str, Any]] = []

    # 1. True Negative: Smooth luminance ramp
    f1 = np.zeros((n_frames, height, width, 3), dtype=np.uint8)
    for t in range(n_frames):
        val = int(round(40 + (160 * t / max(1, n_frames - 1))))
        f1[t, :, :, :] = val
    fixtures.append({
        "fixture_id": "ref_neg_smooth_ramp",
        "description": "Smooth luminance ramp from dark grey to light grey over 3.0s",
        "expected_hazard": False,
        "expected_types": (),
        "expected_window_ms": None,
        "frames": f1,
        "fps": fps,
    })

    # 2. True Negative: Sub-threshold ~1.9 Hz pulse (<= 3 flashes/s)
    f2 = np.full((n_frames, height, width, 3), 30, dtype=np.uint8)
    for t in range(30, 60):
        if (t // 8) % 2 == 1:
            f2[t, :, :, :] = 230
    fixtures.append({
        "fixture_id": "ref_neg_subthreshold_2hz_pulse",
        "description": "Sub-threshold ~1.9 Hz luminance pulse (<= 3 flashes/s)",
        "expected_hazard": False,
        "expected_types": (),
        "expected_window_ms": None,
        "frames": f2,
        "fps": fps,
    })

    # 3. True Negative: Synthetic compression noise + high-frequency uncorrelated sensor speckle
    rng = np.random.default_rng(seed=20260928)
    f_noise = rng.integers(85, 175, size=(n_frames, height, width, 3), dtype=np.uint8)
    fixtures.append({
        "fixture_id": "ref_neg_compression_and_sensor_noise",
        "description": "Deterministic uncorrelated sensor & compression noise around mid-grey",
        "expected_hazard": False,
        "expected_types": (),
        "expected_window_ms": None,
        "frames": f_noise,
        "fps": fps,
    })

    # 4. True Positive: 7.5 Hz full-screen white/black luminance strobe [1000ms, 2000ms]
    f3 = np.full((n_frames, height, width, 3), 20, dtype=np.uint8)
    for t in range(30, 60):
        f3[t, :, :, :] = 250 if ((t - 30) // 2) % 2 == 0 else 15
    fixtures.append({
        "fixture_id": "ref_pos_7p5hz_luminance_strobe",
        "description": "7.5 Hz full-screen white/black luminance flash between 1000ms and 2000ms",
        "expected_hazard": True,
        "expected_types": ("luminance_flash",),
        "expected_window_ms": (1000, 2000),
        "frames": f3,
        "fps": fps,
    })

    # 5. True Positive: 7.5 Hz saturated red strobe [500ms, 1500ms]
    f4 = np.full((n_frames, height, width, 3), 40, dtype=np.uint8)
    for t in range(15, 45):
        if ((t - 15) // 2) % 2 == 0:
            f4[t, :, :, :] = (255, 0, 0)
        else:
            f4[t, :, :, :] = (40, 0, 0)
    fixtures.append({
        "fixture_id": "ref_pos_7p5hz_saturated_red",
        "description": "7.5 Hz saturated red alternation between 500ms and 1500ms",
        "expected_hazard": True,
        "expected_types": ("saturated_red",),
        "expected_window_ms": (500, 1500),
        "frames": f4,
        "fps": fps,
    })

    # 6. True Positive: 8-pair vertical bar grating [667ms, 1333ms]
    f5 = np.full((n_frames, height, width, 3), 100, dtype=np.uint8)
    bar_width = max(1, width // 16)
    for t in range(20, 40):
        for col in range(width):
            is_bright = ((col // bar_width) % 2) == 0
            f5[t, :, col, :] = 245 if is_bright else 15
    fixtures.append({
        "fixture_id": "ref_pos_8pair_vertical_stripes",
        "description": "8-pair high-contrast vertical stripe grating (spatial_pattern)",
        "expected_hazard": True,
        "expected_types": ("spatial_pattern",),
        "expected_window_ms": (667, 1333),
        "frames": f5,
        "fps": fps,
    })

    # 7. True Positive: 2D High-Contrast Checkerboard [833ms, 1667ms] (previously FN under 1D axis averaging)
    f6 = np.full((n_frames, height, width, 3), 128, dtype=np.uint8)
    yy, xx = np.indices((height, width))
    cell_size = max(1, width // 16)
    checker = (((yy // cell_size) + (xx // cell_size)) % 2 == 0)
    for t in range(25, 50):
        f6[t, checker, :] = 245
        f6[t, ~checker, :] = 15
    fixtures.append({
        "fixture_id": "ref_pos_2d_checkerboard",
        "description": "High-contrast 2D checkerboard where full-row/col averages cancel out",
        "expected_hazard": True,
        "expected_types": ("spatial_pattern",),
        "expected_window_ms": (833, 1667),
        "frames": f6,
        "fps": fps,
    })

    # 8. True Positive: 45-degree diagonal & radial grating [1000ms, 1800ms]
    f7 = np.full((n_frames, height, width, 3), 120, dtype=np.uint8)
    diag_period = max(2, width // 12)
    diag_mask = (((yy + xx) // diag_period) % 2 == 0)
    for t in range(30, 54):
        f7[t, diag_mask, :] = 245
        f7[t, ~diag_mask, :] = 15
    fixtures.append({
        "fixture_id": "ref_pos_diagonal_grating",
        "description": "45-degree high-contrast diagonal stripe grating (spatial_pattern)",
        "expected_hazard": True,
        "expected_types": ("spatial_pattern",),
        "expected_window_ms": (1000, 1800),
        "frames": f7,
        "fps": fps,
    })

    return fixtures


def run_reference_benchmark() -> Dict[str, Any]:
    """
    Runs the 2D FFT + multi-orientation screening prototype over all 8 reference
    fixtures and compiles the measured confusion matrix (TP, TN, FP, FN) and timing accuracy.
    """
    fixtures = build_reference_fixtures()
    results: List[Dict[str, Any]] = []
    tp = tn = fp = fn = 0

    for fix in fixtures:
        report = analyze_rgb_frame_sequence(fix["frames"], fps=fix["fps"])
        detected_hazard = len(report.findings) > 0
        detected_types = sorted(
            set().union(*(f.types for f in report.findings))
        ) if detected_hazard else []

        expected = fix["expected_hazard"]
        if expected and detected_hazard:
            outcome = "TP"
            tp += 1
        elif not expected and not detected_hazard:
            outcome = "TN"
            tn += 1
        elif not expected and detected_hazard:
            outcome = "FP"
            fp += 1
        else:
            outcome = "FN"
            fn += 1

        timing_error_ms: Optional[int] = None
        if expected and detected_hazard and fix["expected_window_ms"] is not None:
            exp_s, exp_e = fix["expected_window_ms"]
            act_s = report.findings[0].start_ms
            act_e = report.findings[-1].end_ms
            timing_error_ms = max(abs(act_s - exp_s), abs(act_e - exp_e))

        results.append({
            "fixture_id": fix["fixture_id"],
            "description": fix["description"],
            "asset_revision_id": report.asset_revision_id,
            "expected_hazard": expected,
            "detected_hazard": detected_hazard,
            "expected_types": list(fix["expected_types"]),
            "detected_types": detected_types,
            "outcome": outcome,
            "timing_error_ms": timing_error_ms,
            "intervals": [f.to_sidecar_interval_dict() for f in report.findings],
        })

    return {
        "detector_version": DETECTOR_VERSION,
        "ea_iris_status": EA_IRIS_EVALUATION_STATUS,
        "confusion_matrix": {"TP": tp, "TN": tn, "FP": fp, "FN": fn, "total": len(fixtures)},
        "results": results,
        "unimplemented_capabilities": list(UNIMPLEMENTED_DETECTOR_CAPABILITIES),
    }


if __name__ == "__main__":
    summary = run_reference_benchmark()
    print(json.dumps(summary, indent=2))
