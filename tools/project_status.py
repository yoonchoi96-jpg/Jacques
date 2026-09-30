from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection


def main():
    p = argparse.ArgumentParser(description="Show Jacques production project status")
    p.add_argument("project_id", type=int)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        project = conn.execute(
            "SELECT * FROM generation_projects WHERE project_id=?",
            (args.project_id,),
        ).fetchone()
        if project is None:
            raise SystemExit(f"Unknown project_id: {args.project_id}")

        assets = conn.execute(
            "SELECT asset_type, COUNT(*) AS count FROM generation_assets WHERE project_id=? GROUP BY asset_type",
            (args.project_id,),
        ).fetchall()
        outputs = conn.execute(
            """SELECT stage, COUNT(*) AS count,
                      SUM(CASE WHEN audio_path IS NOT NULL THEN 1 ELSE 0 END) AS local_count
               FROM generation_outputs WHERE project_id=? GROUP BY stage""",
            (args.project_id,),
        ).fetchall()
        jobs = conn.execute(
            "SELECT status, COUNT(*) AS count FROM generation_jobs WHERE project_id=? GROUP BY status",
            (args.project_id,),
        ).fetchall()

    result = {
        "project_id": project["project_id"],
        "project_key": project["project_key"],
        "title": project["title"],
        "status": project["status"],
        "root_path": project["root_path"],
        "reference_track_id": project["reference_track_id"],
        "assets": {r["asset_type"]: r["count"] for r in assets},
        "outputs": {
            r["stage"]: {"count": r["count"], "local_count": r["local_count"]}
            for r in outputs
        },
        "jobs": {r["status"]: r["count"] for r in jobs},
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"Project : {result['title']} (#{result['project_id']})")
    print(f"Status  : {result['status']}")
    print(f"Root    : {result['root_path']}")
    print(f"Reference track: {result['reference_track_id'] or '-'}")
    print("Assets  :", result["assets"] or "-")
    print("Outputs :", result["outputs"] or "-")
    print("Jobs    :", result["jobs"] or "-")


if __name__ == "__main__":
    main()
