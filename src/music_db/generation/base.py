from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def create_generation_job(conn: sqlite3.Connection, provider: str, *, model=None, prompt=None, lyrics=None, bpm=None, key_scale=None, duration_seconds=None, reference_track_id=None, parent_job_id=None, project_id=None, request=None):
    cur = conn.execute("""
      INSERT INTO generation_jobs
      (provider,model,status,prompt,lyrics,bpm,key_scale,duration_seconds,reference_track_id,parent_job_id,project_id,request_json,created_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (provider,model,"queued",prompt,lyrics,bpm,key_scale,duration_seconds,reference_track_id,parent_job_id,project_id,json.dumps(request or {},ensure_ascii=False),now()))
    conn.commit()
    return cur.lastrowid

def update_generation_job(conn, job_id, *, status=None, provider_task_id=None, response=None, error=None, started=False, completed=False):
    fields, values = [], []
    if status is not None:
        fields += ["status=?"]; values += [status]
    if provider_task_id is not None:
        fields += ["provider_task_id=?"]; values += [provider_task_id]
    if response is not None:
        fields += ["response_json=?"]; values += [json.dumps(response,ensure_ascii=False)]
    if error is not None:
        fields += ["error=?"]; values += [str(error)]
    if started:
        fields += ["started_at=?"]; values += [now()]
    if completed:
        fields += ["completed_at=?"]; values += [now()]
    if not fields:
        return
    values.append(job_id)
    conn.execute("UPDATE generation_jobs SET " + ", ".join(fields) + " WHERE job_id=?", values)
    conn.commit()
