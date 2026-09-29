from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_audio(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    stat = p.stat()
    return {
        "sha256": sha256_file(str(p)),
        "size_bytes": stat.st_size,
        "path": str(p),
    }


def attach_fingerprint(conn: sqlite3.Connection, output_id: int, path: str) -> dict:
    fp = fingerprint_audio(path)
    conn.execute(
        """UPDATE generation_outputs
           SET fingerprint_sha256=?
           WHERE output_id=?""",
        (fp["sha256"], output_id),
    )
    conn.commit()
    return fp
