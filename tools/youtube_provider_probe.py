from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_db.harmony.chordidentifier import build_harmony_payload

from playwright.sync_api import sync_playwright

ARTIFACT_DIR = Path("probe_artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
YOUTUBE_URL = os.environ["YOUTUBE_URL"].strip()
PROVIDERS = [x.strip().lower() for x in os.environ.get("PROVIDERS", "").split(",") if x.strip()]

PROVIDER_CONFIG = {
    "chordidentifier": {
        "url": "https://chordidentifier.com/chord-finder-from-youtube/",
        "wait_seconds": 90,
    },
    "songscription": {
        "url": "https://www.songscription.ai/youtube-to-sheet-music",
        "wait_seconds": 45,
    },
}

def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "provider"

def extract_visible_text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)

def run_provider(page, provider: str) -> dict:
    cfg = PROVIDER_CONFIG[provider]
    result = {"provider": provider, "youtube_url": YOUTUBE_URL, "provider_url": cfg["url"], "status": "unknown"}

    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(3_000)
    result["initial_text_excerpt"] = extract_visible_text(page)[:4_000]

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
            try:
                result = run_provider(page, provider) if provider in PROVIDER_CONFIG else {
                    "provider": provider, "status": "unsupported_provider"
                }
            except Exception as exc:
                result = {
                    "provider": provider,
                    "youtube_url": YOUTUBE_URL,
                    "status": "exception",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            stem = safe_name(provider)
            try:
                page.screenshot(path=str(ARTIFACT_DIR / f"{stem}.png"), full_page=True)
                (ARTIFACT_DIR / f"{stem}.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass

        browser.close()

    payload = {"youtube_url": YOUTUBE_URL, "providers": PROVIDERS, "results": results}
    (ARTIFACT_DIR / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
