from __future__ import annotations

import json
import time

BASE_URL = "https://magic-chords.dev/api/v1"

def _segments(result):
    raw = result.get("chords") or result.get("segments") or result.get("timeline") or []
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("chords") or raw.get("segments") or []
    out = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        chord = x.get("chord") or x.get("symbol") or x.get("label")
        start = x.get("start_sec", x.get("start", x.get("startTime", 0)))
        end = x.get("end_sec", x.get("end", x.get("endTime")))
        if chord and end is not None and float(end) > float(start):
            out.append({"start_sec": float(start), "end_sec": float(end),
                        "chord": str(chord), "confidence": x.get("confidence"),
                        "section": x.get("section") or x.get("section_name"), "raw": x})
    return out

def analyze(page, youtube_url, polls=24, wait=5):
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    job = page.evaluate("""async ({url}) => {
        const r = await fetch("%s/analyze/url", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({url})});
        return await r.json();
    }""" % BASE_URL, {"url": youtube_url})
    job_id = job.get("job_id") or job.get("id")
    if not job_id:
        raise RuntimeError("Magic Chords returned no job id")
    for _ in range(polls):
        state = page.evaluate("""async (id) => {
            const r = await fetch("%s/jobs/" + id); return await r.json();
        }""" % BASE_URL, job_id)
        status = str(state.get("status") or state.get("state") or "").lower()
        if status in {"completed","complete","done","success"}:
            break
        if status in {"failed","error","cancelled","canceled"}:
            raise RuntimeError("Magic Chords job failed")
        time.sleep(wait)
    else:
        raise TimeoutError("Magic Chords job timed out")
    result = page.evaluate("""async (id) => {
        const r = await fetch("%s/jobs/" + id + "/result"); return await r.json();
    }""" % BASE_URL, job_id)
    return {"source":"magic_chords","source_url":BASE_URL+"/jobs/"+job_id+"/result",
            "youtube_url":youtube_url,"job_id":job_id,"status":"completed",
            "confidence":result.get("confidence"),"key":result.get("key"),
            "tempo":result.get("tempo") or result.get("bpm"),
            "segments":_segments(result),"method":"magic_chords_url_api"}
