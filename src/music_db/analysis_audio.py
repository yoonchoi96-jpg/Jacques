"""Local scale/mode and melody analysis for Jacques.

All outputs are candidate observations with confidence/evidence, not claims of
ground truth. Pitch tracking is based on librosa pYIN; chroma is used for
tonal/scale estimation.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np


PC_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MODE_INTERVALS = {
    "ionian": [0, 2, 4, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
    "whole_tone": [0, 2, 4, 6, 8, 10],
}

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _normalize(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    norm = np.linalg.norm(v)
    return v / norm if norm else v


def _scale_score(chroma: np.ndarray, tonic: int, intervals: list[int]) -> float:
    mask = np.zeros(12, dtype=float)
    mask[(tonic + np.asarray(intervals)) % 12] = 1.0
    inside = float(np.sum(chroma * mask))
    outside = float(np.sum(chroma * (1.0 - mask)))
    return inside - outside * 0.5


def estimate_scale_mode(y: np.ndarray, sr: int) -> dict[str, Any]:
    import librosa

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = _normalize(np.mean(chroma, axis=1))
    candidates = []
    for tonic in range(12):
        major_corr = float(np.dot(profile, _normalize(np.roll(MAJOR_PROFILE, tonic))))
        minor_corr = float(np.dot(profile, _normalize(np.roll(MINOR_PROFILE, tonic))))
        candidates.extend([
            (major_corr, tonic, "ionian", "major"),
            (minor_corr, tonic, "aeolian", "natural_minor"),
        ])
        for mode, intervals in MODE_INTERVALS.items():
            if mode in {"ionian", "aeolian"}:
                continue
            candidates.append((_scale_score(profile, tonic, intervals), tonic, mode, mode))
    candidates.sort(reverse=True)
    best = candidates[0]
    second = candidates[1][0] if len(candidates) > 1 else best[0]
    confidence = max(0.0, min(1.0, 0.5 + (best[0] - second) * 2.0))
    return {
        "tonic": PC_NAMES[best[1]],
        "mode": best[2],
        "scale": best[3],
        "scale_degrees": MODE_INTERVALS[best[2]],
        "confidence": confidence,
        "candidates": [
            {"tonic": PC_NAMES[t], "mode": m, "scale": s, "score": float(sc)}
            for sc, t, m, s in candidates[:8]
        ],
        "chroma_mean": profile.tolist(),
    }


def _midi_to_pc(midi: np.ndarray) -> np.ndarray:
    return np.mod(np.rint(midi).astype(int), 12)


def _contour(midi: np.ndarray) -> str:
    if len(midi) < 2:
        return "static"
    diffs = np.diff(midi)
    up = int(np.sum(diffs > 0.5))
    down = int(np.sum(diffs < -0.5))
    return "ascending" if up > down * 1.5 else "descending" if down > up * 1.5 else "mixed"


def _interval_distribution(midi: np.ndarray) -> dict[str, int]:
    if len(midi) < 2:
        return {}
    steps = np.rint(np.diff(midi)).astype(int)
    return {str(k): int(v) for k, v in Counter(steps).most_common()}


def analyze_melody_audio(
    audio_path: str | Path,
    *,
    fmin: float = 65.4,
    fmax: float = 2093.0,
) -> dict[str, Any]:
    import librosa

    y, sr = librosa.load(str(audio_path), sr=44100, mono=True)
    f0, voiced, probs = librosa.pyin(
        y, sr=sr, fmin=fmin, fmax=min(fmax, sr / 2.0),
        frame_length=2048, hop_length=512
    )
    times = librosa.times_like(f0, sr=sr, hop_length=512)
    valid = np.isfinite(f0) & voiced & (probs >= 0.70)
    midi = librosa.hz_to_midi(f0[valid])
    times_valid = times[valid]
    if len(midi) == 0:
        return {
            "analysis_version": "melody_scale_v1",
            "scale_mode": estimate_scale_mode(y, sr),
            "melody": None,
            "confidence": 0.0,
        }

    pcs = _midi_to_pc(midi)
    pc_counts = Counter(int(x) for x in pcs)
    distribution = {PC_NAMES[k]: int(v) for k, v in pc_counts.items()}
    intervals = _interval_distribution(midi)
    scale_mode = estimate_scale_mode(y, sr)

    # Collapse near-identical adjacent MIDI frames into note events.
    events = []
    current = [float(midi[0]), float(times_valid[0]), float(times_valid[0])]
    for m, t in zip(midi[1:], times_valid[1:]):
        if abs(float(m) - current[0]) <= 0.6 and float(t) - current[2] < 0.15:
            current[2] = float(t)
        else:
            events.append(tuple(current))
            current = [float(m), float(t), float(t)]
    events.append(tuple(current))

    event_midi = np.array([round(x[0]) for x in events], dtype=float)
    semitone_range = float(np.max(event_midi) - np.min(event_midi)) if len(event_midi) else 0.0
    mean_midi = float(np.mean(event_midi)) if len(event_midi) else None
    motif = [int(x) for x in np.rint(np.diff(event_midi[:9])).astype(int)] if len(event_midi) > 1 else []

    return {
        "analysis_version": "melody_scale_v1",
        "sample_rate": int(sr),
        "duration_seconds": float(len(y) / sr),
        "scale_mode": scale_mode,
        "melody": {
            "start_sec": float(times_valid[0]),
            "end_sec": float(times_valid[-1]),
            "range_semitones": semitone_range,
            "mean_midi": mean_midi,
            "contour": _contour(event_midi),
            "pitch_class_distribution": distribution,
            "interval_distribution": intervals,
            "motif_interval_candidate": motif,
            "voiced_frame_ratio": float(np.mean(valid)),
            "mean_voicing_probability": float(np.mean(probs[valid])) if np.any(valid) else 0.0,
        },
        "confidence": float(np.mean(probs[valid])) if np.any(valid) else 0.0,
    }


def analyze_and_store_scale_melody(track_id: str, audio_path: str | Path) -> dict[str, Any]:
    from .analysis_store import store_evidence
    from .database import get_connection

    result = analyze_melody_audio(audio_path)
    scale = result["scale_mode"]
    evidence_id = store_evidence(
        track_id, "scale_mode", "scale_mode_analyzer", "local", scale,
        method="chroma_scale_candidate", version=result["analysis_version"],
        confidence=scale.get("confidence"),
    )
    with get_connection() as conn:
        melody = result.get("melody")
        chord_tone_ratio = None
        if melody:
            structures = conn.execute(
                "SELECT start_sec, end_sec, pitch_classes FROM harmony_structures WHERE track_id=? ORDER BY start_sec",
                (track_id,),
            ).fetchall()
            if structures:
                total_weight = sum(melody["pitch_class_distribution"].values())
                chord_weight = 0
                for row in structures:
                    try:
                        pcs = set(json.loads(row["pitch_classes"] or "[]"))
                    except (TypeError, ValueError):
                        pcs = set()
                    if not pcs:
                        continue
                    for name, count in melody["pitch_class_distribution"].items():
                        pc = PC_NAMES.index(name)
                        if pc in pcs:
                            chord_weight += int(count)
                if total_weight:
                    chord_tone_ratio = float(chord_weight / total_weight)
            melody["chord_tone_ratio"] = chord_tone_ratio

        conn.execute("DELETE FROM scale_mode_analysis WHERE track_id=?", (track_id,))
        conn.execute(
            """INSERT INTO scale_mode_analysis
               (track_id, section_name, start_sec, end_sec, tonic, mode, scale,
                parent_scale, scale_degrees_json, confidence, evidence_json,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                track_id, "full_track", 0.0,
                result.get("duration_seconds"), scale.get("tonic"), scale.get("mode"),
                scale.get("scale"), "diatonic" if scale.get("mode") in MODE_INTERVALS else None,
                json.dumps(scale.get("scale_degrees", [])),
                scale.get("confidence"),
                json.dumps({"evidence_ids": [evidence_id], "candidates": scale.get("candidates", [])}),
            ),
        )
        conn.execute("DELETE FROM melody_analysis WHERE track_id=?", (track_id,))
        melody = result.get("melody")
        if melody:
            conn.execute(
                """INSERT INTO melody_analysis
                   (track_id, section_name, start_sec, end_sec, key_candidate,
                    mode_candidate, scale_candidate, range_semitones, mean_midi,
                    contour, pitch_class_distribution_json, interval_distribution_json,
                    motif_json, non_chord_tone_json, chord_tone_ratio, confidence,
                    evidence_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    track_id, "full_track", melody["start_sec"], melody["end_sec"],
                    scale.get("tonic"), scale.get("mode"), scale.get("scale"),
                    melody["range_semitones"], melody["mean_midi"], melody["contour"],
                    json.dumps(melody["pitch_class_distribution"]),
                    json.dumps(melody["interval_distribution"]),
                    json.dumps(melody["motif_interval_candidate"]),
                    json.dumps({"chord_tone_ratio": chord_tone_ratio}),
                    chord_tone_ratio,
                    result["confidence"],
                    json.dumps({"evidence_ids": [evidence_id]}),
                ),
            )
        conn.commit()
    return {"track_id": track_id, "evidence_id": evidence_id, "analysis": result}
