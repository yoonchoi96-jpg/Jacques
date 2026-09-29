from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.generation.audio_analysis import analyze_audio
from music_db.generation.compare import compare


def main():
    p = argparse.ArgumentParser(description="Compare two legally available local audio files")
    p.add_argument("audio_a")
    p.add_argument("audio_b")
    args = p.parse_args()
    a = analyze_audio(args.audio_a)
    b = analyze_audio(args.audio_b)
    print(json.dumps(compare(a, b), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
