from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "probe_artifacts"
OUT_MD = ARTIFACT_DIR / "top_track_analysis.md"
OUT_JSON = ARTIFACT_DIR / "top_track_analysis.json"


def load_results():
    rows = []
    for path in sorted(ARTIFACT_DIR.glob("result_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("track_id"):
            rows.append(data)
    return rows


def provider_summary(data):
    out = []
    for r in data.get("results") or []:
        hp = r.get("harmony_payload") or {}
        out.append({
            "provider": r.get("provider"),
            "status": r.get("status"),
            "segments": r.get("normalized_segment_count", hp.get("segment_count", 0)),
            "error_type": r.get("error_type"),
            "error": r.get("error"),
            "source_url": hp.get("source_url") or r.get("source_url") or r.get("provider_url"),
            "matched_video_url": r.get("matched_video_url") or r.get("youtube_url"),
            "submitted_at": r.get("submitted_at"),
            "completed_at": r.get("completed_at"),
            "confidence": hp.get("confidence"),
            "parser_version": hp.get("parser_version") or r.get("parser_version"),
            "raw_result_available": (r.get("provider_state") or {}).get(
                "raw_result_available", False
            ) if r.get("provider_state") else bool(r.get("raw_result_available")),
        })
    return out


def metrics(profile):
    progression = profile.get("progression") or []
    beats = (profile.get("beat_grid") or {}).get("beats") or []
    bars = (profile.get("beat_grid") or {}).get("bars") or []
    return {
        "segment_count": len(progression),
        "unique_chord_count": len(set(progression)),
        "extensions": profile.get("extensions") or [],
        "harmonic_rhythm_sec": profile.get("harmonic_rhythm_sec"),
        "chord_change_rate_per_sec": profile.get("chord_change_rate_per_sec"),
        "intra_beat_change_count": sum(1 for b in beats if len(b.get("changes") or []) > 1),
        "bar_count": len(bars),
        "distinct_bar_patterns": len(set(b.get("compact") for b in bars)),
        "common_chords": Counter(progression).most_common(8),
    }


def make_report(data, rank):
    p = data.get("harmony_profile") or {}
    policy = p.get("consensus_policy") or {}
    return {
        "rank": rank,
        "track_id": data.get("track_id"),
        "youtube_url": data.get("youtube_url"),
        "providers": provider_summary(data),
        "harmony": {
            "analysis_status": p.get("analysis_status", "unknown"),
            "key": p.get("key"),
            "mode": p.get("mode"),
            "tempo": p.get("tempo"),
            "time_signature": p.get("time_signature"),
            "confidence": p.get("confidence"),
            "fusion_mode": policy.get("fusion_mode"),
            "strict_consensus_segment_count": policy.get("strict_consensus_segment_count"),
            "strict_consensus_coverage": policy.get("strict_consensus_coverage"),
            "minimum_consensus_coverage": policy.get("minimum_consensus_coverage"),
            "evidence_count": p.get("evidence_count", 0),
            "actual_sources": sorted(
                {
                    source
                    for segment in (
                        p.get("segments") or p.get("provisional_segments") or []
                    )
                    for source in (segment.get("sources") or [])
                    + ([segment.get("source")] if segment.get("source") else [])
                }
            ),
            "provisional_progression": p.get("provisional_progression") or [],
            "fallback_reasons": sorted(
                {
                    segment.get("fallback_reason")
                    for segment in (p.get("structure_map") or [])
                    + (p.get("provisional_segments") or [])
                    if segment.get("fallback_reason")
                }
            ),
            "qc_issues": p.get("qc_issues") or [],
            "evidence_classes": sorted(
                {
                    item.get("evidence_class")
                    for item in (p.get("segments") or [])
                    + (p.get("provisional_segments") or [])
                    + (p.get("structure_map") or [])
                    if item.get("evidence_class")
                }
                | {
                    item.get("roman_numeral_evidence_class")
                    for item in (p.get("segments") or [])
                    if item.get("roman_numeral_evidence_class")
                }
            ),
            "fallback_used": any(
                item.get("source") == "neutral_fallback"
                or item.get("evidence_class") == "fallback_placeholder"
                for item in (p.get("structure_map") or [])
                + (p.get("provisional_segments") or [])
            ),
            "provisional_provider_fallback_used": bool(
                p.get("provisional_segments")
            ),
            "grid_semantics": (p.get("beat_grid") or {}).get("grid_semantics"),
        },
        "structure_map": p.get("structure_map") or [],
        "metrics": metrics(p),
        "prompt_harmony": p.get("prompt_harmony"),
    }


def markdown(r):
    h = r["harmony"]
    m = r["metrics"]
    provider_results = ", ".join(
        "{}={} ({} segments)".format(
            provider["provider"], provider["status"], provider["segments"]
        )
        for provider in r["providers"]
    ) or "none"
    lines = [
        f'## {r["rank"]}. {r["track_id"]}',
        f'- YouTube: {r["youtube_url"]}',
        f'- Key / Tempo / Meter: {h["key"]} / {h["tempo"]} BPM / {h["time_signature"]}',
        f'- Analysis status: {h["analysis_status"]}; sources: {", ".join(h["actual_sources"]) or "none"}; evidence count: {h["evidence_count"]}',
        f'- Fusion: {h["fusion_mode"]}; strict coverage: {h["strict_consensus_coverage"]}',
        f'- Confidence: {h["confidence"]}',
        f'- QC issues: {", ".join(h["qc_issues"]) or "none"}',
        f'- Fallback used: {h["fallback_used"]}; reasons: {", ".join(h["fallback_reasons"]) or "none"}',
        f'- Provisional provider fallback used: {h["provisional_provider_fallback_used"]}',
        f'- Provisional-only progression (not consensus): {", ".join(h["provisional_progression"]) or "none"}',
        f'- Provider results: {provider_results}',
        f'- Evidence classes: {", ".join(h["evidence_classes"]) or "none"}',
        f'- Harmony segments: {m["segment_count"]}; unique chords: {m["unique_chord_count"]}',
        f'- Harmonic rhythm: {m["harmonic_rhythm_sec"]} sec/chord; change rate: {m["chord_change_rate_per_sec"]}',
        f'- Intra-beat changes: {m["intra_beat_change_count"]}; bars: {m["bar_count"]}; distinct bar patterns: {m["distinct_bar_patterns"]}',
        f'- Extensions: {", ".join(m["extensions"]) or "none detected"}',
        "",
        "### Structure",
    ]
    if r["structure_map"]:
        for s in r["structure_map"]:
            lines.append(
                f'- {s.get("section")}: Bars {s.get("start_bar")}-{s.get("end_bar")} '
                f'({s.get("start_sec")}-{s.get("end_sec")}s; source={s.get("source")})'
            )
    else:
        lines.append("- No explicit semantic structure evidence.")
    lines += [
        "",
        "### Common chords",
        "- " + ", ".join(f"{c} x{n}" for c, n in m["common_chords"]),
        "",
        "### Prompt-ready harmony",
        "~~~text",
        r["prompt_harmony"] or "(unavailable)",
        "~~~",
        "",
    ]
    return "\n".join(lines)


def main():
    rows = load_results()
    reports = [make_report(data, i) for i, data in enumerate(rows, 1)]
    OUT_JSON.write_text(
        json.dumps({"track_count": len(reports), "tracks": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = ["# Jacques Top Track Analysis", "", "Prompt-ready harmony and structural evidence.", ""]
    md.extend(markdown(r) for r in reports)
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"Generated {OUT_MD} and {OUT_JSON} for {len(reports)} tracks.")


if __name__ == "__main__":
    main()
