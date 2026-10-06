from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _load(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    segments = payload.get("segments") or payload.get("chords") or []
    normalized = []
    for item in segments:
        start = float(item.get("start_sec", item.get("start")))
        end = float(item.get("end_sec", item.get("end")))
        chord = str(item.get("chord") or "").strip()
        if end > start and chord:
            normalized.append({"start_sec": start, "end_sec": end, "chord": chord})
    normalized.sort(key=lambda x: (x["start_sec"], x["end_sec"]))
    payload["segments"] = normalized
    payload["segment_count"] = len(normalized)
    return payload


def _split(chord: str):
    if chord.upper() in {"N", "NO_CHORD", "N.C."}:
        return None, None, None
    root = chord[:2] if len(chord) > 1 and chord[1] in "#b" else chord[:1]
    remainder = chord[len(root):]
    if "/" in remainder:
        quality, bass = remainder.split("/", 1)
    else:
        quality, bass = remainder, None
    return root, quality or "", bass


def _identity(chord: str):
    root, quality, bass = _split(chord)
    if root is None:
        return None
    pcs = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
           "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
           "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    return (pcs.get(root), quality, pcs.get(bass) if bass else None)


def _overlap(a, b):
    return max(0.0, min(a["end_sec"], b["end_sec"]) - max(a["start_sec"], b["start_sec"]))


def compare(a: dict, b: dict) -> dict:
    boundaries = sorted(
        {x["start_sec"] for x in a["segments"]} |
        {x["end_sec"] for x in a["segments"]} |
        {x["start_sec"] for x in b["segments"]} |
        {x["end_sec"] for x in b["segments"]}
    )
    agreement = 0.0
    comparable = 0.0
    disagreements = []
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        aa = next((x for x in a["segments"] if x["start_sec"] < right and x["end_sec"] > left), None)
        bb = next((x for x in b["segments"] if x["start_sec"] < right and x["end_sec"] > left), None)
        if not aa or not bb:
            continue
        duration = right - left
        comparable += duration
        if _identity(aa["chord"]) == _identity(bb["chord"]):
            agreement += duration
        elif len(disagreements) < 20:
            disagreements.append({
                "start_sec": round(left, 4),
                "end_sec": round(right, 4),
                "a": aa["chord"],
                "b": bb["chord"],
            })
    return {
        "source_a": a.get("source"),
        "source_b": b.get("source"),
        "duration_comparable_sec": round(comparable, 4),
        "agreement_sec": round(agreement, 4),
        "agreement_ratio": round(agreement / comparable, 4) if comparable else 0.0,
        "strict_two_source_eligible": comparable > 0 and agreement / comparable >= 0.20,
        "disagreements_sample": disagreements,
    }


def main():
    parser = argparse.ArgumentParser(description="Runner-free BTC harmony QC and two-source comparison")
    parser.add_argument("--audio", required=True, help="Local MP3/WAV/M4A/FLAC file")
    parser.add_argument("--reference-json", help="Independent provider JSON, e.g. Methodic Truth artifact")
    parser.add_argument("--output", default="probe_artifacts/btc_local_qc.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    btc_out = out.with_name(out.stem + "_btc.json")

    proc = subprocess.run(
        [sys.executable, str(root / "tools/btc_local_chord_provider.py"),
         "--audio", args.audio, "--output", str(btc_out)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "BTC inference failed.\nSTDOUT:\n" + proc.stdout[-6000:] +
            "\nSTDERR:\n" + proc.stderr[-6000:]
        )

    btc = _load(str(btc_out))
    result = {
        "status": "btc_success",
        "audio": str(Path(args.audio).resolve()),
        "btc": {
            "model": btc.get("model"),
            "segment_count": btc.get("segment_count"),
            "vocabulary": btc.get("vocabulary"),
        },
    }

    if args.reference_json:
        reference = _load(args.reference_json)
        result["comparison"] = compare(reference, btc)
        result["status"] = (
            "strict_two_source_candidate"
            if result["comparison"]["strict_two_source_eligible"]
            else "insufficient_two_source_agreement"
        )

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
