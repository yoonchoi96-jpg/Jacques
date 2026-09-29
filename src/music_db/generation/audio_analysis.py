from __future__ import annotations

import json
from pathlib import Path


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
    y, sr = librosa.load(path, sr=None, mono=True)
    if len(y) == 0:
        raise ValueError(f"Empty audio file: {path}")

    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo).reshape(-1)[0])

    rms = librosa.feature.rms(y=y)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
    zcr = librosa.feature.zero_crossing_rate(y)[0]

    peak = float(np.max(np.abs(y)))
    rms_mean = float(np.mean(rms))
    rms_db = float(20 * np.log10(max(rms_mean, 1e-12)))
    peak_db = float(20 * np.log10(max(peak, 1e-12)))

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    key_index = int(np.argmax(chroma_mean))
    key_names = ["C", "C#", "D", "D#", "E", "F",
                 "F#", "G", "G#", "A", "A#", "B"]

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_rate = float(len(librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)) / max(duration, 1e-9))

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
    }


def analyze_and_store(conn, output_id, path):
    from .outputs import add_analysis
    payload = analyze_audio(path)
    add_analysis(conn, output_id, "audio_features_v1", payload)
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
