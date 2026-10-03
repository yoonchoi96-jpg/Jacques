"""Technical mix/master measurements for Jacques.

Measurements are descriptive evidence. They are not automatically converted
into aesthetic judgments or fixed "correct" targets.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


EPS = 1e-12


def _db(value: float) -> float:
    return 20.0 * math.log10(max(abs(float(value)), EPS))


def _mono(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        return data.astype(float)
    return np.mean(data.astype(float), axis=1)


def _rms(data: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(data)) + EPS))


def _true_peak_approx(data: np.ndarray) -> float:
    # Oversampling-based approximation; report explicitly as an estimate.
    from scipy.signal import resample_poly
    if data.ndim == 1:
        up = resample_poly(data.astype(float), 4, 1)
    else:
        up = resample_poly(data.astype(float), 4, 1, axis=0)
    return float(np.max(np.abs(up)))


def _spectrum(mono: np.ndarray, sr: int) -> dict[str, Any]:
    window = np.hanning(len(mono))
    spec = np.abs(np.fft.rfft(mono * window))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
    power = spec ** 2
    total = float(np.sum(power) + EPS)

    bands = {
        "sub_20_60": (20, 60),
        "bass_60_250": (60, 250),
        "low_mid_250_500": (250, 500),
        "mid_500_2000": (500, 2000),
        "upper_mid_2000_5000": (2000, 5000),
        "presence_5000_10000": (5000, 10000),
        "air_10000_20000": (10000, 20000),
    }
    energy = {}
    for name, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        energy[name] = float(np.sum(power[mask]) / total) if np.any(mask) else 0.0

    return {
        "frequency_hz": freqs.tolist(),
        "magnitude": spec.tolist(),
        "band_energy_ratio": energy,
    }


def _lufs(data: np.ndarray, sr: int) -> dict[str, float | None]:
    try:
        import pyloudnorm as pyln
    except ImportError:
        return {
            "integrated": None,
            "short_term": None,
            "momentary": None,
            "available": False,
        }

    meter = pyln.Meter(sr)
    mono_or_stereo = data if data.ndim == 2 else data
    integrated = float(meter.integrated_loudness(mono_or_stereo))
    return {
        "integrated": integrated,
        "short_term": None,
        "momentary": None,
        "available": True,
    }


def analyze_production_audio(
    audio_path: str | Path,
    *,
    vu_reference_dbfs: float = -18.0,
) -> dict[str, Any]:
    data, sr = sf.read(str(audio_path), always_2d=False)
    data = np.asarray(data, dtype=float)
    mono = _mono(data)

    peak = float(np.max(np.abs(data)))
    rms = _rms(mono)
    peak_dbfs = _db(peak)
    rms_dbfs = _db(rms)
    true_peak = _true_peak_approx(data)
    true_peak_dbtp = _db(true_peak)

    # A VU number is calibration-dependent. This is a simple RMS-derived
    # program-level proxy referenced to the requested dBFS calibration.
    vu_average = rms_dbfs - float(vu_reference_dbfs)

    crest = peak_dbfs - rms_dbfs

    phase_correlation = None
    stereo_width = None
    mono_compatibility = None
    if data.ndim == 2 and data.shape[1] >= 2:
        left, right = data[:, 0], data[:, 1]
        denom = math.sqrt(float(np.sum(left * left) * np.sum(right * right))) + EPS
        phase_correlation = float(np.sum(left * right) / denom)
        mid = (left + right) * 0.5
        side = (left - right) * 0.5
        stereo_width = float(_rms(side) / (_rms(mid) + EPS))
        mono_compatibility = float(_rms(mid) / (_rms(mono) + EPS))

    clipping_samples = int(np.sum(np.abs(data) >= 0.999999))

    return {
        "analysis_version": "production_measurements_v1",
        "sample_rate": int(sr),
        "channels": int(1 if data.ndim == 1 else data.shape[1]),
        "duration_seconds": float(len(mono) / sr),
        "peak_dbfs": peak_dbfs,
        "true_peak_dbtp_approx": true_peak_dbtp,
        "rms_dbfs": rms_dbfs,
        "vu_reference_dbfs": float(vu_reference_dbfs),
        "vu_average_proxy": float(vu_average),
        "crest_factor_db": float(crest),
        "clipping_samples": clipping_samples,
        "phase_correlation": phase_correlation,
        "stereo_width": stereo_width,
        "mono_compatibility": mono_compatibility,
        "lufs": _lufs(data, sr),
        "spectrum": _spectrum(mono, sr),
    }


def analyze_and_store_production(
    track_id: str,
    audio_path: str | Path,
    *,
    stage: str = "master",
    vu_reference_dbfs: float = -18.0,
) -> dict[str, Any]:
    """Analyze an audio file and persist measurements/evidence for one track."""
    import json
    from ..analysis_store import store_evidence
    from ..database import get_connection

    result = analyze_production_audio(
        audio_path, vu_reference_dbfs=vu_reference_dbfs
    )
    evidence_id = store_evidence(
        track_id,
        "production",
        "production_meter",
        "local",
        result,
        method="production_measurements",
        version=result.get("analysis_version"),
        confidence=1.0,
    )
    lufs = result.get("lufs") or {}
    spectrum = result.get("spectrum") or {}
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO production_analysis (
                track_id, stage, source, start_sec, end_sec, sample_rate,
                peak_dbfs, true_peak_dbtp, rms_dbfs,
                lufs_momentary, lufs_short_term, lufs_integrated,
                vu_reference_dbfs, vu_average, crest_factor_db,
                phase_correlation, stereo_width, mono_compatibility,
                clipping_samples, eq_balance_json, spectrum_json,
                dynamics_json, stereo_json, confidence, evidence_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                track_id, stage, "production_meter", 0.0,
                result["duration_seconds"], result["sample_rate"],
                result["peak_dbfs"], result["true_peak_dbtp_approx"],
                result["rms_dbfs"], lufs.get("momentary"),
                lufs.get("short_term"), lufs.get("integrated"),
                result["vu_reference_dbfs"], result["vu_average_proxy"],
                result["crest_factor_db"], result.get("phase_correlation"),
                result.get("stereo_width"), result.get("mono_compatibility"),
                result["clipping_samples"],
                json.dumps(spectrum.get("band_energy_ratio", {})),
                json.dumps(spectrum),
                json.dumps({"rms_dbfs": result["rms_dbfs"], "crest_factor_db": result["crest_factor_db"]}),
                json.dumps({
                    "phase_correlation": result.get("phase_correlation"),
                    "stereo_width": result.get("stereo_width"),
                    "mono_compatibility": result.get("mono_compatibility"),
                }),
                1.0,
                json.dumps({"evidence_ids": [evidence_id]}),
            ),
        )
        metrics = [
            ("loudness", "lufs_integrated", lufs.get("integrated"), "LUFS"),
            ("level", "peak_dbfs", result.get("peak_dbfs"), "dBFS"),
            ("level", "true_peak_dbtp", result.get("true_peak_dbtp_approx"), "dBTP"),
            ("level", "rms_dbfs", result.get("rms_dbfs"), "dBFS"),
            ("level", "vu_average", result.get("vu_average_proxy"), "VU_proxy"),
            ("dynamics", "crest_factor", result.get("crest_factor_db"), "dB"),
            ("stereo", "phase_correlation", result.get("phase_correlation"), "ratio"),
            ("stereo", "stereo_width", result.get("stereo_width"), "ratio"),
            ("stereo", "mono_compatibility", result.get("mono_compatibility"), "ratio"),
            ("technical", "clipping_samples", result.get("clipping_samples"), "samples"),
        ]
        for category, parameter, value, unit in metrics:
            if value is None:
                continue
            conn.execute(
                """INSERT INTO production_observations
                   (track_id, stage, category, parameter, value, unit,
                    confidence, evidence_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    track_id, stage, category, parameter, float(value), unit,
                    1.0, json.dumps({"evidence_ids": [evidence_id]}),
                ),
            )
        conn.commit()
    return {"track_id": track_id, "stage": stage, "evidence_id": evidence_id, "analysis": result}
