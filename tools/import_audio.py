from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.assets import import_audio_asset


def main():
    p = argparse.ArgumentParser(description="Import local audio into a Jacques project")
    p.add_argument("project_id", type=int)
    p.add_argument("audio")
    p.add_argument("--type", choices=("reference", "generations", "edits", "final"), default="reference")
    p.add_argument("--title", default=None)
    p.add_argument("--move", action="store_true")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        asset_id, path = import_audio_asset(
            conn, args.project_id, args.audio,
            asset_type=args.type, title=args.title, move=args.move,
        )
    print(f"asset_id={asset_id}")
    print(f"asset_type={args.type}")
    print(f"audio_path={path}")


if __name__ == "__main__":
    main()
