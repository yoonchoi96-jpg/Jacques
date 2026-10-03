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
        "wait_seconds": 90,
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
}

def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "provider"

def extract_visible_text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)

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

    if provider == "mazmazika":
        # Mazmazika accepts a YouTube URL and exposes a timestamped chord timeline.
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
        await target.fill(YOUTUBE_URL)
        buttons = page.get_by_role("button")
        clicked = False
        for idx in range(buttons.count()):
            b = buttons.nth(idx)
            label = (b.inner_text()).strip().lower()
            if "analyze" in label and "chord" in label:
                await b.click()
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
        # ChordIdentifier keeps the YouTube input inside a modal.
        page.locator('button[title="YouTube video"]').click()
        input_box = page.locator("#ci-youtube-url")
        input_box.wait_for(state="visible", timeout=5_000)
        input_box.fill(YOUTUBE_URL)
        submit = page.locator('button:has-text("Fetch audio & generate chords")').first
        submit.wait_for(state="visible", timeout=5_000)
        submit.click()
    else:
        input_box = page.locator("#youtube-link")
        input_box.wait_for(state="visible", timeout=5_000)
        input_box.fill(YOUTUBE_URL)
        submit = input_box.locator("xpath=../following-sibling::button").first
        submit.wait_for(state="visible", timeout=5_000)
        submit.click()

    page.wait_for_timeout(cfg["wait_seconds"] * 1_000)
    text = extract_visible_text(page)
    result["final_url"] = page.url
    result["final_text_excerpt"] = text[:16_000]
    if provider == "chordidentifier":
        result["harmony_payload"] = build_harmony_payload(
            page.content(), source_url=page.url, youtube_url=YOUTUBE_URL
        )
    lower = text.lower()

    if provider == "chordidentifier":
        if any(x in lower for x in (
            "chord timeline", "chord summary", "export chords",
            "unlock full chords", "chords detected"
        )):
            result["status"] = "accepted_or_processing"
        elif any(x in lower for x in ("error", "failed", "invalid", "not found", "unable")):
            result["status"] = "provider_error_or_rejection"
        else:
            result["status"] = "submitted_unknown_result"
    else:
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
                if harmony_payload and harmony_payload.get("segments"):
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
            fuse_track_harmony(conn, TRACK_ID)
            conn.commit()
        payload["track_id"] = TRACK_ID
        payload["imported"] = imported

    (ARTIFACT_DIR / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
