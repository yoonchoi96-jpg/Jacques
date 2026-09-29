from __future__ import annotations

import json
import math


KEY_ENHARMONIC = {
    "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab", "A#": "Bb",
}


NUMERIC = [
    "duration_seconds", "tempo_bpm", "rms_db", "peak_db",
    "crest_factor_db", "spectral_centroid_hz", "spectral_rolloff_hz",
    "zero_crossing_rate", "onset_rate_per_second", "beat_count",
    "spectral_bandwidth_hz", "spectral_flatness", "silence_ratio",
    "dynamic_range_estimate_db", "key_confidence",
    "stereo_correlation", "stereo_width",
]


def compare(a: dict, b: dict) -> dict:
    out = {}
    for key in NUMERIC:
        av, bv = a.get(key), b.get(key)
        if (
            isinstance(av, (int, float))
            and isinstance(bv, (int, float))
            and math.isfinite(float(av))
            and math.isfinite(float(bv))
        ):
            delta = bv - av
            pct = None if av == 0 else (delta / abs(av)) * 100
            out[key] = {"a": av, "b": bv, "delta": delta, "percent": pct}
    for key in ("mfcc_mean", "mfcc_std", "spectral_contrast_mean_db"):
        av, bv = a.get(key), b.get(key)
        if (
            isinstance(av, list) and isinstance(bv, list)
            and av and len(av) == len(bv)
            and all(
                isinstance(x, (int, float)) and math.isfinite(float(x))
                for x in av + bv
            )
        ):
            deltas = [y - x for x, y in zip(av, bv)]
            out[key] = {
                "a": av,
                "b": bv,
                "mean_absolute_delta": sum(abs(x) for x in deltas) / len(deltas),
                "euclidean_distance": math.sqrt(sum(x * x for x in deltas)),
            }

    if a.get("estimated_key") and b.get("estimated_key"):
        out["estimated_key"] = {
            "a": a["estimated_key"], "b": b["estimated_key"],
            "same": (
                a["estimated_key"] == b["estimated_key"]
                or KEY_ENHARMONIC.get(a["estimated_key"]) == b["estimated_key"]
                or KEY_ENHARMONIC.get(b["estimated_key"]) == a["estimated_key"]
            ),
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
