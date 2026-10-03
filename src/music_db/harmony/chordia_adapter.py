from __future__ import annotations

def chordia_estimate(audio_path, chord_dict="submission"):
    """Optional LV-Chordia evidence adapter.

    LV-Chordia is kept optional because its PyTorch/model footprint is much
    heavier than Jacques' baseline audio stack. Raw model labels are converted
    to Jacques' normalized chord representation without treating the model as
    ground truth.
    """
    try:
        from lv_chordia.chord_recognition import chord_recognition
    except ImportError as exc:
        raise RuntimeError(
            "LV-Chordia is optional. Install it before enabling this source."
        ) from exc

    rows = chord_recognition(audio_path, chord_dict_name=chord_dict)
    segments = []
    for row in rows or []:
        chord = row.get("chord")
        if not chord:
            continue
        start = row.get("start_time", row.get("start_sec"))
        end = row.get("end_time", row.get("end_sec"))
        if start is None or end is None:
            continue
        # JAMS-style labels such as A:min7 -> Jacques-style Am7.
        root, sep, quality = chord.partition(":")
        quality_map = {
            "maj": "",
            "min": "m",
            "maj7": "maj7",
            "min7": "m7",
            "7": "7",
            "dim": "dim",
            "aug": "aug",
        }
        normalized = root + quality_map.get(quality, quality)
        segments.append({
            "start_sec": float(start),
            "end_sec": float(end),
            "chord": normalized,
            "confidence": None,
            "method": "lv_chordia",
            "raw_chord": chord,
        })
    return {"segments": segments, "method": "lv_chordia"}
