from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.generation.audio_analysis import analyze_audio
from music_db.generation.prompt_builder import build_prompt


def main():
    p = argparse.ArgumentParser(description="Build a generation prompt from local-audio analysis")
    p.add_argument("audio")
    p.add_argument("--genre", default="")
    p.add_argument("--mood", default="")
    p.add_argument("--vocal", default="")
    p.add_argument("--notes", default="")
    args = p.parse_args()
    features = analyze_audio(args.audio)
    print(build_prompt(
        features, genre=args.genre, mood=args.mood,
        vocal=args.vocal, reference_notes=args.notes,
    ))


if __name__ == "__main__":
    main()
