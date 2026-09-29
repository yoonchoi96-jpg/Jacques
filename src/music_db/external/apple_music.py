from __future__ import annotations

import json
import os
from datetime import date

import requests

from music_db.external.matching import find_track

SOURCE = "apple_music"
BASE = "https://api.music.apple.com/v1"

def _session(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "Jacques/1.0"})
    return s

def sync_catalog(conn, storefronts=None, dry_run=False):
    token = os.getenv("APPLE_MUSIC_DEVELOPER_TOKEN")
    if not token:
        return {"skipped": True, "reason": "APPLE_MUSIC_DEVELOPER_TOKEN missing"}
    storefronts = storefronts or [x.strip() for x in os.getenv("APPLE_MUSIC_STOREFRONTS", "kr,us,jp,gb,fr").split(",") if x.strip()]
    session = _session(token)
    stats = {"storefronts": 0, "charts": 0, "matched": 0, "errors": 0}
    for sf in storefronts:
        try:
            r = session.get(f"{BASE}/catalog/{sf}/charts", params={"types": "songs", "limit": 100}, timeout=20)
            r.raise_for_status()
            songs = (r.json().get("results", {}).get("songs") or [])
            stats["storefronts"] += 1
            for rank, item in enumerate(songs, 1):
                attrs = item.get("attributes", {})
                title, artist = attrs.get("name") or "", attrs.get("artistName") or ""
                track_id = find_track(conn, title, artist)
                stats["charts"] += 1
                stats["matched"] += int(bool(track_id))
                if dry_run:
                    continue
                conn.execute("""
                    INSERT INTO source_records
                    (source,entity_type,external_id,track_id,artist_name,title,url,data_json,observed_at)
                    VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(source,entity_type,external_id) DO UPDATE SET
                      track_id=excluded.track_id, artist_name=excluded.artist_name, title=excluded.title,
                      url=excluded.url, data_json=excluded.data_json, observed_at=excluded.observed_at
                """, (SOURCE, f"chart:{sf}", item.get("id"), track_id, artist, title, item.get("href"),
                      json.dumps(attrs, ensure_ascii=False)))
                conn.execute("""
                    INSERT INTO chart_entries
                    (source,chart_name,chart_date,rank,title,artist_name,track_id,source_url,raw_data,observed_at)
                    VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(source,chart_name,chart_date,rank) DO UPDATE SET
                      title=excluded.title, artist_name=excluded.artist_name, track_id=excluded.track_id,
                      source_url=excluded.source_url, raw_data=excluded.raw_data, observed_at=excluded.observed_at
                """, (SOURCE, f"songs:{sf}", date.today().isoformat(), rank, title, artist, track_id,
                      item.get("href"), json.dumps(attrs, ensure_ascii=False)))
            if not dry_run:
                conn.commit()
        except Exception as exc:
            stats["errors"] += 1
            print(f"[Apple Music] {sf}: {type(exc).__name__}: {exc}")
    return stats
