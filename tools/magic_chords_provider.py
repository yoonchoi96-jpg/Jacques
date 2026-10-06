from __future__ import annotations

BASE_URL = "https://magic-chords.dev/api/v1"

class MagicChordsJobError(RuntimeError):
    def __init__(self, job_id: str, stage: str, message: str):
        super().__init__(f"Magic Chords {stage} failed for {job_id}: {message}")
        self.job_id = job_id
        self.stage = stage

class MagicChordsTimeout(TimeoutError):
    def __init__(self, job_id: str):
        super().__init__(f"Magic Chords job timed out: {job_id}")
        self.job_id = job_id

class MagicChordsResultError(MagicChordsJobError):
    def __init__(self, job_id: str, message: str):
        super().__init__(job_id, "result fetch", message)

class MagicChordsParseError(ValueError):
    pass

def _parse_segments(result):
    raw = None
    if isinstance(result, dict):
        for key in ("chords", "segments", "timeline"):
            if key in result:
                raw = result[key]
                break
        if raw is None:
            for key in ("data", "result", "analysis"):
                nested = result.get(key)
                if isinstance(nested, dict):
                    try:
                        return _parse_segments(nested)
                    except MagicChordsParseError:
                        continue
    if raw is None:
        raise MagicChordsParseError("Magic Chords response has no supported chord timeline field")
    if isinstance(raw, dict):
        raw = next((raw[key] for key in ("items", "chords", "segments") if key in raw), None)
    if not isinstance(raw, list):
        raise MagicChordsParseError("Magic Chords timeline is not a list")
    out, invalid = [], 0
    for x in raw:
        if not isinstance(x, dict):
            invalid += 1
            continue
        chord = x.get("chord") or x.get("symbol") or x.get("label")
        start = x.get("start_sec", x.get("start", x.get("startTime")))
        end = x.get("end_sec", x.get("end", x.get("endTime")))
        try:
            start, end = float(start), float(end)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if chord and end > start:
            out.append({"start_sec": start, "end_sec": end, "chord": str(chord),
                        "confidence": x.get("confidence"),
                        "section": x.get("section") or x.get("section_name"), "raw": x})
        else:
            invalid += 1
    return out, len(raw), invalid

def _api_json(page, method, path, payload=None):
    return page.evaluate("""async ({method, path, payload}) => {
        const options = {method, headers: {"Content-Type": "application/json"}};
        if (payload !== null) options.body = JSON.stringify(payload);
        const r = await fetch(path, options);
        let body = null;
        try { body = await r.json(); } catch (_) {}
        return {ok: r.ok, status: r.status, body};
    }""", {"method": method, "path": path, "payload": payload})

def _submit(page, youtube_url):
    response = _api_json(page, "POST", BASE_URL + "/analyze/url", {"url": youtube_url})
    if not response["ok"]:
        raise RuntimeError(f"Magic Chords submit HTTP {response['status']}: {response['body']}")
    body = response["body"] or {}
    job_id = body.get("job_id") or body.get("id")
    if not job_id:
        raise RuntimeError("Magic Chords returned no job id")
    return str(job_id)

def _poll(page, job_id, polls, wait):
    for _ in range(polls):
        response = _api_json(page, "GET", BASE_URL + "/jobs/" + job_id)
        if response["status"] == 404:
            raise MagicChordsJobError(job_id, "status poll", "unknown job_id (HTTP 404)")
        if not response["ok"]:
            raise MagicChordsJobError(job_id, "status poll",
                                      f"HTTP {response['status']}: {response['body']}")
        state = response["body"] or {}
        status = str(state.get("status") or state.get("state") or "").lower()
        if status in {"completed", "complete", "done", "success"}:
            return
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise MagicChordsJobError(job_id, "job", f"provider status={status}; response={state}")
        page.wait_for_timeout(wait * 1000)
    raise MagicChordsTimeout(job_id)

def analyze(page, youtube_url, polls=24, wait=5, job_id=None):
    page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30000)
    # A 404 means the capability-token job is gone; never poll it again.
    if job_id:
        try:
            _poll(page, job_id, polls, wait)
        except MagicChordsJobError as exc:
            if "HTTP 404" not in str(exc):
                raise
            job_id = None
    if not job_id:
        job_id = _submit(page, youtube_url)
        _poll(page, job_id, polls, wait)

    response = _api_json(page, "GET", BASE_URL + "/jobs/" + job_id + "/result")
    if response["status"] == 404:
        raise MagicChordsResultError(job_id, "completed job disappeared (HTTP 404)")
    if not response["ok"]:
        raise MagicChordsResultError(job_id, f"HTTP {response['status']}: {response['body']}")
    result = response["body"] or {}
    segments, raw_count, invalid_count = _parse_segments(result)
    return {
        "source": "magic_chords",
        "source_url": BASE_URL + "/jobs/" + job_id + "/result",
        "youtube_url": youtube_url,
        "job_id": job_id,
        "status": "completed",
        "raw_result_available": True,
        "confidence": result.get("confidence"),
        "key": result.get("key"),
        "tempo": result.get("tempo") or result.get("bpm"),
        "segments": segments,
        "raw_segment_count": raw_count,
        "invalid_segment_count": invalid_count,
        "parser_version": "magic-chords-api-v2",
        "method": "magic_chords_url_api",
    }
