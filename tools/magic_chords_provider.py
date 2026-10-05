from __future__ import annotations

BASE_URL = "https://magic-chords.dev/api/v1"


class MagicChordsJobError(RuntimeError):
    """A submitted job failed during status/result retrieval."""

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

def _parse_segments(result):
    raw = result.get("chords") or result.get("segments") or result.get("timeline") or []
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("chords") or raw.get("segments") or []
    if not isinstance(raw, list):
        raise ValueError("Magic Chords timeline is not a list")
    out = []
    invalid = 0
    for x in raw:
        if not isinstance(x, dict):
            invalid += 1
            continue
        chord = x.get("chord") or x.get("symbol") or x.get("label")
        start = x.get("start_sec", x.get("start", x.get("startTime")))
        end = x.get("end_sec", x.get("end", x.get("endTime")))
        try:
            start = float(start)
            end = float(end)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if chord and end > start:
            out.append({
                "start_sec": start, "end_sec": end,
                "chord": str(chord), "confidence": x.get("confidence"),
                "section": x.get("section") or x.get("section_name"), "raw": x,
            })
        else:
            invalid += 1
    return out, len(raw), invalid


def _segments(result):
    return _parse_segments(result)[0]


def _poll(page, job_id, polls, wait):
    for _ in range(polls):
        try:
            state = page.evaluate("""async (id) => {
                const r = await fetch("%s/jobs/" + id);
                if (!r.ok) throw new Error("status " + r.status);
                return await r.json();
            }""" % BASE_URL, job_id)
        except Exception as exc:
            raise MagicChordsJobError(job_id, "status poll", str(exc)) from exc
        status = str(state.get("status") or state.get("state") or "").lower()
        if status in {"completed", "complete", "done", "success"}:
            return
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise RuntimeError("Magic Chords job failed: " + status)
        page.wait_for_timeout(wait * 1000)
    raise MagicChordsTimeout(job_id)

def analyze(page, youtube_url, polls=24, wait=5, job_id=None):
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    if not job_id:
        job = page.evaluate("""async ({url}) => {
            const r = await fetch("%s/analyze/url", {
                method:"POST",
                headers:{"Content-Type":"application/json"},
                body:JSON.stringify({url})
            });
            if (!r.ok) throw new Error("submit " + r.status);
            return await r.json();
        }""" % BASE_URL, {"url": youtube_url})
        job_id = job.get("job_id") or job.get("id")
        if not job_id:
            raise RuntimeError("Magic Chords returned no job id")

    _poll(page, job_id, polls, wait)
    try:
        result = page.evaluate("""async (id) => {
            const r = await fetch("%s/jobs/" + id + "/result");
            if (!r.ok) throw new Error("result " + r.status);
            return await r.json();
        }""" % BASE_URL, job_id)
    except Exception as exc:
        raise MagicChordsResultError(job_id, str(exc)) from exc

    segments, raw_segment_count, invalid_segment_count = _parse_segments(result)
    return {
        "source": "magic_chords",
        "source_url": BASE_URL + "/jobs/" + job_id + "/result",
        "youtube_url": youtube_url,
        "job_id": job_id,
        "status": "completed",
        "confidence": result.get("confidence"),
        "key": result.get("key"),
        "tempo": result.get("tempo") or result.get("bpm"),
        "segments": segments,
        "raw_segment_count": raw_segment_count,
        "invalid_segment_count": invalid_segment_count,
        "parser_version": "magic-chords-api-v1",
        "method": "magic_chords_url_api",
    }
