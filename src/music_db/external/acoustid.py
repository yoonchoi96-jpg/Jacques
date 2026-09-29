from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import requests

SOURCE = "acoustid"
ENDPOINT = "https://api.acoustid.org/v2/lookup"


def fingerprint_file(path):
    path = str(Path(path).expanduser().resolve())
    proc = subprocess.run(
        ["fpcalc", "-json", path],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(proc.stdout)
    return int(data["duration"]), data["fingerprint"]


def lookup_file(path, *, meta="recordings,recordingids,releases,releaseids,tracks,isrcs"):
    client = os.getenv("ACOUSTID_CLIENT")
    if not client:
        raise RuntimeError("ACOUSTID_CLIENT is missing")
    duration, fingerprint = fingerprint_file(path)
    r = requests.post(
        ENDPOINT,
        data={
            "client": client,
            "duration": duration,
            "fingerprint": fingerprint,
            "meta": meta,
            "format": "json",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def identify_track(conn, track_id, path):
    data = lookup_file(path)
    conn.execute(
        """INSERT INTO source_records
        (source, entity_type, external_id, track_id, url, data_json, observed_at)
        VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(source,entity_type,external_id)
        DO UPDATE SET track_id=excluded.track_id, data_json=excluded.data_json,
                      observed_at=excluded.observed_at""",
        (SOURCE, "audio_fingerprint", str(Path(path).resolve()),
         track_id, ENDPOINT, json.dumps(data, ensure_ascii=False)),
    )
    conn.execute(
        """INSERT INTO enrichment_status
        (track_id, source, entity_type, status, attempts, completed_at, created_at, updated_at)
        VALUES (?,?,?,'success',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        ON CONFLICT(track_id,source,entity_type)
        DO UPDATE SET status='success', attempts=enrichment_status.attempts+1,
                      completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP""",
        (track_id, SOURCE, "audio_fingerprint"),
    )
    conn.commit()
    return data
