from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

from music_db.external.matching import find_track

SOURCE = "pitchfork"
FEEDS = (
    "https://pitchfork.com/feed/feed-album-reviews/rss",
    "https://pitchfork.com/feed/reviews/best/albums/rss",
    "https://pitchfork.com/feed/feed-track-reviews/rss",
)

def _text(el, tag):
    x = el.find(tag)
    return x.text.strip() if x is not None and x.text else ""

def _metadata(url, session):
    r = session.get(url, headers={"User-Agent": "Jacques/1.0 (+music database)"}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    og = soup.select_one('meta[property="og:title"]')
    title = og.get("content", "") if og else ""
    score = author = date = None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if not isinstance(obj, dict):
                continue
            if score is None:
                value = obj.get("ratingValue") or (obj.get("reviewRating") or {}).get("ratingValue")
                try:
                    score = float(value) if value is not None else None
                except (TypeError, ValueError):
                    pass
            if author is None:
                a = obj.get("author")
                author = a.get("name") if isinstance(a, dict) else a
            date = date or obj.get("datePublished")
    if score is None:
        m = re.search(r'"ratingValue"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', r.text)
        if m:
            score = float(m.group(1))
    return title, score, author, date

def sync(conn, dry_run=False):
    stats = {"feeds": 0, "reviews": 0, "matched": 0, "errors": 0}
    session = requests.Session()
    seen = set()
    for feed_url in FEEDS:
        try:
            r = session.get(feed_url, headers={"User-Agent": "Jacques/1.0 (+music database)"}, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            stats["feeds"] += 1
            for item in root.findall(".//item"):
                link = _text(item, "link")
                if not link or link in seen:
                    continue
                seen.add(link)
                rss_title, pub = _text(item, "title"), _text(item, "pubDate")
                stats["reviews"] += 1
                if dry_run:
                    continue
                try:
                    page_title, score, author, review_date = _metadata(link, session)
                except Exception:
                    page_title, score, author, review_date = rss_title, None, None, pub
                track_id = find_track(conn, page_title or rss_title)
                conn.execute("""
                    INSERT INTO review_entries
                    (source,entity_type,title,score,review_date,author,headline,url,track_id,raw_data,observed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(source,url) DO UPDATE SET
                      title=excluded.title, score=excluded.score, review_date=excluded.review_date,
                      author=excluded.author, headline=excluded.headline, track_id=excluded.track_id,
                      raw_data=excluded.raw_data, observed_at=excluded.observed_at
                """, (SOURCE, "album_review", page_title or rss_title, score, review_date,
                      author, rss_title, link, track_id,
                      json.dumps({"feed": feed_url, "title": rss_title, "pubDate": pub}, ensure_ascii=False)))
                stats["matched"] += int(bool(track_id))
            if not dry_run:
                conn.commit()
        except Exception as exc:
            stats["errors"] += 1
            print(f"[Pitchfork] {feed_url}: {type(exc).__name__}: {exc}")
    return stats
