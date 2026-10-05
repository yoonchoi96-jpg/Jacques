from __future__ import annotations

import json
import math
import re
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
MIN_CONSENSUS_COVERAGE = 0.20
SOURCE_PRIORITY = {
    "chordidentifier": 10,
    "chordino": 20,
    "magic_chords": 25,
    "essentia": 30,
    "librosa_chroma_template": 40,
    "methodic_truth": 15,
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
    return {
        "root": root,
        "quality": quality,
        "bass": bass,
        "pitch_classes": _pitch_classes(chord),
    }


def _chord_identity_key(chord):
    """Match harmonic identity by pitch class while preserving source spelling."""
    identity = _chord_identity(chord)
    if not identity:
        return None
    root_pc = CHORD_ROOT_TO_PC.get(identity["root"])
    bass_pc = (
        CHORD_ROOT_TO_PC.get(identity["bass"])
        if identity["bass"] is not None
        else None
    )
    if root_pc is None or (identity["bass"] is not None and bass_pc is None):
        return None
    return (root_pc, identity["quality"], bass_pc)

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

def _coerce_seconds(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        try:
            total = 0.0
            for part in parts:
                total = total * 60.0 + float(part)
            return total
        except ValueError:
            return None
    try:
        return float(text)
    except ValueError:
        return None


def import_external_harmony(conn, track_id, payload, source="external"):
    source = payload.get("source") or source
    source_url = payload.get("source_url")
    segments = payload.get("segments") or payload.get("chords") or []
    has_tracks = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'"
    ).fetchone()
    track_row = (
        conn.execute(
            "SELECT duration_ms FROM tracks WHERE track_id=?",
            (track_id,),
        ).fetchone()
        if has_tracks
        else None
    )
    duration_sec = (
        float(track_row[0]) / 1000.0
        if track_row and track_row[0] is not None
        else None
    )

    # Provider observations are snapshots, not append-only history. Clear the
    # previous observation for this track/source before importing the current
    # payload, including an empty/processing payload. Otherwise a failed or
    # partial provider refresh can silently contaminate consensus with stale
    # evidence from an earlier run.
    conn.execute(
        "DELETE FROM harmony_segments WHERE track_id=? AND source=?",
        (track_id, source),
    )
    conn.execute(
        "DELETE FROM harmony_sources WHERE track_id=? AND source=?",
        (track_id, source),
    )

    imported_count = 0
    skipped_count = 0
    for seg in segments:
        item = dict(seg)
        start_sec = _coerce_seconds(
            item.get("start_sec", item.get("start", item.get("start_time")))
        )
        end_sec = _coerce_seconds(
            item.get("end_sec", item.get("end", item.get("end_time")))
        )
        # Never fabricate timing. Beat/bar normalization requires real timestamps.
        # Keep malformed provider evidence in the raw provider record, but do not
        # let it poison the normalized harmony tables.
        identity_key = _chord_identity_key(item.get("chord"))
        if (
            start_sec is None
            or end_sec is None
            or not math.isfinite(start_sec)
            or not math.isfinite(end_sec)
            or start_sec < 0
            or end_sec <= start_sec
            or (duration_sec is not None and end_sec > duration_sec + 2.0)
            or identity_key is None
            or identity_key[1] not in QUALITY_INTERVALS
        ):
            skipped_count += 1
            continue
        item["start_sec"] = start_sec
        item["end_sec"] = end_sec
        item.setdefault("confidence", payload.get("confidence"))
        item.setdefault("method", "external")
        if payload.get("key") is not None:
            item.setdefault("provider_key", payload.get("key"))
        if payload.get("tempo") is not None:
            item.setdefault("provider_tempo", payload.get("tempo"))
        if payload.get("time_signature") is not None:
            item.setdefault("provider_time_signature", payload.get("time_signature"))
        item.setdefault("section_name", seg.get("section"))
        if payload.get("sections"):
            item["_provider_sections"] = payload.get("sections")
        if payload.get("chart_bars"):
            item["_provider_chart_bars"] = payload.get("chart_bars")
        conn.execute(
            """
            INSERT OR REPLACE INTO harmony_sources
              (track_id, source, source_type, source_url, section_name,
               start_sec, end_sec, chord, key, confidence, raw_data, observed_at)
            VALUES (?, ?, 'external', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                track_id, source, source_url, item.get("section_name"),
                start_sec, end_sec,
                item.get("chord"), payload.get("key"), item.get("confidence"),
                json.dumps(item, ensure_ascii=False), _now(),
            ),
        )
        _store_segment(conn, track_id, source, item)
        imported_count += 1
    conn.commit()
    return imported_count

def analyze_track_audio(conn, track_id, audio_path):
    """Deprecated: Jacques is streaming/link-only and never analyzes local released audio."""
    raise RuntimeError(
        "Local audio analysis is disabled. Register a Spotify or YouTube URL "
        "and import provider evidence instead."
    )

def _build_beat_grid(consensus, tempo, beats_per_bar=4, time_signature="4/4"):
    """Project timestamped chords onto a beat/bar grid without erasing intra-beat changes."""
    if not consensus or not tempo or tempo <= 0:
        return {
            "available": False,
            "reason": "missing_tempo_or_consensus",
            "time_signature": time_signature,
            "time_signature_source": "provider_or_default",
            "anchor_sec": None,
            "beat_duration_sec": None,
            "beats": [],
            "bars": [],
        }

    beat_duration = 60.0 / float(tempo)
    anchor = float(consensus[0]["start_sec"])
    last_end = max(float(item["end_sec"]) for item in consensus)
    beat_count = max(1, int((last_end - anchor) / beat_duration + 0.999999))

    beats = []
    for index in range(beat_count):
        start = anchor + index * beat_duration
        end = start + beat_duration
        overlapping = [
            item for item in consensus
            if float(item["end_sec"]) > start and float(item["start_sec"]) < end
        ]
        overlapping.sort(key=lambda item: float(item["start_sec"]))
        chords = []
        for item in overlapping:
            chord = item.get("chord")
            if chord and chord not in chords:
                chords.append(chord)

        beat_number = (index % beats_per_bar) + 1
        bar_number = (index // beats_per_bar) + 1
        snapped = []
        for item in overlapping:
            snapped_beat_index = round(
                (float(item["start_sec"]) - anchor) / beat_duration
            )
            snapped_beat = (snapped_beat_index % beats_per_bar) + 1
            snapped_bar = (snapped_beat_index // beats_per_bar) + 1
            snapped.append({
                "chord": item.get("chord"),
                "source_start_sec": round(float(item["start_sec"]), 4),
                "snapped_bar": snapped_bar,
                "snapped_beat": snapped_beat,
            })

        beats.append({
            "bar": bar_number,
            "beat": beat_number,
            "start_sec": round(start, 4),
            "end_sec": round(end, 4),
            "chord": chords[0] if len(chords) == 1 else (" → ".join(chords) if chords else None),
            "chords": chords,
            "changes": snapped,
            "roman_numerals": [
                item.get("roman_numeral") for item in overlapping
                if item.get("roman_numeral")
            ],
            "confidence": (
                sum(float(item.get("confidence") or 0.0) for item in overlapping) / len(overlapping)
                if overlapping else None
            ),
        })

    bars = []
    for bar_number in range(1, max((beat["bar"] for beat in beats), default=0) + 1):
        bar_beats = [beat for beat in beats if beat["bar"] == bar_number]
        bars.append({
            "bar": bar_number,
            "beats": bar_beats,
            "compact": " ".join(
                beat["chord"] or "." for beat in bar_beats
            ),
        })

    return {
        "available": True,
        "time_signature": time_signature,
        "time_signature_source": "provider_or_default",
        "anchor_sec": round(anchor, 4),
        "beat_duration_sec": round(beat_duration, 6),
        "beats": beats,
        "bars": bars,
        "grid_semantics": "relative_to_first_consensus_onset_with_onset_snapping",
    }


def _build_structure_map(consensus, beat_grid, explicit_sections=None):
    """Build a conservative bar-aware song structure map."""
    explicit_sections = explicit_sections or []
    bars = beat_grid.get("bars") or []
    if not bars or not consensus:
        return []

    def bar_for_sec(sec):
        beats = beat_grid.get("beats") or []
        if not beats:
            return 1
        nearest = min(beats, key=lambda beat: abs(float(beat["start_sec"]) - float(sec)))
        return int(nearest["bar"])

    sections = []
    for item in explicit_sections:
        try:
            start_sec = float(item["start_sec"])
            end_sec = float(item["end_sec"])
        except (TypeError, ValueError):
            continue
        if end_sec <= start_sec:
            continue
        start_bar = bar_for_sec(start_sec)
        end_bar = max(start_bar, bar_for_sec(max(start_sec, end_sec - 1e-6)))
        sections.append({
            "section": item.get("section") or item.get("label") or "Section",
            "start_bar": start_bar,
            "end_bar": end_bar,
            "start_sec": round(start_sec, 4),
            "end_sec": round(end_sec, 4),
            "chords": [beat.get("chord") for beat in beat_grid.get("beats", [])
                       if start_bar <= beat["bar"] <= end_bar and beat.get("chord")],
            "source": item.get("source", "provider"),
            "confidence": item.get("confidence"),
            "semantic_label_evidence": bool(item.get("explicit", True)),
        })
    if sections:
        sections.sort(key=lambda item: (item["start_bar"], item["end_bar"]))
        return sections

    return [{
        "section": "Section 01",
        "start_bar": min((bar["bar"] for bar in bars), default=1),
        "end_bar": max((bar["bar"] for bar in bars), default=1),
        "start_sec": round(float(consensus[0]["start_sec"]), 4),
        "end_sec": round(float(consensus[-1]["end_sec"]), 4),
        "chords": [beat.get("chord") for beat in beat_grid.get("beats", []) if beat.get("chord")],
        "source": "neutral_fallback",
        "confidence": None,
        "semantic_label_evidence": False,
    }]

def _build_prompt_harmony(beat_grid, structure_map=None):
    if not beat_grid.get("available"):
        return None
    lines = [
        f"TIME: {beat_grid['time_signature']}",
        f"BEAT: {beat_grid['beat_duration_sec']:.3f}s",
        "HARMONY GRID:",
    ]
    for bar in beat_grid["bars"]:
        lines.append(f"Bar {bar['bar']:02d} | " + " | ".join(
            f"{beat['beat']}:{beat['chord'] or '.'}"
            for beat in bar["beats"]
        ))
    if structure_map:
        lines.extend(["", "SONG STRUCTURE:"])
        for item in structure_map:
            lines.append(f"[{item['section']}] Bars {item['start_bar']:02d}-{item['end_bar']:02d}")
    return "\n".join(lines)


def _validate_harmony_profile(conn, track_id, profile):
    issues = []
    segments = profile.get("segments") or []
    consensus_evidence = profile.get("strict_consensus_segments") or []
    evidence_count = profile.get("evidence_count", 0)
    strict_mode = profile.get("analysis_status") == "strict_consensus"

    if profile.get("confidence") is not None and evidence_count == 0:
        issues.append("confidence_without_evidence")
    if profile.get("confidence") is not None and not strict_mode:
        issues.append("confidence_on_non_strict_profile")
    if any(item.get("source_count", 0) < MIN_CONSENSUS_SOURCES for item in segments):
        issues.append("single_provider_in_strict_segments")

    track = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tracks'"
    ).fetchone()
    duration_sec = None
    if track:
        row = conn.execute(
            "SELECT duration_ms FROM tracks WHERE track_id=?",
            (track_id,),
        ).fetchone()
        if row and row[0] is not None:
            duration_sec = float(row[0]) / 1000.0

    for item in segments:
        start = item.get("start_sec")
        end = item.get("end_sec")
        if (
            start is None
            or end is None
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or start < 0
            or end <= start
            or (duration_sec is not None and end > duration_sec + 2.0)
        ):
            issues.append("invalid_consensus_segment_interval")
            break

    for source, coverage in (profile.get("provider_coverage") or {}).items():
        if coverage.get("duration_sec", 0) <= 0:
            issues.append(f"empty_provider_coverage:{source}")

    policy = profile.get("consensus_policy") or {}
    if policy.get("strict_consensus_coverage", 0) > 1:
        issues.append("consensus_coverage_out_of_range")
    evidence_duration = policy.get("evidence_duration_sec")
    if (
        profile.get("analysis_status") != "quality_rejected"
        and evidence_duration
        and evidence_duration > 0
    ):
        intervals = sorted(
            (float(item["start_sec"]), float(item["end_sec"]))
            for item in consensus_evidence
            if item.get("start_sec") is not None and item.get("end_sec") is not None
        )
        covered = 0.0
        if intervals:
            current_start, current_end = intervals[0]
            for start, end in intervals[1:]:
                if start <= current_end:
                    current_end = max(current_end, end)
                else:
                    covered += current_end - current_start
                    current_start, current_end = start, end
            covered += current_end - current_start
        actual_coverage = covered / float(evidence_duration)
        if abs(actual_coverage - float(policy.get("strict_consensus_coverage", 0))) > 0.0002:
            issues.append("consensus_coverage_mismatch")
    if (
        policy.get("fusion_mode") == "strict_multi_source_consensus"
        and not segments
    ):
        issues.append("strict_mode_without_segments")

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='data_quality_issues'"
    ).fetchone()
    if exists:
        issue_type = "harmony_profile_qc"
        details = json.dumps(
            {"issues": sorted(set(issues)), "analysis_status": profile.get("analysis_status")},
            ensure_ascii=False,
        )
        if issues:
            conn.execute(
                """
                INSERT INTO data_quality_issues
                    (entity_type, entity_id, issue_type, severity, details, detected_at)
                VALUES ('harmony_profile', ?, ?, 'error', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(entity_type, entity_id, issue_type) DO UPDATE SET
                    severity='error', details=excluded.details,
                    detected_at=CURRENT_TIMESTAMP, resolved_at=NULL
                """,
                (track_id, issue_type, details),
            )
        else:
            conn.execute(
                """
                UPDATE data_quality_issues SET resolved_at=CURRENT_TIMESTAMP
                WHERE entity_type='harmony_profile' AND entity_id=?
                  AND issue_type=? AND resolved_at IS NULL
                """,
                (track_id, issue_type),
            )
    return issues


def fuse_track_harmony(conn, track_id):
    """Fuse up to five independent observations without collapsing harmonic identity.

    A progression is only promoted to consensus when at least two distinct
    sources agree on the same chord identity (root/quality/bass). Pitch-set
    equivalents such as Am7 and C6 remain distinct candidates and are exposed
    as ambiguity metadata instead of being silently merged.
    """
    has_provider_runs = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='harmony_provider_runs'"
    ).fetchone()
    provider_filter = (
        """
        AND NOT EXISTS (
            SELECT 1 FROM harmony_provider_runs latest
            WHERE latest.track_id = harmony_segments.track_id
              AND latest.provider = harmony_segments.source
              AND latest.execution_id = (
                  SELECT MAX(newer.execution_id)
                  FROM harmony_provider_runs newer
                  WHERE newer.track_id = latest.track_id
                    AND newer.provider = latest.provider
              )
              AND latest.status NOT IN ('success_with_data', 'success_empty')
        )
        """
        if has_provider_runs
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT source, section_name, start_sec, end_sec, chord, confidence
        FROM harmony_segments
        WHERE track_id=? AND chord IS NOT NULL
        {provider_filter}
        ORDER BY start_sec, source
        """,
        (track_id,),
    ).fetchall()
    if not rows:
        for table, where in (
            ("harmony_profiles", "track_id=?"),
            ("harmony_structures", "track_id=?"),
            ("harmony_consensus", "track_id=?"),
            (
                "music_analysis_evidence",
                "track_id=? AND domain='harmony' AND source='harmony_consensus'",
            ),
        ):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists:
                conn.execute(f"DELETE FROM {table} WHERE {where}", (track_id,))
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

    # Build atomic time windows first, then vote across providers inside each window.
    # This avoids anchor-order bias and makes disagreement/coverage measurable.
    boundaries = sorted({
        float(item["start_sec"]) for item in candidates
    } | {
        float(item["end_sec"]) for item in candidates
    })
    raw_consensus = []
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        active = []
        for source in sources:
            overlaps = [
                item for item in by_source[source]
                if float(item["end_sec"]) > left and float(item["start_sec"]) < right
            ]
            if not overlaps:
                continue
            # One vote per provider. Prefer the segment covering the largest
            # portion of this atomic window, then the highest source confidence.
            chosen = max(
                overlaps,
                key=lambda item: (
                    max(0.0, min(right, float(item["end_sec"])) - max(left, float(item["start_sec"]))),
                    float(item.get("confidence") or 0.5),
                ),
            )
            active.append(chosen)
        if len(active) < MIN_CONSENSUS_SOURCES:
            continue

        groups = defaultdict(list)
        for item in active:
            identity_key = _chord_identity_key(item["chord"])
            if identity_key is not None:
                groups[identity_key].append(item)
        if not groups:
            continue

        winner_key, winner_items = max(
            groups.items(),
            key=lambda pair: (
                len({item["source"] for item in pair[1]}),
                sum(float(item.get("confidence") or 0.5) for item in pair[1]),
            ),
        )
        supporting_sources = {item["source"] for item in winner_items}
        active_sources = {item["source"] for item in active}
        if len(supporting_sources) < MIN_CONSENSUS_SOURCES:
            continue

        anchor = winner_items[0]
        identity = _chord_identity(anchor["chord"])
        confidence_values = [
            float(item["confidence"])
            for item in winner_items
            if item.get("confidence") is not None
        ]
        agreement = len(supporting_sources) / max(1, len(active_sources))
        confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        competing = [item for item in active if item not in winner_items]
        raw_consensus.append({
            "start_sec": left,
            "end_sec": right,
            "chord": anchor["chord"],
            "chord_family": anchor["chord"],
            "root": identity["root"],
            "quality": identity["quality"],
            "bass": identity["bass"],
            "pitch_classes": identity["pitch_classes"],
            "pitch_set_equivalents": sorted({
                alt
                for item in active
                for alt in _chord_ambiguity(item["chord"])
                if alt != anchor["chord"]
            }),
            "confidence": confidence,
            "agreement": agreement,
            "source_count": len(supporting_sources),
            "evidence_count": len(winner_items),
            "sources": sorted(supporting_sources),
            "active_source_count": len(active_sources),
            "evidence": winner_items,
            "competing_evidence": competing,
        })

    # Merge adjacent windows with identical winning harmonic identity.
    consensus = []
    for item in raw_consensus:
        if consensus and consensus[-1]["end_sec"] == item["start_sec"] and _chord_identity_key(consensus[-1]["chord"]) == _chord_identity_key(item["chord"]):
            prev = consensus[-1]
            previous_duration = prev["end_sec"] - prev["start_sec"]
            next_duration = item["end_sec"] - item["start_sec"]
            if prev["confidence"] is None:
                merged_confidence = item["confidence"]
            elif item["confidence"] is None:
                merged_confidence = prev["confidence"]
            else:
                merged_confidence = (
                    prev["confidence"] * previous_duration
                    + item["confidence"] * next_duration
                ) / (previous_duration + next_duration)
            prev["end_sec"] = item["end_sec"]
            prev["confidence"] = merged_confidence
            prev["agreement"] = min(prev["agreement"], item["agreement"])
            prev["source_count"] = max(prev["source_count"], item["source_count"])
            prev["active_source_count"] = max(prev.get("active_source_count", 1), item.get("active_source_count", 1))
            prev["evidence"].extend(item["evidence"])
            prev["competing_evidence"].extend(item["competing_evidence"])
            prev["evidence_count"] += item["evidence_count"]
            prev["sources"] = sorted(set(prev["sources"]) | set(item["sources"]))
        else:
            consensus.append(item)

    strict_consensus = list(consensus)
    evidence_start = min(
        float(item["start_sec"]) for item in candidates
    ) if candidates else None
    evidence_end = max(
        float(item["end_sec"]) for item in candidates
    ) if candidates else None
    strict_intervals = sorted(
        (float(item["start_sec"]), float(item["end_sec"]))
        for item in strict_consensus
        if float(item["end_sec"]) > float(item["start_sec"])
    )
    strict_duration = 0.0
    if strict_intervals:
        cur_start, cur_end = strict_intervals[0]
        for start_sec, end_sec in strict_intervals[1:]:
            if start_sec <= cur_end:
                cur_end = max(cur_end, end_sec)
            else:
                strict_duration += cur_end - cur_start
                cur_start, cur_end = start_sec, end_sec
        strict_duration += cur_end - cur_start
    evidence_duration = (
        evidence_end - evidence_start
        if evidence_start is not None and evidence_end is not None
        else 0.0
    )
    strict_coverage = (
        strict_duration / evidence_duration
        if evidence_duration > 0 else 0.0
    )
    has_strict_profile = bool(
        strict_consensus
        and strict_coverage >= MIN_CONSENSUS_COVERAGE
    )

    fusion_mode = "strict_multi_source_consensus"
    provisional_segments = []
    if not has_strict_profile:
        fallback_source = max(
            sources,
            key=lambda source: (
                max(
                    (float(item["end_sec"]) for item in by_source[source]),
                    default=0.0,
                ) - min(
                    (float(item["start_sec"]) for item in by_source[source]),
                    default=0.0
                ),
                len(by_source[source]),
            ),
            default=None,
        )
        if fallback_source:
            provisional_segments = [
                {
                    **item,
                    "source": fallback_source,
                    "agreement": None,
                    "source_count": 1,
                    "evidence": [item],
                    "competing_evidence": [],
                    "confidence": None,
                    "evidence_class": "provisional_provider_evidence",
                    "fallback_reason": (
                        "Fewer than two independent providers agree across "
                        "the minimum strict-consensus coverage."
                    ),
                    "semantic_label_evidence": False,
                }
                for item in by_source[fallback_source]
                if (
                    float(item["end_sec"]) > float(item["start_sec"])
                    and _chord_identity_key(item.get("chord")) is not None
                    and _chord_identity_key(item.get("chord"))[1] in QUALITY_INTERVALS
                )
            ]
        fusion_mode = (
            "provisional_provider_baseline"
            if provisional_segments
            else "insufficient_evidence"
        )

    consensus = strict_consensus if has_strict_profile else []
    consensus.sort(key=lambda item: (item["start_sec"], item["end_sec"]))
    progression = [item["chord"] for item in consensus]

    harmony_source_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(harmony_sources)")
    }
    key_sql = (
        "SELECT key FROM harmony_sources WHERE track_id=? AND key IS NOT NULL"
    )
    key_params = [track_id]
    if "source" in harmony_source_columns:
        key_sql += " AND source IN (" + ", ".join("?" for _ in sources) + ")"
        key_params.extend(sources)
    key_sql += " ORDER BY confidence DESC"
    if "observed_at" in harmony_source_columns:
        key_sql += ", observed_at DESC"
    key_sql += " LIMIT 1"
    key_row = conn.execute(key_sql, key_params).fetchone()
    resolved_key = key_row[0] if key_row else None
    mode = None
    if resolved_key and " " in resolved_key:
        resolved_key, mode = resolved_key.rsplit(" ", 1)
    if resolved_key not in CHORD_ROOT_TO_PC:
        resolved_key = None
        mode = None

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
    if tempo is not None and (not math.isfinite(tempo) or not 30.0 <= tempo <= 300.0):
        tempo = None
    if tempo is None:
        tempo_rows = conn.execute(
            "SELECT raw_data FROM harmony_sources WHERE track_id=? "
            "ORDER BY confidence DESC, observed_at DESC",
            (track_id,),
        ).fetchall()
        for row in tempo_rows:
            try:
                raw = json.loads(row[0] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            candidate = raw.get("provider_tempo")
            try:
                candidate_tempo = float(candidate)
                if math.isfinite(candidate_tempo) and 30.0 <= candidate_tempo <= 300.0:
                    tempo = candidate_tempo
                    break
            except (TypeError, ValueError):
                continue

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

    time_signature = "4/4"
    source_clause = ""
    source_params = [track_id]
    if "source" in harmony_source_columns:
        placeholders = ", ".join("?" for _ in sources)
        source_clause = f" AND source IN ({placeholders})"
        source_params.extend(sources)
    raw_data_column = "raw_data" in harmony_source_columns
    time_rows = (
        conn.execute(
            "SELECT raw_data FROM harmony_sources WHERE track_id=?"
            + source_clause
            + (
                " ORDER BY observed_at DESC"
                if "observed_at" in harmony_source_columns
                else ""
            ),
            source_params,
        ).fetchall()
        if raw_data_column
        else []
    )
    for row in time_rows:
        try:
            raw = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        candidate = raw.get("provider_time_signature")
        if isinstance(candidate, str):
            parts = candidate.strip().split("/", 1)
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                time_signature = candidate.strip()
                break
    try:
        beats_per_bar = int(time_signature.split("/", 1)[0])
    except (TypeError, ValueError):
        beats_per_bar = 4
        time_signature = "4/4"

    beat_grid = _build_beat_grid(
        consensus,
        tempo,
        beats_per_bar=beats_per_bar,
        time_signature=time_signature,
    )

    chart_bars = []
    chart_rows = time_rows
    for row in chart_rows:
        try:
            raw = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if raw.get("chart_bars") or raw.get("_provider_chart_bars"):
            chart_bars = raw.get("chart_bars") or raw.get("_provider_chart_bars")
            break

    explicit_sections = []
    for row in chart_rows:
        try:
            raw = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if raw.get("sections") or raw.get("_provider_sections"):
            explicit_sections = raw.get("sections") or raw.get("_provider_sections")
            break

    analysis_status = (
        "strict_consensus"
        if consensus and strict_coverage >= MIN_CONSENSUS_COVERAGE
        else (
            "provisional_insufficient_evidence"
            if provisional_segments
            else "insufficient_evidence"
        )
    )
    has_strict_profile = analysis_status == "strict_consensus"
    structure_map = _build_structure_map(consensus, beat_grid, explicit_sections)
    prompt_harmony = (
        _build_prompt_harmony(beat_grid, structure_map)
        if has_strict_profile
        else None
    )
    if chart_bars and has_strict_profile:
        chart_lines = ["CHORD CHART BARS:"]
        for chart in chart_bars:
            chart_lines.append(
                f"Bar {int(chart['bar']):02d}: " + " → ".join(chart.get("chords", []))
            )
        prompt_harmony = (prompt_harmony + "\n\n" + "\n".join(chart_lines)) if prompt_harmony else "\n".join(chart_lines)

    provider_coverage = {}
    for source in sources:
        source_items = by_source[source]
        if source_items:
            source_start = min(float(item["start_sec"]) for item in source_items)
            source_end = max(float(item["end_sec"]) for item in source_items)
            provider_coverage[source] = {
                "start_sec": round(source_start, 4),
                "end_sec": round(source_end, 4),
                "duration_sec": round(max(0.0, source_end - source_start), 4),
                "segment_count": len(source_items),
            }

    unique_evidence = {
        (
            item.get("source"),
            item.get("start_sec"),
            item.get("end_sec"),
            item.get("chord"),
        )
        for segment in strict_consensus
        for item in segment.get("evidence", [])
    }
    confidence_segments = [
        item
        for item in strict_consensus
        if item.get("confidence") is not None
        and float(item["end_sec"]) > float(item["start_sec"])
    ]
    confidence_duration = sum(
        float(item["end_sec"]) - float(item["start_sec"])
        for item in confidence_segments
    )
    weighted_confidence = (
        sum(
            float(item["confidence"])
            * (float(item["end_sec"]) - float(item["start_sec"]))
            for item in confidence_segments
        )
        / confidence_duration
        if has_strict_profile and confidence_duration > 0
        else None
    )
    profile = {
        "analysis_status": analysis_status,
        "key": resolved_key,
        "mode": mode,
        "tempo": tempo,
        "time_signature": time_signature,
        "progression": progression,
        "provisional_progression": [
            item["chord"] for item in provisional_segments
        ],
        "roman_progression": [item["roman_numeral"] for item in consensus],
        "segments": consensus,
        "strict_consensus_segments": strict_consensus,
        "provisional_segments": provisional_segments,
        "beat_grid": beat_grid,
        "prompt_harmony": prompt_harmony,
        "chart_bars": chart_bars if has_strict_profile else [],
        "provisional_chart_bars": chart_bars if not has_strict_profile else [],
        "structure_map": structure_map,
        "harmonic_rhythm_sec": harmonic_rhythm,
        "chord_change_rate_per_sec": change_rate,
        "extensions": sorted({
            chord for chord in progression
            if any(token in chord for token in ("7", "sus", "dim", "aug", "6", "add"))
        }),
        "confidence": weighted_confidence,
        "confidence_calculation": {
            "method": "duration_weighted_reported_provider_confidence",
            "confidence_duration_sec": round(confidence_duration, 4),
            "confidence_segment_count": len(confidence_segments),
        },
        "evidence_count": len(unique_evidence),
        "provider_coverage": provider_coverage,
        "consensus_policy": {
            "max_sources": MAX_HARMONY_SOURCES,
            "minimum_agreeing_sources": MIN_CONSENSUS_SOURCES,
            "identity_rule": "root_pitch_class+quality+bass_pitch_class",
            "pitch_set_equivalents_are_not_merged": True,
            "fusion_mode": fusion_mode,
            "strict_consensus_segment_count": len(strict_consensus),
            "strict_consensus_coverage": round(strict_coverage, 4),
            "evidence_duration_sec": round(evidence_duration, 4),
            "minimum_consensus_coverage": MIN_CONSENSUS_COVERAGE,
        },
    }
    profile["qc_issues"] = _validate_harmony_profile(conn, track_id, profile)
    if profile["qc_issues"]:
        profile["analysis_status"] = "quality_rejected"
        profile["confidence"] = None
        profile["progression"] = []
        profile["roman_progression"] = []
        profile["segments"] = []
        profile["prompt_harmony"] = None
        profile["harmonic_rhythm_sec"] = None
        profile["chord_change_rate_per_sec"] = None
        profile["qc_issues"] = _validate_harmony_profile(conn, track_id, profile)

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
            json.dumps(structure_map, ensure_ascii=False),
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
    conn.execute(
        """
        DELETE FROM music_analysis_evidence
        WHERE track_id=? AND domain='harmony' AND source='harmony_consensus'
        """,
        (track_id,),
    )
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
