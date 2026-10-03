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

CHORD_ROOT_TO_PC = {name: i for i, name in enumerate(CHROMA_ROOTS)}
CHORD_ROOT_TO_PC.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})
QUALITY_INTERVALS = {
    "": [0, 4, 7],
    "m": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "m7": [0, 3, 7, 10],
    "6": [0, 4, 7, 9],
    "m6": [0, 3, 7, 9],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "dim": [0, 3, 6],
    "dim7": [0, 3, 6, 9],
    "aug": [0, 4, 8],
    "add9": [0, 4, 7, 2],
}
MAX_HARMONY_SOURCES = 5
MIN_CONSENSUS_SOURCES = 2

def _family(chord):
    chord = str(chord or "").strip()
    return chord if chord else None

def _split(chord):
    chord = _family(chord)
    if not chord or chord.upper() in {"N", "NO_CHORD", "N.C."}:
        return None, None, None
    root = chord[:2] if len(chord) > 1 and chord[1] in "#b" else chord[:1]
    remainder = chord[len(root):]
    bass = None
    if "/" in remainder:
        quality, bass = remainder.split("/", 1)
    else:
        quality = remainder
    return root, quality or "", bass

def _pitch_classes(chord):
    root, quality, bass = _split(chord)
    if root not in CHORD_ROOT_TO_PC:
        return []
    intervals = QUALITY_INTERVALS.get(quality)
    if intervals is None:
        return []
    pcs = sorted({(CHORD_ROOT_TO_PC[root] + interval) % 12 for interval in intervals})
    if bass in CHORD_ROOT_TO_PC:
        pcs = [CHORD_ROOT_TO_PC[bass]] + [pc for pc in pcs if pc != CHORD_ROOT_TO_PC[bass]]
    return pcs

def _chord_identity(chord):
    root, quality, bass = _split(chord)
    if root is None:
        return None
    return {"root": root, "quality": quality, "bass": bass, "pitch_classes": _pitch_classes(chord)}

def _chord_ambiguity(chord):
    """Return pitch-set-equivalent spellings without treating them as the same chord."""
    identity = _chord_identity(chord)
    if not identity or not identity["pitch_classes"]:
        return []
    target = set(identity["pitch_classes"])
    candidates = []
    for root, root_pc in CHORD_ROOT_TO_PC.items():
        for quality, intervals in QUALITY_INTERVALS.items():
            pcs = {(root_pc + interval) % 12 for interval in intervals}
            if pcs == target:
                candidate = root + quality
                if candidate != chord:
                    candidates.append(candidate)
    return sorted(set(candidates))

def _overlap_ratio(a, b):
    start = max(float(a["start_sec"]), float(b["start_sec"]))
    end = min(float(a["end_sec"]), float(b["end_sec"]))
    overlap = max(0.0, end - start)
    shorter = min(
        float(a["end_sec"]) - float(a["start_sec"]),
        float(b["end_sec"]) - float(b["start_sec"]),
    )
    return overlap / shorter if shorter > 0 else 0.0


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
    """Fuse up to five independent observations without collapsing harmonic identity.

    A progression is only promoted to consensus when at least two distinct
    sources agree on the same chord identity (root/quality/bass). Pitch-set
    equivalents such as Am7 and C6 remain distinct candidates and are exposed
    as ambiguity metadata instead of being silently merged.
    """
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

    by_source = defaultdict(list)
    for row in rows:
        by_source[row["source"]].append(dict(row))

    sources = sorted(by_source)[:MAX_HARMONY_SOURCES]
    evidence_rows = [item for source in sources for item in by_source[source]]
    candidates = []
    for item in evidence_rows:
        if not item.get("chord"):
            continue
        candidates.append(item)

    consensus = []
    used = set()
    for anchor_idx, anchor in enumerate(candidates):
        anchor_key = (anchor["source"], float(anchor["start_sec"]), float(anchor["end_sec"]), anchor["chord"])
        if anchor_key in used:
            continue
        cluster = []
        source_names = set()
        for item in candidates:
            if item["source"] in source_names:
                continue
            if _overlap_ratio(anchor, item) >= 0.5:
                cluster.append(item)
                source_names.add(item["source"])
        if len(source_names) < MIN_CONSENSUS_SOURCES:
            continue

        identity = _chord_identity(anchor["chord"])
        if not identity:
            continue
        same_identity = [
            item for item in cluster
            if _chord_identity(item["chord"]) == identity
        ]
        if len({item["source"] for item in same_identity}) < MIN_CONSENSUS_SOURCES:
            continue

        source_count = len({item["source"] for item in same_identity})
        confidence_values = [float(item["confidence"] or 0.5) for item in same_identity]
        agreement = source_count / max(1, len(source_names))
        confidence = min(1.0, 0.5 * agreement + 0.5 * (sum(confidence_values) / len(confidence_values)))

        start_sec = max(float(item["start_sec"]) for item in same_identity)
        end_sec = min(float(item["end_sec"]) for item in same_identity)
        if end_sec <= start_sec:
            start_sec = min(float(item["start_sec"]) for item in same_identity)
            end_sec = max(float(item["end_sec"]) for item in same_identity)

        ambiguity = sorted({
            alt
            for item in cluster
            for alt in _chord_ambiguity(item["chord"])
            if alt != anchor["chord"]
        })

        consensus_item = {
            "start_sec": start_sec,
            "end_sec": end_sec,
            "chord": anchor["chord"],
            "chord_family": anchor["chord"],
            "root": identity["root"],
            "quality": identity["quality"],
            "bass": identity["bass"],
            "pitch_classes": identity["pitch_classes"],
            "pitch_set_equivalents": ambiguity,
            "confidence": confidence,
            "agreement": agreement,
            "source_count": source_count,
            "evidence": same_identity,
            "competing_evidence": [
                item for item in cluster if item not in same_identity
            ],
        }
        consensus.append(consensus_item)
        used.update(
            (item["source"], float(item["start_sec"]), float(item["end_sec"]), item["chord"])
            for item in same_identity
        )

    consensus.sort(key=lambda item: (item["start_sec"], item["end_sec"]))
    progression = [item["chord"] for item in consensus]

    key_row = conn.execute(
        "SELECT key FROM harmony_sources WHERE track_id=? AND key IS NOT NULL "
        "ORDER BY confidence DESC LIMIT 1",
        (track_id,),
    ).fetchone()
    resolved_key = key_row[0] if key_row else None
    mode = None
    if resolved_key and " " in resolved_key:
        resolved_key, mode = resolved_key.rsplit(" ", 1)

    def roman_degree(chord):
        if not resolved_key or resolved_key not in CHORD_ROOT_TO_PC:
            return None
        root, quality, _ = _split(chord)
        if root not in CHORD_ROOT_TO_PC:
            return None
        tonic = CHORD_ROOT_TO_PC[resolved_key]
        degree = (CHORD_ROOT_TO_PC[root] - tonic) % 12
        major_degrees = {0: "I", 2: "ii", 4: "iii", 5: "IV", 7: "V", 9: "vi", 11: "vii°"}
        label = major_degrees.get(degree)
        if not label:
            return None
        if quality.startswith("m") and label.isupper():
            label = label.lower()
        if "7" in quality:
            label += "7"
        return label

    for item in consensus:
        item["roman_numeral"] = roman_degree(item["chord"])

    tempo_row = conn.execute(
        "SELECT tempo FROM audio_features WHERE track_id=? AND tempo IS NOT NULL",
        (track_id,),
    ).fetchone()
    tempo = float(tempo_row[0]) if tempo_row else None

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
        "roman_progression": [item["roman_numeral"] for item in consensus],
        "segments": consensus,
        "harmonic_rhythm_sec": harmonic_rhythm,
        "chord_change_rate_per_sec": change_rate,
        "extensions": sorted({
            chord for chord in progression
            if any(token in chord for token in ("7", "sus", "dim", "aug", "6", "add"))
        }),
        "confidence": (
            sum(item["confidence"] for item in consensus) / len(consensus)
            if consensus else 0
        ),
        "consensus_policy": {
            "max_sources": MAX_HARMONY_SOURCES,
            "minimum_agreeing_sources": MIN_CONSENSUS_SOURCES,
            "identity_rule": "root+quality+bass",
            "pitch_set_equivalents_are_not_merged": True,
        },
    }

    loop_bars = None
    if tempo and harmonic_rhythm:
        beats_per_chord = harmonic_rhythm * tempo / 60.0
        loop_bars = round((beats_per_chord * len(consensus)) / 4.0, 2)

    conn.execute(
        """
        INSERT OR REPLACE INTO harmony_profiles
          (track_id, key, mode, harmonic_rhythm, chord_change_rate, loop_bars,
           progression_json, sections_json, extensions_json, bass_motion_json,
           consensus_confidence, analysis_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track_id, resolved_key, mode, harmonic_rhythm, change_rate, loop_bars,
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
