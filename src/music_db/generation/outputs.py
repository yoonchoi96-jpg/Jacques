from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def _walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def extract_audio_refs(response):
    """Best-effort extraction from provider response shapes."""
    refs = []
    seen = set()
    for key, value in _walk(response or {}):
        if not isinstance(value, str):
            continue
        lk = str(key).lower()
        explicit_audio = any(
            x in lk for x in (
                "audio_url", "audio_path", "audio_file", "audio_output",
                "output_audio", "file_url", "file_path"
            )
        )
        generic_url = lk in ("url", "path", "output", "file")
        audio_ext = Path(value.split("?", 1)[0].lower()).suffix in {
            ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".webm"
        }
        if (explicit_audio or (generic_url and audio_ext)):
            if value.startswith(("http://", "https://", "file://", "/")):
                if value not in seen:
                    seen.add(value)
                    refs.append(value)
    return refs


def create_output(conn: sqlite3.Connection, job_id, *, output_index=0,
                  audio_path=None, audio_url=None, metadata=None,
                  project_id=None, stage="generation"):
    metadata = metadata or {}
    cur = conn.execute(
        """INSERT INTO generation_outputs
        (job_id, output_index, audio_path, audio_url, duration_seconds,
         bpm, key_scale, sample_rate, format, analysis_json, project_id, stage, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id, output_index, audio_path, audio_url,
            metadata.get("duration_seconds"), metadata.get("bpm"),
            metadata.get("key_scale"), metadata.get("sample_rate"),
            metadata.get("format"),
            json.dumps(metadata.get("analysis"), ensure_ascii=False)
            if metadata.get("analysis") is not None else None,
            project_id, stage, now(),
        ),
    )
    conn.commit()
    output_id = cur.lastrowid
    if audio_path:
        try:
            digest = hashlib.sha256()
            with open(audio_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            conn.execute(
                "UPDATE generation_outputs SET fingerprint_sha256=? WHERE output_id=?",
                (digest.hexdigest(), output_id),
            )
            conn.commit()
        except (OSError, ValueError):
            pass
    return output_id


def create_derivative_output(
    conn: sqlite3.Connection,
    source_output_id: int,
    audio_path: str,
    *,
    stage: str = "edits",
    title: str | None = None,
    note: str | None = None,
):
    """Register a locally edited derivative and link it to its source output."""
    source = conn.execute(
        """SELECT o.*, j.project_id AS job_project_id
           FROM generation_outputs o
           JOIN generation_jobs j ON j.job_id=o.job_id
           WHERE o.output_id=?""",
        (source_output_id,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Unknown source output: {source_output_id}")
    if not Path(audio_path).expanduser().is_file():
        raise FileNotFoundError(audio_path)

    from .base import create_generation_job
    project_id = source["project_id"] or source["job_project_id"]
    job_id = create_generation_job(
        conn,
        "local_edit",
        model="local",
        prompt=title or "Local edited derivative",
        parent_job_id=source["job_id"],
        project_id=project_id,
        request={"source_output_id": source_output_id, "note": note},
    )
    update = conn.execute(
        "UPDATE generation_jobs SET status='succeeded', completed_at=? WHERE job_id=?",
        (now(), job_id),
    )
    conn.commit()
    output_id = create_output(
        conn,
        job_id,
        output_index=0,
        audio_path=str(Path(audio_path).expanduser().resolve()),
        project_id=project_id,
        stage=stage,
    )
    link_outputs(
        conn,
        output_id,
        source_output_id,
        "edited_from",
        note=note or f"source_output_id={source_output_id}",
    )
    return output_id

def add_analysis(conn, output_id, analysis_type, payload):
    conn.execute(
        """INSERT INTO generation_analysis
        (output_id, analysis_type, payload_json, created_at)
        VALUES (?,?,?,?)""",
        (output_id, analysis_type, json.dumps(payload, ensure_ascii=False), now()),
    )
    conn.commit()


def link_outputs(conn, from_output_id, to_output_id, relation_type,
                 confidence=None, note=None):
    conn.execute(
        """INSERT INTO generation_relations
        (from_output_id, to_output_id, relation_type, confidence, note, created_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(from_output_id,to_output_id,relation_type)
        DO UPDATE SET confidence=excluded.confidence, note=excluded.note""",
        (from_output_id, to_output_id, relation_type, confidence, note, now()),
    )
    conn.commit()


def download_audio_ref(ref, destination_dir, *, filename=None, timeout=120):
    """Download an HTTP(S) audio reference and return the local path."""
    if not ref.startswith(("http://", "https://")):
        local = Path(ref.replace("file://", "")).expanduser()
        return str(local.resolve()) if local.exists() else None

    import requests
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)

    if not filename:
        clean = ref.split("?", 1)[0].rstrip("/")
        filename = Path(clean).name or "generated_audio.bin"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    path = destination / safe

    with requests.get(ref, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        content_type = (
            r.headers.get("Content-Type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type in {"application/json", "text/html"} or content_type.startswith("image/"):
            raise ValueError(
                f"Refused non-audio response Content-Type: {content_type or 'unknown'}"
            )
        if path.suffix.lower() not in {
            ".mp3", ".wav", ".flac", ".m4a", ".aac",
            ".ogg", ".opus", ".webm",
        }:
            extension_map = {
                "audio/mpeg": ".mp3",
                "audio/wav": ".wav",
                "audio/x-wav": ".wav",
                "audio/flac": ".flac",
                "audio/mp4": ".m4a",
                "audio/aac": ".aac",
                "audio/ogg": ".ogg",
                "audio/opus": ".opus",
                "audio/webm": ".webm",
            }
            inferred = extension_map.get(content_type)
            if inferred:
                path = path.with_suffix(inferred)
        with path.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
    return str(path.resolve())


def find_local_audio_refs(response):
    return [x for x in extract_audio_refs(response)
            if Path(x.replace("file://", "")).exists()]
