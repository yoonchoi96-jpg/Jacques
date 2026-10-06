from __future__ import annotations

import argparse
import json
from pathlib import Path

from transformers import AutoModel

MODEL_ID = "puar-playground/btc-chord"


def analyze(audio_path: str, output_path: str) -> dict:
    model = AutoModel.from_pretrained(MODEL_ID, trust_remote_code=True, large_voca=True)
    segments = model.predict(audio_path)
    normalized = []
    for item in segments:
        start = float(item["start"])
        end = float(item["end"])
        if end <= start:
            continue
        normalized.append({
            "start_sec": round(start, 4),
            "end_sec": round(end, 4),
            "chord": str(item["chord"]),
        })
    if not normalized:
        raise RuntimeError("BTC returned no chord segments")
    payload = {
        "source": "btc_local",
        "source_url": "https://huggingface.co/puar-playground/btc-chord",
        "confidence": None,
        "segments": normalized,
        "segment_count": len(normalized),
        "method": "btc_transformer_local",
        "model": MODEL_ID,
        "vocabulary": 170,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.audio, args.output), ensure_ascii=False, indent=2))

# QC trigger
