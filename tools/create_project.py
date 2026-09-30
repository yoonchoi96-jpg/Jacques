from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.projects import create_project


def main():
    p = argparse.ArgumentParser(description="Create a Jacques music production project")
    p.add_argument("title")
    p.add_argument("--reference-track-id", default=None)
    p.add_argument("--root", default=None, help="Optional production projects root")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        project_id, root = create_project(
            conn,
            args.title,
            reference_track_id=args.reference_track_id,
            root_dir=args.root,
        )
    print(f"project_id={project_id}")
    print(f"project_root={root}")
    for stage in ("reference", "generations", "edits", "final"):
        print(f"{stage:12} {root / stage}")


if __name__ == "__main__":
    main()
