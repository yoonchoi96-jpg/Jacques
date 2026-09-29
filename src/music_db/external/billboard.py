from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from music_db.external.matching import find_track

SOURCE = "billboard"
BASE = "https://www.billboard.com/charts/"
DEFAULT_CHARTS = ("hot-100", "billboard-200")

def _num(text):
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else None

def _parse_items(soup):
    out = []
    for item in soup.select("li.o-chart-results-list__item"):
        title_el = item.select_one("h2#title-of-a-story") or item.select_one("h2.c-title")
        if not title_el:
            continue
        labels = [x.get_text(" ", strip=True) for x in item.select(".c-label")]
        nums = [_num(x) for x in labels if _num(x) is not None]
        rank = nums[0] if nums else None
        if rank is None:
            continue
        artist_el = item.select_one(".c-label.a-no-trucate")
        artist = artist_el.get_text(" ", strip=True) if artist_el else None
        out.append({
            "rank": rank,
            "title": title_el.get_text(" ", strip=True),
            "artist": artist,
            "previous_rank": nums[1] if len(nums) > 1 else None,
            "peak_rank": nums[2] if len(nums) > 2 else None,
            "weeks_on_chart": nums[3] if len(nums) > 3 else None,
        })
    return out

def fetch_chart(chart_name, session=None):
    session = session or requests.Session()
    url = BASE + chart_name + "/"
    r = session.get(url, headers={"User-Agent": "Jacques/1.0 (+music database)"}, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    date_value = None
    for sel in (".c-tagline.a-font-primary-bold", ".c-chart-info__date-range", "time"):
        el = soup.select_one(sel)
        if el:
            date_value = el.get("datetime") or el.get_text(" ", strip=True)
            break
    date_value = date_value or datetime.now(timezone.utc).date().isoformat()
    return {"chart_name": chart_name, "chart_date": date_value, "url": url, "items": _parse_items(soup)}

def sync(conn, charts=None, dry_run=False):
    charts = charts or [x.strip() for x in os.getenv("BILLBOARD_CHARTS", ",".join(DEFAULT_CHARTS)).split(",") if x.strip()]
    stats = {"charts": 0, "entries": 0, "matched": 0, "errors": 0}
    session = requests.Session()
    for chart in charts:
        try:
            data = fetch_chart(chart, session)
            stats["charts"] += 1
            for item in data["items"]:
                stats["entries"] += 1
                if dry_run:
                    continue
                track_id = find_track(conn, item["title"], item.get("artist"))
                conn.execute("""
                    INSERT INTO chart_entries
                    (source,chart_name,chart_date,rank,previous_rank,peak_rank,weeks_on_chart, title,artist_name,track_id,source_url,raw_data,observed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(source,chart_name,chart_date,rank) DO UPDATE SET
                      previous_rank=excluded.previous_rank, peak_rank=excluded.peak_rank,
                      weeks_on_chart=excluded.weeks_on_chart, title=excluded.title,
                      artist_name=excluded.artist_name, track_id=excluded.track_id,
                      source_url=excluded.source_url, raw_data=excluded.raw_data,
                      observed_at=excluded.observed_at
                """, (SOURCE, chart, data["chart_date"], item["rank"], item.get("previous_rank"),
                      item.get("peak_rank"), item.get("weeks_on_chart"), item["title"],
                      item.get("artist"), track_id, data["url"], json.dumps(item, ensure_ascii=False)))
                stats["matched"] += int(bool(track_id))
            if not dry_run:
                conn.commit()
        except Exception as exc:
            stats["errors"] += 1
            print(f"[Billboard] {chart}: {type(exc).__name__}: {exc}")
    return stats
