from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECTS_ROOT = PROJECT_ROOT / "projects"
STAGES = ("reference", "generations", "edits", "final")


def now():
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return value.strip("._-")[:120] or "untitled"


def create_project(
    conn: sqlite3.Connection,
    title: str,
    *,
    reference_track_id: str | None = None,
    root_dir: str | Path | None = None,
) -> tuple[int, Path]:
    root = Path(root_dir or DEFAULT_PROJECTS_ROOT).expanduser().resolve()
    project_key = slugify(title)
    project_root = root / project_key
    project_root.mkdir(parents=True, exist_ok=True)
    for stage in STAGES:
        (project_root / stage).mkdir(exist_ok=True)

    timestamp = now()
    cur = conn.execute(
        """
        INSERT INTO generation_projects
        (project_key, title, root_path, reference_track_id, status, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(project_key) DO UPDATE SET
            title=excluded.title,
            root_path=excluded.root_path,
            reference_track_id=COALESCE(excluded.reference_track_id, generation_projects.reference_track_id),
            updated_at=excluded.updated_at
        """,
        (project_key, title, str(project_root), reference_track_id,
         "active", timestamp, timestamp),
    )
    conn.commit()
    row = conn.execute(
        "SELECT project_id, root_path, reference_track_id FROM generation_projects WHERE project_key=?",
        (project_key,),
    ).fetchone()
    project_path = Path(row["root_path"] if isinstance(row, sqlite3.Row) else row[1])
    manifest = project_path / "project.json"
    import json
    manifest.write_text(json.dumps({
        "project_id": int(row["project_id"] if isinstance(row, sqlite3.Row) else row[0]),
        "project_key": project_key,
        "title": title,
        "reference_track_id": row["reference_track_id"] if isinstance(row, sqlite3.Row) else row[2],
        "stages": list(STAGES),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(row["project_id"] if isinstance(row, sqlite3.Row) else row[0]), project_path


def get_project(conn: sqlite3.Connection, project_id: int):
    return conn.execute(
        "SELECT * FROM generation_projects WHERE project_id=?", (project_id,)
    ).fetchone()


def update_project_status(conn: sqlite3.Connection, project_id: int, status: str) -> None:
    allowed = {"active", "paused", "completed", "archived"}
    if status not in allowed:
        raise ValueError(f"Unknown project status: {status}")
    cur = conn.execute(
        "UPDATE generation_projects SET status=?, updated_at=? WHERE project_id=?",
        (status, now(), project_id),
    )
    if cur.rowcount != 1:
        conn.rollback()
        raise ValueError(f"Unknown generation project: {project_id}")
    conn.commit()


def stage_dir(conn: sqlite3.Connection, project_id: int, stage: str) -> Path:
    if stage not in STAGES:
        raise ValueError(f"Unknown project stage: {stage}")
    row = get_project(conn, project_id)
    if row is None:
        raise ValueError(f"Unknown generation project: {project_id}")
    path = Path(row["root_path"]) / stage
    path.mkdir(parents=True, exist_ok=True)
    return path
