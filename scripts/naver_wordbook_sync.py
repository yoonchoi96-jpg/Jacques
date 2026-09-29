#!/usr/bin/env python3
"""Probe/sync entry point for NAVER Dictionary wordbooks.

This program is intended to run on a user's own Mac via a GitHub
self-hosted runner. It never stores a NAVER password.

First run:
  python scripts/naver_wordbook_sync.py --bootstrap

Scheduled runs:
  python scripts/naver_wordbook_sync.py --sync

The persistent Playwright profile lives outside the repository by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://learn.dict.naver.com/wordbook/enkodict/#/my/main"
ROOT = Path(os.environ.get("NAVER_WORDBOOK_DATA", str(Path.home() / ".naver_wordbook")))
PROFILE = ROOT / "browser_profile"
OUT = ROOT / "exports"
DB = ROOT / "naver_wordbook.sqlite3"

ROOT.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
      CREATE TABLE IF NOT EXISTS words (
        id INTEGER PRIMARY KEY,
        word TEXT NOT NULL,
        meaning TEXT,
        wordbook TEXT,
        source_url TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        UNIQUE(word, meaning, wordbook)
      )
    """)
    con.commit()
    return con

def extract_cards(page):
    # Current DOM is intentionally discovered rather than relying on one old selector.
    selectors = [
        "[class*='wordbook'] li",
        "[class*='Wordbook'] li",
        "[class*='card']",
        "[class*='Card']",
        "li",
    ]
    cards = []
    seen = set()

    for selector in selectors:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 5000)):
                el = locator.nth(i)
                text = clean(el.inner_text(timeout=1000))
                if not text or len(text) > 1000:
                    continue
                key = text
                if key in seen:
                    continue
                seen.add(key)
                cards.append({"text": text})
        except Exception:
            continue
        if len(cards) >= 20:
            break

    return cards

def save_snapshot(page, cards):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "timestamp": datetime.now().isoformat(),
        "url": page.url,
        "title": page.title(),
        "cards": cards,
    }
    (OUT / f"snapshot_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def bootstrap():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=False,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("NAVER 로그인 후 내 단어장이 보이는 상태로 만든 다음 ENTER를 누르세요.")
        input(">>> ")
        page.reload(wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        print("URL:", page.url)
        print("TITLE:", page.title())
        print("로그인 프로필 저장:", PROFILE)
        context.close()

def sync():
    con = init_db()
    now = datetime.now().isoformat()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            headless=True,
            viewport={"width": 1440, "height": 1000},
            locale="ko-KR",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)

        cards = extract_cards(page)
        save_snapshot(page, cards)

        # At probe stage we preserve raw card text. Parsing into word/meaning
        # fields comes after the current DOM/API structure has been verified.
        for card in cards:
            con.execute(
                """INSERT OR IGNORE INTO words
                   (word, meaning, wordbook, source_url, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (card["text"], "", "unknown", page.url, now, now),
            )
        con.commit()

        print(f"Captured cards: {len(cards)}")
        print(f"DB: {DB}")
        print(f"Exports: {OUT}")
        context.close()
    con.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["--bootstrap", "--sync"])
    args = ap.parse_args()
    bootstrap() if args.mode == "--bootstrap" else sync()

if __name__ == "__main__":
    main()
