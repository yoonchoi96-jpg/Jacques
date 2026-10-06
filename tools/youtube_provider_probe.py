from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_db.harmony.chordidentifier import build_harmony_payload
from music_db.database import initialize_database, get_connection
from music_db.harmony.pipeline import import_external_harmony, fuse_track_harmony
from magic_chords_provider import MagicChordsJobError, MagicChordsTimeout, analyze as analyze_magic_chords

from playwright.sync_api import sync_playwright

ARTIFACT_DIR = Path("probe_artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_URL = os.environ["YOUTUBE_URL"].strip()
TRACK_ID = os.environ.get("TRACK_ID", "").strip()
PROVIDERS = [x.strip().lower() for x in os.environ.get("PROVIDERS", "").split(",") if x.strip()]
PROVIDER_RETRIES = max(1, int(os.environ.get("PROVIDER_RETRIES", "2")))

PROVIDER_CONFIG = {
    "chordidentifier": {
        "url": "https://chordidentifier.com/chord-finder-from-youtube/",
        "wait_seconds": 180,
    },
    "songscription": {
        "url": "https://www.songscription.ai/youtube-to-sheet-music",
        "wait_seconds": 45,
    },
    "magic_chords": {
        "url": "https://magic-chords.dev/api/v1",
        "wait_seconds": 5,
    },
    "mazmazika": {
        "url": "https://www.mazmazika.com/chordanalyzer",
        "wait_seconds": 60,
    },
    "methodic_truth": {
        "url": "https://methodictruth.com/song-analyzer",
        "wait_seconds": 35,
    },
}

def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "provider"

def extract_visible_text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)


def _parse_timestamp(value: str) -> float | None:
    value = value.strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?", value):
        return None
    parts = value.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None


def extract_timed_chords(text: str) -> list[dict]:
    """Parse provider-rendered chord/timestamp pairs conservatively."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chord_re = re.compile(
        r"^[A-G](?:#|b)?(?:maj13|maj11|maj9|maj7|maj|min13|min11|min9|min7|min|m13|m11|m9|m7|m|dim7|dim|aug|sus2|sus4|sus|add9|add11|7|6|9|11|13)?(?:/[A-G](?:#|b)?)?$"
    )
    rows = []
    pending = None
    for line in lines:
        ts = _parse_timestamp(line)
        if ts is not None and pending:
            if not rows or ts > rows[-1]["start_sec"]:
                rows.append({"start_sec": ts, "chord": pending})
            pending = None
            continue
        if chord_re.fullmatch(line):
            pending = line
    for idx, row in enumerate(rows):
        if idx + 1 < len(rows):
            row["end_sec"] = rows[idx + 1]["start_sec"]
        else:
            row["end_sec"] = None
    return [row for row in rows if row.get("end_sec") is not None and row["end_sec"] > row["start_sec"]]



def extract_methodic_sections(text: str) -> list[dict]:
    """Extract explicit section labels only when the provider visibly exposes them."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    section_re = re.compile(r"^(intro|verse(?:\s*\d+)?|pre[- ]?chorus|chorus(?:\s*\d+)?|post[- ]?chorus|bridge(?:\s*\d+)?|breakdown|interlude|outro|hook(?:\s*\d+)?|refrain)(?:\s*[:\-].*)?$", re.IGNORECASE)
    timestamp_re = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?)$")
    markers = []
    for idx, line in enumerate(lines):
        match = section_re.fullmatch(line)
        if not match:
            continue
        start_sec = None
        for candidate in lines[idx + 1:idx + 8]:
            if timestamp_re.fullmatch(candidate):
                parts = [int(x) for x in candidate.split(":")]
                start_sec = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
                break
        if start_sec is not None:
            label = match.group(1).title().replace("Pre Chorus", "Pre-Chorus").replace("Post Chorus", "Post-Chorus")
            markers.append({"section": label, "start_sec": float(start_sec), "source": "methodic_truth", "explicit": True, "confidence": 0.9})
    for idx, marker in enumerate(markers):
        marker["end_sec"] = markers[idx + 1]["start_sec"] if idx + 1 < len(markers) else None
    return [item for item in markers if item.get("end_sec") is not None]

def extract_methodic_metadata(text: str) -> dict:
    """Extract song-level BPM/key/meter from Methodic Truth's result header."""
    head = text[:2_500]
    bpm_match = re.search(r"\bBPM\s+(\d+(?:\.\d+)?)\b", head)
    key_match = re.search(r"\bKEY\s+([A-G](?:#|b)?\s+(?:Major|Minor))\b", head)
    meter_match = re.search(r"\b(\d+)/(\d+)\s+feel\b", head)
    return {
        "tempo": float(bpm_match.group(1)) if bpm_match else None,
        "key": key_match.group(1) if key_match else None,
        "time_signature": f"{meter_match.group(1)}/{meter_match.group(2)}" if meter_match else None,
    }


def run_provider(page, provider: str) -> dict:
    if provider == "magic_chords":
        payload = analyze_magic_chords(page, YOUTUBE_URL)
        return {"provider": "magic_chords", "youtube_url": YOUTUBE_URL,
                "provider_url": PROVIDER_CONFIG["magic_chords"]["url"],
                "status": "accepted_or_processing", "harmony_payload": payload}
    cfg = PROVIDER_CONFIG[provider]
    result = {"provider": provider, "youtube_url": YOUTUBE_URL, "provider_url": cfg["url"], "status": "unknown"}

    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(3_000)
    result["initial_text_excerpt"] = extract_visible_text(page)[:4_000]

    if provider == "methodic_truth":
        # Methodic Truth accepts public YouTube URLs and renders chord/timestamp pairs.
        # Re-open the result URL first so cached analyses do not submit repeatedly.
        import re
        video_match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", YOUTUBE_URL)
        result_url = (
            f"https://methodictruth.com/song-analyzer?v={video_match.group(1)}"
            if video_match
            else "https://methodictruth.com/song-analyzer"
        )
        page.goto(result_url)
        page.wait_for_timeout(4_000)
        text = extract_visible_text(page)

        if "② CHORD CHART" not in text:
            inputs = page.locator("input")
            target = None
            for idx in range(inputs.count()):
                item = inputs.nth(idx)
                placeholder = (item.get_attribute("placeholder") or "").lower()
                input_type = (item.get_attribute("type") or "").lower()
                if "youtube" in placeholder or input_type == "url":
                    target = item
                    break
            if target is None:
                raise RuntimeError("Methodic Truth YouTube URL input not found")
            target.fill(YOUTUBE_URL)

            buttons = page.get_by_role("button")
            clicked = False
            for idx in range(buttons.count()):
                b = buttons.nth(idx)
                label = (b.inner_text()).strip().lower()
                if "analyze" in label:
                    b.click()
                    clicked = True
                    break
            if not clicked:
                raise RuntimeError("Methodic Truth analyze button not found")
            page.wait_for_timeout(cfg["wait_seconds"] * 1_000)
            text = extract_visible_text(page)

        result["final_url"] = page.url
        result["final_text_excerpt"] = text[:16_000]
        metadata = extract_methodic_metadata(text)
        explicit_sections = extract_methodic_sections(text)

        # The UI renders chord and timestamp on adjacent lines in Play Along:
        #   Esus4
        #   0:00
        #   F#sus4
        #   0:06
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        try:
            play_start = next(i for i, line in enumerate(lines) if "③ PLAY ALONG" in line)
            play_end = next(i for i in range(play_start + 1, len(lines)) if "④ LYRICS" in lines[i])
            play_lines = lines[play_start + 1:play_end]
        except StopIteration:
            play_lines = []

        timestamp_re = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
        chord_re = re.compile(
            r"^[A-G](?:#|b)?(?:maj7|maj|min7|min|m7|m|dim7|dim|aug|sus2|sus4|sus|7|6|9|11|13|add9)?$"
        )
        chord_rows = []
        pending_chord = None
        for line in play_lines:
            if chord_re.fullmatch(line):
                pending_chord = line
                continue
            if pending_chord and timestamp_re.fullmatch(line):
                parts = [int(x) for x in line.split(":")]
                start_sec = (
                    parts[0] * 60 + parts[1]
                    if len(parts) == 2
                    else parts[0] * 3600 + parts[1] * 60 + parts[2]
                )
                chord_rows.append({"start_sec": float(start_sec), "chord": pending_chord})
                pending_chord = None

        if chord_rows:
            for idx, row in enumerate(chord_rows):
                row["end_sec"] = (
                    chord_rows[idx + 1]["start_sec"]
                    if idx + 1 < len(chord_rows)
                    else None
                )
            result["harmony"] = chord_rows
                # Parse Methodic Truth's numbered CHORD CHART so bar-level structure
            # survives independently from the finer PLAY ALONG timestamps.
            chart_bars = []
            try:
                chart_start = next(i for i, line in enumerate(lines) if "② CHORD CHART" in line)
                chart_end = next(i for i in range(chart_start + 1, len(lines)) if "③ PLAY ALONG" in lines[i])
                chart_lines = lines[chart_start + 1:chart_end]
                idx = 0
                while idx < len(chart_lines):
                    if re.fullmatch(r"\d+", chart_lines[idx]):
                        bar = int(chart_lines[idx])
                        idx += 1
                        tokens = []
                        while idx < len(chart_lines) and not re.fullmatch(r"\d+", chart_lines[idx]):
                            if re.fullmatch(r"[A-G](?:#|b)?(?:maj7|maj|min7|m7|min|m|dim7|dim|aug|sus2|sus4|sus|7|6|9|11|13|add9)?", chart_lines[idx]):
                                tokens.extend(chart_lines[idx].split())
                            idx += 1
                        if tokens:
                            chart_bars.append({"bar": bar, "chords": tokens})
                    else:
                        idx += 1
            except StopIteration:
                chart_bars = []
    
            result["harmony_payload"] = {
                "source": "methodic_truth",
                "source_url": "https://methodictruth.com/song-analyzer",
                "youtube_url": YOUTUBE_URL,
                "confidence": None,
                "tempo": metadata.get("tempo"),
                "key": metadata.get("key"),
                "time_signature": metadata.get("time_signature"),
                "segments": chord_rows,
                "chart_bars": chart_bars,
                "segment_count": len(chord_rows),
                "chart_bar_count": len(chart_bars),
                "sections": explicit_sections,
                "method": "youtube_browser_analysis",
            }
            result["status"] = "success"
        else:
            result["status"] = "accepted_or_processing"
        return result

    if provider == "mazmazika":
        # Mazmazika accepts a YouTube URL and performs server-side audio decoding.
        # Jacques never downloads or stores the released audio.
        inputs = page.locator("input")
        target = None
        for idx in range(inputs.count()):
            item = inputs.nth(idx)
            placeholder = (item.get_attribute("placeholder") or "").lower()
            input_type = (item.get_attribute("type") or "").lower()
            if "youtube" in placeholder or "soundcloud" in placeholder or input_type == "url":
                target = item
                break
        if target is None:
            raise RuntimeError("Mazmazika media URL input not found")
        target.fill(YOUTUBE_URL)

        buttons = page.get_by_role("button")
        clicked = False
        for idx in range(buttons.count()):
            b = buttons.nth(idx)
            label = (b.inner_text()).strip().lower()
            if "analy" in label:
                b.click()
                clicked = True
                break
        if not clicked:
            raise RuntimeError("Mazmazika analyze button not found")

        deadline_ms = cfg["wait_seconds"] * 1_000
        poll_ms = 5_000
        elapsed_ms = 0
        text = ""
        chord_rows = []
        while elapsed_ms < deadline_ms:
            page.wait_for_timeout(poll_ms)
            elapsed_ms += poll_ms
            text = extract_visible_text(page)
            chord_rows = extract_timed_chords(text)
            lower = text.lower()
            if chord_rows:
                break
            if any(x in lower for x in ("error", "failed", "invalid", "not found")):
                break

        result["final_url"] = page.url
        result["final_text_excerpt"] = text[:16_000]
        result["poll_elapsed_seconds"] = elapsed_ms / 1_000
        if chord_rows:
            result["harmony_payload"] = {
                "source": "mazmazika",
                "source_url": page.url,
                "youtube_url": YOUTUBE_URL,
                "confidence": None,
                "segments": chord_rows,
                "segment_count": len(chord_rows),
                "method": "youtube_browser_analysis",
            }
            result["status"] = "success"
        elif any(x in text.lower() for x in ("error", "failed", "invalid", "not found")):
            result["status"] = "provider_error_or_rejection"
        else:
            result["status"] = "accepted_or_processing"
        return result

    if provider == "mazmazika_legacy":
        # Retained only as a compatibility branch; use the implementation above.
        inputs = page.locator('input')
        target = None
        for idx in range(inputs.count()):
            item = inputs.nth(idx)
            placeholder = (item.get_attribute("placeholder") or "").lower()
            input_type = (item.get_attribute("type") or "").lower()
            if "youtube" in placeholder or "soundcloud" in placeholder or input_type == "url":
                target = item
                break
        if target is None:
            raise RuntimeError("Mazmazika YouTube URL input not found")
        target.fill(YOUTUBE_URL)
        buttons = page.get_by_role("button")
        clicked = False
        for idx in range(buttons.count()):
            b = buttons.nth(idx)
            label = (b.inner_text()).strip().lower()
            if "analyze" in label and "chord" in label:
                b.click()
                clicked = True
                break
        if not clicked:
            raise RuntimeError("Mazmazika analyze button not found")
        page.wait_for_timeout(cfg["wait_seconds"] * 1_000)
        text = extract_visible_text(page)
        result["final_url"] = page.url
        result["final_text_excerpt"] = text[:16_000]
        result["status"] = "accepted_or_processing" if any(
            x in text.lower() for x in ("chord progression", "ai chord detection", "analyzing", "analysis")
        ) else "submitted_unknown_result"
        return result

    if provider == "chordidentifier":
        # ChordIdentifier progressively renders its timeline. A non-zero segment
        # count is only an intermediate state, so keep polling until the timeline
        # stops changing for several consecutive polls (or the provider reports an
        # explicit failure). This prevents short partial timelines from becoming
        # the canonical provider snapshot.
        deadline_ms = cfg["wait_seconds"] * 1_000
        poll_ms = 5_000
        stable_polls_required = 3
        elapsed_ms = 0
        stable_polls = 0
        previous_signature = None
        payload = {"source": "chordidentifier", "segments": [], "segment_count": 0}
        text = ""

        while elapsed_ms < deadline_ms:
            page.wait_for_timeout(poll_ms)
            elapsed_ms += poll_ms
            text = extract_visible_text(page)
            payload = build_harmony_payload(
                page.content(), source_url=page.url, youtube_url=YOUTUBE_URL
            )
            segments = payload.get("segments") or []
            signature = tuple(
                (
                    segment.get("start_sec"),
                    segment.get("end_sec"),
                    segment.get("chord"),
                )
                for segment in segments
            )

            if signature and signature == previous_signature:
                stable_polls += 1
            elif signature:
                stable_polls = 0
            else:
                stable_polls = 0
            previous_signature = signature

            lower_poll = text.lower()
            if any(x in lower_poll for x in ("error", "failed", "invalid", "not found", "unable")):
                break

            # Require the first non-empty timeline to remain unchanged across
            # multiple polls. This is intentionally stricter than a non-zero
            # segment count, because the provider can append or replace regions
            # while generation is still in progress.
            if signature and stable_polls >= stable_polls_required:
                break

        result["final_url"] = page.url
        result["final_text_excerpt"] = text[:16_000]
        result["harmony_payload"] = payload
        result["poll_elapsed_seconds"] = elapsed_ms / 1_000
        result["stable_polls"] = stable_polls
        result["timeline_segment_count"] = payload.get("segment_count", 0)

        lower = text.lower()
        if payload.get("segment_count", 0) > 0 and stable_polls >= stable_polls_required:
            result["status"] = "success"
        elif any(x in lower for x in ("error", "failed", "invalid", "not found", "unable")):
            result["status"] = "provider_error_or_rejection"
        else:
            result["status"] = "accepted_or_processing"
    else:
        page.wait_for_timeout(cfg["wait_seconds"] * 1_000)
        text = extract_visible_text(page)
        result["final_url"] = page.url
        result["final_text_excerpt"] = text[:16_000]
        lower = text.lower()
        if page.url != cfg["url"] or any(x in lower for x in (
            "your transcriptions", "piano roll", "transcription", "processing"
        )):
            result["status"] = "accepted_or_processing"
        elif any(x in lower for x in ("error", "failed", "invalid", "not found")):
            result["status"] = "provider_error_or_rejection"
        else:
            result["status"] = "submitted_unknown_result"
    return result

def main() -> None:
    if not re.match(r"^https?://", YOUTUBE_URL):
        raise SystemExit("YOUTUBE_URL must be an http(s) URL")
    if not PROVIDERS:
        raise SystemExit("No providers selected")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        )
        page = context.new_page()

        for provider in PROVIDERS:
            result = None
            magic_job_id = None
            for attempt in range(1, PROVIDER_RETRIES + 1):
                try:
                    if provider == "magic_chords" and magic_job_id:
                        payload = analyze_magic_chords(page, YOUTUBE_URL, job_id=magic_job_id)
                        result = {
                            "provider": "magic_chords",
                            "youtube_url": YOUTUBE_URL,
                            "provider_url": PROVIDER_CONFIG["magic_chords"]["url"],
                            "status": "accepted_or_processing",
                            "harmony_payload": payload,
                        }
                    else:
                        result = run_provider(page, provider) if provider in PROVIDER_CONFIG else {
                        "provider": provider, "status": "unsupported_provider"
                    }
                    result["attempt"] = attempt
                    break
                except (MagicChordsTimeout, MagicChordsJobError) as exc:
                    magic_job_id = exc.job_id
                    result = {
                        "provider": provider,
                        "youtube_url": YOUTUBE_URL,
                        "status": "poll_timeout" if isinstance(exc, MagicChordsTimeout) else "result_fetch_error",
                        "attempt": attempt,
                        "error": str(exc),
                        "job_id": magic_job_id,
                    }
                    if attempt < PROVIDER_RETRIES:
                        page.wait_for_timeout(min(10_000, attempt * 2_000))
                except Exception as exc:
                    error_text = f"{type(exc).__name__}: {exc}"
                    result = {
                        "provider": provider,
                        "youtube_url": YOUTUBE_URL,
                        "status": "rate_limited" if "429" in error_text else "exception",
                        "attempt": attempt,
                        "error": error_text,
                    }
                    # A provider-side 429 must never cause a second heavy submission.
                    # Preserve the evidence and move on to the next provider.
                    if "429" in error_text:
                        break
                    if attempt < PROVIDER_RETRIES:
                        page.wait_for_timeout(min(10_000, attempt * 2_000))
            results.append(result)
            stem = safe_name(provider)
            try:
                page.screenshot(path=str(ARTIFACT_DIR / f"{stem}.png"), full_page=True)
                (ARTIFACT_DIR / f"{stem}.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass

        browser.close()

    payload = {"youtube_url": YOUTUBE_URL, "providers": PROVIDERS, "results": results}

    if TRACK_ID:
        initialize_database()
        imported = []
        with get_connection() as conn:
            for result in results:
                conn.execute(
                    "INSERT INTO music_analysis_evidence "
                    "(track_id, domain, source, source_type, method, payload_json, confidence, observed_at, created_at) "
                    "VALUES (?, 'media_analysis_run', ?, 'web', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (
                        TRACK_ID,
                        result.get("provider", "unknown"),
                        "youtube_provider_probe",
                        json.dumps({
                            "youtube_url": YOUTUBE_URL,
                            "status": result.get("status"),
                            "attempt": result.get("attempt"),
                            "error": result.get("error"),
                            "provider_url": result.get("provider_url"),
                        }, ensure_ascii=False),
                        1.0 if result.get("status") == "accepted_or_processing" else 0.0,
                    ),
                )
                harmony_payload = result.get("harmony_payload")
                # Treat every provider payload as the current snapshot, including
                # an empty/processing payload. import_external_harmony() clears the
                # previous provider observation before importing, so empty results
                # must also reach it to prevent stale evidence from surviving runs.
                if harmony_payload is not None:
                    count = import_external_harmony(
                        conn, TRACK_ID, harmony_payload,
                        source=harmony_payload.get("source", result["provider"]),
                    )
                    imported.append({
                        "provider": result["provider"],
                        "harmony_segments": count,
                    })
                    conn.execute(
                        "INSERT INTO music_analysis_evidence "
                        "(track_id, domain, source, source_type, method, payload_json, confidence, observed_at, created_at) "
                        "VALUES (?, 'media_analysis', ?, 'web', ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (
                            TRACK_ID,
                            result["provider"],
                            harmony_payload.get("method", "youtube_browser_analysis"),
                            json.dumps(harmony_payload, ensure_ascii=False),
                            harmony_payload.get("confidence"),
                        ),
                    )
            profile = fuse_track_harmony(conn, TRACK_ID)
            if profile:
                payload["harmony_profile"] = profile
                print("HARMONY CONSENSUS")
                print(json.dumps({
                    "key": profile.get("key"),
                    "tempo": profile.get("tempo"),
                    "time_signature": profile.get("time_signature"),
                    "confidence": profile.get("confidence"),
                    "consensus_policy": profile.get("consensus_policy"),
                    "progression": profile.get("progression"),
                    "roman_progression": profile.get("roman_progression"),
                    "beat_grid": profile.get("beat_grid"),
                    "structure_map": profile.get("structure_map"),
                    "prompt_harmony": profile.get("prompt_harmony"),
                }, ensure_ascii=False, indent=2))
            conn.commit()
        payload["track_id"] = TRACK_ID
        payload["imported"] = imported

    artifact_stem = safe_name(TRACK_ID or "unknown_track")
    (ARTIFACT_DIR / f"result_{artifact_stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
