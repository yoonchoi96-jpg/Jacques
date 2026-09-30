from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.projects import stage_dir


def main():
    p = argparse.ArgumentParser(description="Promote a Jacques output into another project stage")
    p.add_argument("output_id", type=int)
    p.add_argument("--stage", choices=("reference", "generations", "edits", "final"), required=True)
    p.add_argument("--move", action="store_true", help="Move instead of copy; original path is removed")
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        row = conn.execute(
            """SELECT o.output_id, o.audio_path, o.project_id, o.stage,
                      p.title AS project_title
               FROM generation_outputs o
               LEFT JOIN generation_projects p ON p.project_id=o.project_id
               WHERE o.output_id=?""",
            (args.output_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"Unknown output_id: {args.output_id}")
        if not row["audio_path"]:
            raise SystemExit("Output has no local audio_path")
        if not row["project_id"]:
            raise SystemExit("Output is not attached to a generation project")

        source = Path(row["audio_path"]).expanduser().resolve()
        if not source.exists():
            raise SystemExit(f"Audio file does not exist: {source}")

        if args.stage == "final":
            sibling = conn.execute(
                "SELECT output_id FROM generation_outputs WHERE project_id=? AND stage=? AND output_id<>?",
                (row["project_id"], "final", args.output_id),
            ).fetchone()
            if sibling:
                raise SystemExit(
                    f"Project already has final output_id={sibling['output_id']}; "
                    "promote only one final output"
                )

        destination_dir = stage_dir(conn, row["project_id"], args.stage)
        destination = destination_dir / source.name
        if source != destination:
            if args.move:
                shutil.move(str(source), str(destination))
            else:
                shutil.copy2(str(source), str(destination))

        conn.execute(
            "UPDATE generation_outputs SET audio_path=?, stage=? WHERE output_id=?",
            (str(destination), args.stage, args.output_id),
        )
        conn.commit()

        print(f"output_id={args.output_id}")
        print(f"project={row['project_title'] or row['project_id']}")
        print(f"stage={args.stage}")
        print(f"audio_path={destination}")
        print(f"mode={'move' if args.move else 'copy'}")


if __name__ == "__main__":
    main()
