from __future__ import annotations

import json
import math


NUMERIC = [
    "duration_seconds", "tempo_bpm", "rms_db", "peak_db",
    "crest_factor_db", "spectral_centroid_hz", "spectral_rolloff_hz",
    "zero_crossing_rate", "onset_rate_per_second", "beat_count",
    "spectral_bandwidth_hz", "spectral_flatness", "silence_ratio",
]


def compare(a: dict, b: dict) -> dict:
    out = {}
    for key in NUMERIC:
        av, bv = a.get(key), b.get(key)
        if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            delta = bv - av
            pct = None if av == 0 else (delta / abs(av)) * 100
            out[key] = {"a": av, "b": bv, "delta": delta, "percent": pct}
    if a.get("estimated_key") and b.get("estimated_key"):
        out["estimated_key"] = {
            "a": a["estimated_key"], "b": b["estimated_key"],
            "same": a["estimated_key"] == b["estimated_key"],
        }
    return out


def save_comparison(conn, output_a, output_b, payload):
    conn.execute(
        """INSERT INTO generation_analysis
           (output_id, analysis_type, payload_json, created_at)
           VALUES (?, 'output_comparison_v1', ?, CURRENT_TIMESTAMP)""",
        (output_b, json.dumps({
            "compared_output_id": output_a,
            "comparison": payload,
        }, ensure_ascii=False)),
    )
    conn.commit()
