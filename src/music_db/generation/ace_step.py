from __future__ import annotations

import os
import time
import requests
from .base import update_generation_job
from .http import post_json

def _base():
    return os.getenv("ACESTEP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

def _headers():
    token = os.getenv("ACESTEP_API_KEY")
    return {"Authorization": f"Bearer {token}"} if token else {}

def submit(conn, job_id, *, prompt=None, lyrics=None, model="acestep-v15-turbo", thinking=False, audio_format="mp3", **extra):
    payload = {"prompt": prompt or "", "lyrics": lyrics or "", "model": model, "thinking": thinking, "audio_format": audio_format}
    payload.update(extra)
    update_generation_job(conn, job_id, status="running", started=True)
    r = post_json(f"{_base()}/release_task", json_body=payload, headers=_headers(), timeout=60)
    try:
        r.raise_for_status()
        data = r.json()
        body = data.get("data") or data
        task_id = body.get("task_id")
        update_generation_job(conn, job_id, status="submitted", provider_task_id=task_id, response=data)
        return data
    except Exception as exc:
        update_generation_job(conn, job_id, status="failed", error=exc, completed=True)
        raise

def poll(task_id, timeout_seconds=1800, interval_seconds=5):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        r = post_json(f"{_base()}/query_result", json_body={"task_id": task_id}, headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
        body = data.get("data") or data
        status = body.get("status")
        if status in (1, "1"):
            return body
        if status in (2, "2"):
            raise RuntimeError(str(body.get("error") or data))
        time.sleep(interval_seconds)
    raise TimeoutError(f"ACE-Step task timed out: {task_id}")
