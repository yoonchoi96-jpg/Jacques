from __future__ import annotations

import json
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
SOURCE_PRIORITY = {
    "chordidentifier": 10,
    "chordino": 20,
    "magic_chords": 25,
    "essentia": 30,
    "librosa_chroma_template": 40,
}

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

def analyze_track_audio(conn, track_id, audio_path):
    """Deprecated: Jacques is streaming/link-only and never analyzes local released audio."""
    raise RuntimeError(
        "Local audio analysis is disabled. Register a Spotify or YouTube URL "
        "and import provider evidence instead."
    )

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

    sources = sorted(
        by_source,
        key=lambda source: (SOURCE_PRIORITY.get(source, 100), source),
    )[:MAX_HARMONY_SOURCES]
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
    # Persist normalized harmonic structure separately from the consensus label.
    # UST fields are only populated when evidence explicitly supplies them;
    # a slash chord/inversion is never silently reinterpreted as a UST.
    conn.execute("DELETE FROM harmony_structures WHERE track_id=?", (track_id,))
    for item in consensus:
        identity = _chord_identity(item["chord"]) or {}
        bass = identity.get("bass") or identity.get("root")
        root = identity.get("root")
        inversion = None
        if bass and root and bass != root:
            inversion = "slash_bass"
        evidence_ids = []
        cur = conn.execute(
            """INSERT INTO music_analysis_evidence
               (track_id, domain, source, source_type, method, version,
                start_sec, end_sec, payload_json, confidence, observed_at, created_at)
               VALUES (?, 'harmony', 'harmony_consensus', 'derived', ?, 'consensus_v1', ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                track_id, "multi_source_consensus", item["start_sec"], item["end_sec"],
                json.dumps(item, ensure_ascii=False), item["confidence"],
            ),
        )
        evidence_ids.append(int(cur.lastrowid))
        conn.execute(
            """INSERT INTO harmony_structures
               (track_id, start_sec, end_sec, root, bass_note, chord_quality,
                inversion, upper_structure_root, upper_structure_quality,
                upper_structure_notes, pitch_classes, roman_candidate,
                function_candidate, secondary_function, borrowed_from_mode,
                confidence, evidence_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                track_id, item["start_sec"], item["end_sec"], root, bass,
                identity.get("quality"), inversion, None, None, None,
                json.dumps(identity.get("pitch_classes", [])),
                item.get("roman_numeral"), None, None, None, item["confidence"],
                json.dumps({"evidence_ids": evidence_ids, "pitch_set_equivalents": item.get("pitch_set_equivalents", []), "competing_evidence": item.get("competing_evidence", [])}, ensure_ascii=False),
            ),
        )
    conn.commit()
    return profile
