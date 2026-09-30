from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .fingerprint import sha256_file
from .projects import stage_dir, STAGES


def now():
    return datetime.now(timezone.utc).isoformat()


def import_audio_asset(
    conn: sqlite3.Connection,
    project_id: int,
    source: str | Path,
    *,
    asset_type: str = "reference",
    title: str | None = None,
    move: bool = False,
    metadata: dict | None = None,
) -> tuple[int, Path]:
    if asset_type not in STAGES:
        raise ValueError(f"Unknown asset type: {asset_type}")
    src = Path(source).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(src)

    destination_dir = stage_dir(conn, project_id, asset_type)
    destination = destination_dir / src.name
    if src != destination:
        if move:
            shutil.move(str(src), str(destination))
        else:
            shutil.copy2(str(src), str(destination))

    fingerprint = sha256_file(str(destination))
    cur = conn.execute(
        """INSERT INTO generation_assets
           (project_id, asset_type, title, audio_path, fingerprint_sha256,
            metadata_json, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            project_id, asset_type, title or src.stem, str(destination),
            fingerprint,
            json.dumps(metadata or {}, ensure_ascii=False),
            now(),
        ),
    )
    conn.commit()
    return cur.lastrowid, destination
