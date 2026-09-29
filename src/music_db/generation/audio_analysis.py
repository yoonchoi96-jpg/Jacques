from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone


ANALYSIS_VERSION = "audio_features_v4"


def now():
    return datetime.now(timezone.utc).isoformat()


def analyze_audio(path):
    """Analyze a local audio file. Requires the optional audio stack."""
    try:
        import librosa
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Audio analysis requires optional dependencies. "
            "Install requirements-audio.txt"
        ) from exc

    path = str(Path(path).expanduser().resolve())
    raw_y, sr = librosa.load(path, sr=None, mono=False)
    channels = np.asarray(raw_y)
    if channels.ndim == 1:
        y = channels
        stereo_correlation = 1.0
        stereo_width = 0.0
    else:
        y = np.mean(channels, axis=0)
        if channels.shape[0] >= 2:
            left, right = channels[0], channels[1]
            stereo_correlation = float(np.corrcoef(left, right)[0, 1]) if np.std(left) and np.std(right) else 1.0
            stereo_width = float(np.mean(np.abs(left - right)) / max(np.mean(np.abs(left + right)), 1e-12))
        else:
            stereo_correlation = 1.0
            stereo_width = 0.0
    if len(y) == 0:
        raise ValueError(f"Empty audio file: {path}")

    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo).reshape(-1)[0])

    rms = librosa.feature.rms(y=y)[0]
    spectral_bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    spectral_flatness = librosa.feature.spectral_flatness(y=y)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]

    peak = float(np.max(np.abs(y)))
    rms_mean = float(np.mean(rms))
    rms_db = float(20 * np.log10(max(rms_mean, 1e-12)))
    peak_db = float(20 * np.log10(max(peak, 1e-12)))
    p999 = float(np.percentile(np.abs(y), 99.9))
    dynamic_range_estimate_db = float(20 * np.log10(max(p999, 1e-12)) - rms_db)

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    key_index = int(np.argmax(chroma_mean))
    chroma_sorted = np.sort(chroma_mean)[::-1]
    key_confidence = float((chroma_sorted[0] - chroma_sorted[1]) / max(chroma_sorted[0], 1e-12)) if len(chroma_sorted) > 1 else 1.0
    key_names = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    silence_ratio = float(np.mean(np.abs(y) < max(peak * 0.01, 1e-5)))
    onset_rate = float(len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / max(duration, 1e-9))
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    spectral_contrast = librosa.feature.spectral_contrast(y=y, sr=sr)

    return {
        "file": path,
        "duration_seconds": duration,
        "sample_rate": int(sr),
        "tempo_bpm": tempo,
        "estimated_key": key_names[key_index],
        "rms_db": rms_db,
        "peak_db": peak_db,
        "crest_factor_db": peak_db - rms_db,
        "spectral_centroid_hz": float(np.mean(centroid)),
        "spectral_rolloff_hz": float(np.mean(rolloff)),
        "zero_crossing_rate": float(np.mean(zcr)),
        "onset_rate_per_second": onset_rate,
        "beat_count": int(len(beat_frames)),
        "spectral_bandwidth_hz": float(np.mean(spectral_bandwidth)),
        "spectral_flatness": float(np.mean(spectral_flatness)),
        "silence_ratio": silence_ratio,
        "dynamic_range_estimate_db": dynamic_range_estimate_db,
        "key_confidence": key_confidence,
        "stereo_correlation": stereo_correlation,
        "stereo_width": stereo_width,
        "mfcc_mean": [float(x) for x in np.mean(mfcc, axis=1)],
        "mfcc_std": [float(x) for x in np.std(mfcc, axis=1)],
        "spectral_contrast_mean_db": [float(x) for x in np.mean(spectral_contrast, axis=1)],
        "analysis_version": ANALYSIS_VERSION,
    }


def analyze_and_store(conn, output_id, path):
    from .fingerprint import sha256_file
    from .outputs import add_analysis

    fingerprint = sha256_file(path)
    cached = conn.execute(
        """SELECT analysis_type, payload_json
           FROM audio_analysis_cache
           WHERE fingerprint_sha256=? AND analysis_type=?
           LIMIT 1""",
        (fingerprint, ANALYSIS_VERSION),
    ).fetchone()
    payload = None
    if cached:
        try:
            candidate = json.loads(cached["payload_json"])
            if (
                isinstance(candidate, dict)
                and candidate.get("analysis_version") == ANALYSIS_VERSION
            ):
                payload = candidate
        except (TypeError, json.JSONDecodeError):
            payload = None

    if payload is not None:
        payload["file"] = str(Path(path).expanduser().resolve())
    else:
        # A corrupted or stale cache entry is self-healed by recomputing
        # the current analysis version for the same audio fingerprint.
        payload = analyze_audio(path)
        conn.execute(
            """INSERT INTO audio_analysis_cache
               (fingerprint_sha256, analysis_type, payload_json, created_at, updated_at)
               VALUES (?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(fingerprint_sha256,analysis_type)
               DO UPDATE SET payload_json=excluded.payload_json,
                             updated_at=CURRENT_TIMESTAMP""",
            (fingerprint, payload["analysis_version"],
             json.dumps(payload, ensure_ascii=False), now()),
        )
        conn.commit()
    add_analysis(conn, output_id, payload["analysis_version"], payload)
    conn.execute(
        """UPDATE generation_outputs
           SET duration_seconds=?, bpm=?, sample_rate=?, format=?, analysis_json=?
           WHERE output_id=?""",
        (
            payload["duration_seconds"], payload["tempo_bpm"],
            payload["sample_rate"], Path(path).suffix.lstrip("."),
            json.dumps(payload, ensure_ascii=False), output_id,
        ),
    )
    conn.commit()
    return payload
