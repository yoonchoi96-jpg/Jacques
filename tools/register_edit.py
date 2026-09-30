from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.outputs import create_derivative_output
from music_db.generation.audio_analysis import analyze_and_store


def main():
    p = argparse.ArgumentParser(description="Register a locally edited Jacques output")
    p.add_argument("source_output_id", type=int)
    p.add_argument("audio_path")
    p.add_argument("--title", default="Local edited derivative")
    p.add_argument("--note", default=None)
    p.add_argument("--stage", choices=("edits", "final"), default="edits")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        output_id = create_derivative_output(
            conn,
            args.source_output_id,
            args.audio_path,
            stage=args.stage,
            title=args.title,
            note=args.note,
        )
        analyze_and_store(conn, output_id, args.audio_path)
    print(f"output_id={output_id}")
    print(f"stage={args.stage}")
    print(f"source_output_id={args.source_output_id}")


if __name__ == "__main__":
    main()
