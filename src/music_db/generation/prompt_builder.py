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
    if features.get("onset_rate_per_second"):
        parts.append(
            "dense rhythmic articulation"
            if features["onset_rate_per_second"] > 3
            else "spacious rhythmic articulation"
        )
    if reference_notes:
        parts.append(reference_notes)
    return ", ".join(parts)
