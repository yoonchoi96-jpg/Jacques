from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ARTIFACT_DIR = Path("probe_artifacts")
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

YOUTUBE_URL = os.environ["YOUTUBE_URL"].strip()
PROVIDERS = [x.strip().lower() for x in os.environ.get("PROVIDERS", "").split(",") if x.strip()]

PROVIDER_CONFIG = {
    "chordidentifier": {
        "url": "https://chordidentifier.com/chord-finder-from-youtube/",
        "input_selectors": [
            'input[placeholder*="youtube" i]',
            'input[type="url"]',
        ],
        "button_selectors": [
            'button:has-text("Fetch audio & generate chords")',
            'button:has-text("generate chords")',
        ],
        "wait_seconds": 90,
    },
    "songscription": {
        "url": "https://www.songscription.ai/youtube-to-sheet-music",
        "input_selectors": [
            'input[placeholder*="YouTube" i]',
            'input[type="url"]',
        ],
        "button_selectors": [],
        "wait_seconds": 30,
    },
}


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "provider"


def extract_visible_text(page) -> str:
    return page.locator("body").inner_text(timeout=15_000)


def run_provider(page, provider: str) -> dict:
    cfg = PROVIDER_CONFIG[provider]
    result = {
        "provider": provider,
        "youtube_url": YOUTUBE_URL,
        "provider_url": cfg["url"],
        "status": "unknown",
    }

    try:
        page.goto(cfg["url"], wait_until="commit", timeout=30_000)
        page.wait_for_timeout(3_000)
    except Exception as exc:
        result["navigation_error"] = f"{type(exc).__name__}: {exc}"
        try:
            body = extract_visible_text(page)
            result["initial_text_excerpt"] = body[:4_000]
        except Exception:
            body = ""
        if not body:
            result["status"] = "navigation_failed"
            return result

    body = extract_visible_text(page)
    result["initial_text_excerpt"] = body[:4_000]

    if provider == "songscription" and "temporarily unavailable" in body.lower():
        result["status"] = "service_temporarily_unavailable"
        return result

    input_box = None
    for selector in cfg["input_selectors"]:
        try:
            candidate = page.locator(selector).first
            if candidate.is_visible(timeout=2_000):
                input_box = candidate
                break
        except Exception:
            pass

    if input_box is None:
        result["status"] = "input_not_found"
        return result

    input_box.fill(YOUTUBE_URL)

    if cfg["button_selectors"]:
        clicked = False
        for selector in cfg["button_selectors"]:
            try:
                button = page.locator(selector).first
                if button.is_visible(timeout=2_000):
                    button.click()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            result["status"] = "submit_button_not_found"
            return result
    else:
        input_box.press("Enter")

    page.wait_for_timeout(cfg["wait_seconds"] * 1_000)

    text = extract_visible_text(page)
    result["final_url"] = page.url
    result["final_text_excerpt"] = text[:12_000]

    lower = text.lower()
    if any(x in lower for x in ("generating chords", "processing", "transcrib", "download", "export")):
        result["status"] = "submitted_or_processing"
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
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        )
        page = context.new_page()

        for provider in PROVIDERS:
            if provider not in PROVIDER_CONFIG:
                results.append({
                    "provider": provider,
                    "status": "unsupported_provider",
                })
                continue

            try:
                result = run_provider(page, provider)
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
                page.screenshot(
                    path=str(ARTIFACT_DIR / f"{stem}.png"),
                    full_page=True,
                )
            except Exception:
                pass

            try:
                (ARTIFACT_DIR / f"{stem}.html").write_text(
                    page.content(),
                    encoding="utf-8",
                )
            except Exception:
                pass

        browser.close()

    payload = {
        "youtube_url": YOUTUBE_URL,
        "providers": PROVIDERS,
        "results": results,
    }

    (ARTIFACT_DIR / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
