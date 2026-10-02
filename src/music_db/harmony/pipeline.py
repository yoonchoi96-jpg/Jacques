from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CHROMA_ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
TEMPLATES = {
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "sus4": [0, 5, 7],
    "dim": [0, 3, 6],
}

def _now():
    return datetime.now(timezone.utc).isoformat()

def _family(chord):
    chord = str(chord or "").strip()
    return chord.split("/")[0] if chord else None

def _split(chord):
    chord = _family(chord)
    if not chord or chord.upper() in {"N", "NO_CHORD", "N.C."}:
        return None, None, None
    root = chord[:2] if len(chord) > 1 and chord[1] in "#b" else chord[:1]
    return root, chord[len(root):] or "", None

def _store_segment(conn, track_id, source, seg):
    root, quality, bass = _split(seg.get("chord"))
    now = _now()
    conn.execute(
        """
        INSERT INTO harmony_segments
          (track_id, source, section_name, start_sec, end_sec, chord, root,
           quality, bass_note, confidence, method, raw_data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source, start_sec, end_sec) DO UPDATE SET
          section_name=excluded.section_name, chord=excluded.chord,
          root=excluded.root, quality=excluded.quality, bass_note=excluded.bass_note,
          confidence=excluded.confidence, method=excluded.method,
          raw_data=excluded.raw_data, updated_at=excluded.updated_at
        """,
        (
            track_id, source, seg.get("section_name"),
            float(seg["start_sec"]), float(seg["end_sec"]), seg.get("chord"),
            root, quality, bass, seg.get("confidence"), seg.get("method"),
            json.dumps(seg, ensure_ascii=False), now, now,
        ),
    )

def import_external_harmony(conn, track_id, payload, source="external"):
    source = payload.get("source") or source
    source_url = payload.get("source_url")
    segments = payload.get("segments") or payload.get("chords") or []
    for seg in segments:
        item = dict(seg)
        item.setdefault("confidence", payload.get("confidence"))
        item.setdefault("method", "external")
        item.setdefault("section_name", seg.get("section"))
        conn.execute(
            """
            INSERT OR REPLACE INTO harmony_sources
              (track_id, source, source_type, source_url, section_name,
               start_sec, end_sec, chord, key, confidence, raw_data, observed_at)
            VALUES (?, ?, 'external', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id, source, source_url, item.get("section_name"),
                item.get("start_sec", 0), item.get("end_sec", 0),
                item.get("chord"), payload.get("key"), item.get("confidence"),
                json.dumps(item, ensure_ascii=False), _now(),
            ),
        )
        _store_segment(conn, track_id, source, item)
    conn.commit()
    return len(segments)

def _librosa_estimate(audio_path):
    import librosa
    import numpy as np

    y, sr = librosa.load(audio_path, sr=44100, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo).reshape(-1)[0])
    beat_times = librosa.frames_to_time(beats, sr=sr)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)

    segments = []
    if len(beat_times) < 2:
        beat_times = np.array([0.0, duration])

    for i in range(0, len(beat_times) - 1, 4):
        start = float(beat_times[i])
        end = float(beat_times[min(i + 4, len(beat_times) - 1)])
        if end <= start:
            continue
        a = int(librosa.time_to_frames(start, sr=sr))
        b = int(librosa.time_to_frames(end, sr=sr))
        vec = np.mean(chroma[:, a:max(a + 1, b)], axis=1)
        norm = np.linalg.norm(vec) or 1.0
        vec = vec / norm
        scores = []
        for root_idx, root_name in enumerate(CHROMA_ROOTS):
            for quality, intervals in TEMPLATES.items():
                template = np.zeros(12, dtype=float)
                for interval in intervals:
                    template[(root_idx + interval) % 12] = 1.0
                template /= np.linalg.norm(template) or 1.0
                scores.append((float(np.dot(vec, template)), root_name + quality))
        scores.sort(reverse=True)
        best, chord = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        confidence = max(0.0, min(1.0, (best - second) * 4.0 + best * 0.5))
        segments.append({
            "start_sec": start,
            "end_sec": end,
            "chord": chord,
            "confidence": confidence,
            "method": "librosa_chroma_template",
        })

    collapsed = []
    for seg in segments:
        if collapsed and _family(collapsed[-1]["chord"]) == _family(seg["chord"]):
            collapsed[-1]["end_sec"] = seg["end_sec"]
            collapsed[-1]["confidence"] = (
                collapsed[-1]["confidence"] + seg["confidence"]
            ) / 2
        else:
            collapsed.append(seg)

    chroma_mean = np.mean(chroma, axis=1)
    major = np.array([1,0,0,0.5,0,0,0.5,1,0,0,0,0.5], dtype=float)
    minor = np.array([1,0,0.5,1,0,0,0.5,1,0,0.5,0,0], dtype=float)
    candidates = []
    for root in range(12):
        candidates.append((float(np.dot(chroma_mean, np.roll(major, root))), CHROMA_ROOTS[root], "major"))
        candidates.append((float(np.dot(chroma_mean, np.roll(minor, root))), CHROMA_ROOTS[root], "minor"))
    candidates.sort(reverse=True)
    _, key_root, mode = candidates[0]

    return {
        "tempo": tempo,
        "duration_sec": duration,
        "key": f"{key_root} {mode}",
        "mode": mode,
        "segments": collapsed,
        "method": "librosa_chroma_template",
    }

def _chordino_estimate(audio_path):
    command = os.getenv("CHORDINO_COMMAND")
    if not command:
        return None
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "chords.csv"
        cmd = command.split() + [audio_path, "-w", str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        if not out.exists():
            return None
        segments = []
        for line in out.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                segments.append({
                    "start_sec": float(parts[0]),
                    "end_sec": float(parts[1]),
                    "chord": parts[2],
                    "confidence": None,
                    "method": "chordino",
                })
            except ValueError:
                continue
        return {"segments": segments, "method": "chordino"} if segments else None

def analyze_track_audio(conn, track_id, audio_path):
    results = [_librosa_estimate(audio_path)]
    chordino = _chordino_estimate(audio_path)
    if chordino:
        results.append(chordino)

    for result in results:
        source = result.get("method", "audio")
        for seg in result.get("segments", []):
            _store_segment(conn, track_id, source, seg)
            conn.execute(
                """
                INSERT OR REPLACE INTO harmony_sources
                  (track_id, source, source_type, section_name, start_sec, end_sec,
                   chord, key, confidence, raw_data, observed_at)
                VALUES (?, ?, 'audio', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id, source, None, seg["start_sec"], seg["end_sec"],
                    seg.get("chord"), result.get("key"), seg.get("confidence"),
                    json.dumps(seg, ensure_ascii=False), _now(),
                ),
            )
    conn.commit()
    return results

def fuse_track_harmony(conn, track_id):
    rows = conn.execute(
        """
        SELECT source, section_name, start_sec, end_sec, chord, confidence
        FROM harmony_segments
        WHERE track_id=? AND chord IS NOT NULL
        ORDER BY start_sec, source
        """,
        (track_id,),
    ).fetchall()
    if not rows:
        return None

    buckets = defaultdict(list)
    for row in rows:
        buckets[round(float(row["start_sec"]), 1)].append(dict(row))

    consensus = []
    for start, evidence in sorted(buckets.items()):
        weights = Counter()
        for item in evidence:
            chord = _family(item["chord"])
            if not chord:
                continue
            source_weight = 1.25 if item["source"] in {"chordino", "essentia"} else 1.0
            confidence = float(item["confidence"] or 0.5)
            weights[chord] += source_weight * max(0.1, min(1.0, confidence))
        if not weights:
            continue
        chord, score = weights.most_common(1)[0]
        total = sum(weights.values()) or 1.0
        agreement = score / total
        end = max(float(item["end_sec"]) for item in evidence)
        confidence = min(1.0, 0.5 * agreement + 0.5 * min(1.0, score / 2.0))
        item = {
            "start_sec": start,
            "end_sec": end,
            "chord": chord,
            "chord_family": chord,
            "confidence": confidence,
            "agreement": agreement,
            "source_count": len(evidence),
            "evidence": evidence,
        }
        consensus.append(item)
        conn.execute(
            """
            INSERT OR REPLACE INTO harmony_consensus
              (track_id, section_name, start_sec, end_sec, chord, chord_family,
               confidence, agreement, source_count, evidence_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id, None, start, end, chord, chord, confidence, agreement,
                len(evidence), json.dumps(evidence, ensure_ascii=False), _now(), _now(),
            ),
        )

    progression = [item["chord"] for item in consensus]
    intervals = [
        item["end_sec"] - item["start_sec"]
        for item in consensus
        if item["end_sec"] > item["start_sec"]
    ]
    harmonic_rhythm = sum(intervals) / len(intervals) if intervals else None
    change_rate = (
        len(consensus) /
        max(1.0, consensus[-1]["end_sec"] - consensus[0]["start_sec"])
    ) if consensus else None
    profile = {
        "progression": progression,
        "segments": consensus,
        "harmonic_rhythm_sec": harmonic_rhythm,
        "chord_change_rate_per_sec": change_rate,
        "extensions": sorted({
            chord for chord in progression
            if any(token in chord for token in ("7", "sus", "dim"))
        }),
        "confidence": (
            sum(item["confidence"] for item in consensus) / len(consensus)
            if consensus else 0
        ),
    }

    conn.execute(
        """
        INSERT OR REPLACE INTO harmony_profiles
          (track_id, key, mode, harmonic_rhythm, chord_change_rate, loop_bars,
           progression_json, sections_json, extensions_json, bass_motion_json,
           consensus_confidence, analysis_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track_id, None, None, harmonic_rhythm, change_rate, None,
            json.dumps(progression, ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            json.dumps(profile["extensions"], ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            profile["confidence"],
            json.dumps(profile, ensure_ascii=False),
            _now(), _now(),
        ),
    )
    conn.commit()
    return profile
