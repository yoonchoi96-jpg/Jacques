#!/usr/bin/env python3
"""NAVER Dictionary wordbook sync.

Runs on the user's own Mac through a GitHub self-hosted runner.
The NAVER password is never stored. A persistent Playwright profile is kept
outside the repository.

Modes:
  --bootstrap   Open Chromium visibly so the user can log in once.
  --sync       Reuse the saved session and export the current wordbooks.
  --probe      Capture DOM/network diagnostics without writing the canonical export.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

URL = "https://learn.dict.naver.com/wordbook/enkodict/#/my/main"
ROOT = Path(os.environ.get("NAVER_WORDBOOK_DATA", str(Path.home() / ".naver_wordbook")))
PROFILE = ROOT / "browser_profile"
OUT = ROOT / "exports"
DB = ROOT / "naver_wordbook.sqlite3"
REPO_EXPORT = Path(os.environ.get("NAVER_WORDBOOK_EXPORT", "data/naver_wordbook.json"))

ROOT.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY,
            word TEXT NOT NULL,
            meaning TEXT,
            pronunciation TEXT,
            part_of_speech TEXT,
            example TEXT,
            wordbook TEXT,
            source_url TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            raw_json TEXT,
            UNIQUE(word, meaning, wordbook)
        )
        """
    )
    con.commit()
    return con


def capture_network(page, events: list[dict]) -> None:
    def on_response(response):
        url = response.url
        if not any(x in url.lower() for x in ("wordbook", "dict.naver", "learn.dict")):
            return
        content_type = (response.headers.get("content-type") or "").lower()
        if "json" not in content_type and not any(
            x in url.lower() for x in ("api", "ajax", "graphql")
        ):
            return
        item = {
            "timestamp": now_iso(),
            "status": response.status,
            "method": response.request.method,
            "url": url,
            "content_type": content_type,
        }
        try:
            body = response.text()
            if len(body) <= 2_000_000:
                item["body"] = body
        except Exception:
            pass
        events.append(item)

    page.on("response", on_response)


def extract_candidate_cards(page) -> list[dict]:
    # Keep this deliberately broad because Naver's SPA DOM can change.
    selectors = [
        "[class*='wordbook'] li",
        "[class*='Wordbook'] li",
        "[class*='wordbook'] [role='listitem']",
        "[class*='Wordbook'] [role='listitem']",
        "[class*='card']",
        "[class*='Card']",
        "li",
    ]
    cards: list[dict] = []
    seen: set[str] = set()

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 5000)
            for i in range(count):
                el = locator.nth(i)
                text = clean(el.inner_text(timeout=800))
                if not text or len(text) > 1500 or text in seen:
                    continue
                seen.add(text)
                cards.append({"text": text, "selector": selector})
        except Exception:
            continue
        if len(cards) >= 200:
            break

    return cards


def parse_cards(cards: list[dict], page_url: str) -> list[dict]:
    # Conservative parser: only promote fields when the DOM text strongly
    # suggests a stable label. Raw text is always retained.
    out = []
    for card in cards:
        text = card["text"]
        lines = [clean(x) for x in re.split(r"\n+", text) if clean(x)]
        word = lines[0] if lines else text
        meaning = ""
        for line in lines[1:]:
            if line != word and len(line) >= 2:
                meaning = line
                break
        out.append(
            {
                "word": word,
                "meaning": meaning,
                "pronunciation": "",
                "part_of_speech": "",
                "example": "",
                "wordbook": "",
                "source_url": page_url,
                "raw_text": text,
            }
        )
    return out


def save_snapshot(page, cards, network_events, mode: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "timestamp": now_iso(),
        "mode": mode,
        "url": page.url,
        "title": page.title(),
        "cards": cards,
        "network": network_events,
    }
    path = OUT / f"snapshot_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_repo_export(records: list[dict]) -> None:
    REPO_EXPORT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "source": "naver_dictionary_wordbook",
        "synced_at": now_iso(),
        "count": len(records),
        "words": records,
    }
    REPO_EXPORT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def bootstrap() -> None:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("NAVER에 로그인하고 내 단어장이 실제로 보이는 상태까지 만든 뒤 ENTER.")
        input(">>> ")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        print("URL:", page.url)
        print("TITLE:", page.title())
        print("PROFILE:", PROFILE)
        print("Saved. No NAVER password was stored.")
        context.close()


def run_sync(probe_only: bool = False) -> None:
    if not PROFILE.exists():
        raise SystemExit(
            f"Persistent browser profile not found: {PROFILE}\n"
            "Run --bootstrap on the Mac once."
        )

    con = init_db()
    network_events: list[dict] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
        )
        page = context.pages[0] if context.pages else context.new_page()
        capture_network(page, network_events)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # Give the SPA a chance to finish rendering / fetching its wordbook.
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        cards = extract_candidate_cards(page)
        snapshot = save_snapshot(page, cards, network_events, "probe" if probe_only else "sync")

        if probe_only:
            print(f"Probe snapshot: {snapshot}")
            print(f"Candidate cards: {len(cards)}")
            print(f"Network JSON/API events: {len(network_events)}")
            context.close()
            con.close()
            return

        if len(cards) == 0:
            context.close()
            con.close()
            raise SystemExit(
                "No wordbook cards were captured. This usually means the session "
                "expired, the wordbook route changed, or the SPA did not render."
                f"\nDiagnostic snapshot: {snapshot}"
            )

        records = parse_cards(cards, page.url)
        save_repo_export(records)

        now = now_iso()
        for record in records:
            con.execute(
                """
                INSERT INTO words
                (word, meaning, pronunciation, part_of_speech, example,
                 wordbook, source_url, first_seen, last_seen, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(word, meaning, wordbook) DO UPDATE SET
                  pronunciation=excluded.pronunciation,
                  part_of_speech=excluded.part_of_speech,
                  example=excluded.example,
                  source_url=excluded.source_url,
                  last_seen=excluded.last_seen,
                  raw_json=excluded.raw_json
                """,
                (
                    record["word"],
                    record["meaning"],
                    record["pronunciation"],
                    record["part_of_speech"],
                    record["example"],
                    record["wordbook"],
                    record["source_url"],
                    now,
                    now,
                    json.dumps(record, ensure_ascii=False),
                ),
            )
        con.commit()

        print(f"Captured cards: {len(cards)}")
        print(f"Export: {REPO_EXPORT}")
        print(f"DB: {DB}")
        print(f"Diagnostic snapshot: {snapshot}")
        context.close()

    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["--bootstrap", "--sync", "--probe"])
    args = ap.parse_args()
    if args.mode == "--bootstrap":
        bootstrap()
    elif args.mode == "--probe":
        run_sync(probe_only=True)
    else:
        run_sync()


if __name__ == "__main__":
    main()
