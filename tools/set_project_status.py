from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.projects import update_project_status


def main():
    p = argparse.ArgumentParser(description="Update Jacques production project status")
    p.add_argument("project_id", type=int)
    p.add_argument("status", choices=("active", "paused", "completed", "archived"))
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        update_project_status(conn, args.project_id, args.status)
    print(f"project_id={args.project_id}")
    print(f"status={args.status}")


if __name__ == "__main__":
    main()
