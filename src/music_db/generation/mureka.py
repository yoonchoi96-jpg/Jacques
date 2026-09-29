from __future__ import annotations

import os
import requests
from .base import update_generation_job
from .http import post_json

ENDPOINT = "https://api.mureka.ai/v1/song/generate"

def submit(conn, job_id, *, lyrics, prompt=None, model="auto", n=1, gender=None, reference_id=None):
    token = os.getenv("MUREKA_API_KEY")
    if not token:
        raise RuntimeError("MUREKA_API_KEY is missing")
    payload = {"lyrics": lyrics, "model": model, "n": n}
    if prompt: payload["prompt"] = prompt
    if gender: payload["gender"] = gender
    if reference_id: payload["reference_id"] = reference_id
    update_generation_job(conn, job_id, status="running", started=True)
    r = post_json(ENDPOINT, json_body=payload, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    try:
        r.raise_for_status()
        data = r.json()
        update_generation_job(conn, job_id, status="submitted", response=data)
        return data
    except Exception as exc:
        update_generation_job(conn, job_id, status="failed", error=exc, completed=True)
        raise
