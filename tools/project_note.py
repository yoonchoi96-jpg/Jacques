from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection


def main():
    p = argparse.ArgumentParser(description="Add a note to a Jacques production project")
    p.add_argument("project_id", type=int)
    p.add_argument("body")
    p.add_argument("--type", default="production")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT project_id FROM generation_projects WHERE project_id=?",
            (args.project_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"Unknown project_id: {args.project_id}")
        conn.execute(
            """INSERT INTO generation_project_notes
               (project_id, note_type, body, created_at)
               VALUES (?,?,?,?)""",
            (
                args.project_id,
                args.type,
                args.body,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    print(f"project_id={args.project_id}")
    print(f"type={args.type}")
    print("saved=true")


if __name__ == "__main__":
    main()
