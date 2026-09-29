from __future__ import annotations


def build_prompt(features: dict, *, genre="", mood="", vocal="",
                 reference_notes=""):
    parts = []
    if genre: parts.append(f"genre: {genre}")
    if mood: parts.append(f"mood: {mood}")
    if vocal: parts.append(f"vocal: {vocal}")
    if features.get("tempo_bpm"):
        parts.append(f"tempo around {round(features['tempo_bpm'])} BPM")
    if features.get("estimated_key"):
        parts.append(f"key center around {features['estimated_key']}")
    if features.get("spectral_centroid_hz"):
        c = features["spectral_centroid_hz"]
        parts.append("bright spectral profile" if c > 3500 else "warm/dark spectral profile")
    if features.get("dynamic_range_estimate_db") is not None:
        dr = features["dynamic_range_estimate_db"]
        parts.append("controlled dynamics" if dr < 12 else "wide dynamics")
    if features.get("spectral_flatness") is not None:
        flat = features["spectral_flatness"]
        parts.append("tonal/harmonic texture" if flat < 0.2 else "noisy/textural character")
    if features.get("silence_ratio") is not None and features["silence_ratio"] > 0.12:
        parts.append("noticeable negative space")
    if features.get("key_confidence") is not None and features["key_confidence"] < 0.08:
        parts.append("ambiguous tonal center")
    if features.get("onset_rate_per_second"):
        parts.append(
            "dense rhythmic articulation"
            if features["onset_rate_per_second"] > 3
            else "spacious rhythmic articulation"
        )
    if reference_notes:
        parts.append(reference_notes)
    return ", ".join(parts)
