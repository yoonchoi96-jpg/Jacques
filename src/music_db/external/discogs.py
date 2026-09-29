from __future__ import annotations

import json
import os
from difflib import SequenceMatcher

import requests

SOURCE = "discogs"
BASE = "https://api.discogs.com"
MIN_MATCH_SCORE = 0.75


def _norm(value):
    return " ".join(str(value or "").casefold().split())


def _match_score(result, artist, album):
    title = str(result.get("title") or "")
    parts = [x.strip() for x in title.split(" - ", 1)]
    result_artist = parts[0] if parts else ""
    result_album = parts[1] if len(parts) == 2 else title
    artist_score = SequenceMatcher(None, _norm(artist), _norm(result_artist)).ratio()
    album_score = SequenceMatcher(None, _norm(album), _norm(result_album)).ratio()
    if _norm(artist) == _norm(result_artist):
        artist_score = 1.0
    if _norm(album) == _norm(result_album):
        album_score = 1.0
    return (artist_score + album_score) / 2


def _best_release(results, artist, album):
    scored = [
        (float(_match_score(result, artist, album)), result)
        for result in results
        if isinstance(result, dict)
    ]
    if not scored:
        return None, 0.0
    score, result = max(scored, key=lambda item: item[0])
    return result, score


def sync(conn, limit=50, dry_run=False):
    token = os.getenv("DISCOGS_TOKEN")
    if not token:
        return {"skipped": True, "reason": "DISCOGS_TOKEN missing"}
    rows = conn.execute("""
        SELECT t.track_id, t.title, t.album, a.name AS artist_name
        FROM tracks t
        LEFT JOIN track_artists ta ON ta.track_id=t.track_id AND ta.artist_order=0
        LEFT JOIN artists a ON a.artist_id=ta.artist_id
        WHERE t.album IS NOT NULL
        ORDER BY t.saved DESC, t.last_played DESC
        LIMIT ?
    """, (limit,)).fetchall()
    s = requests.Session()
    s.headers.update({"Authorization": f"Discogs token={token}", "User-Agent": "Jacques/1.0"})
    stats = {"queried": 0, "saved": 0, "low_match": 0, "errors": 0}
    seen = set()
    for row in rows:
        key = (row["artist_name"], row["album"])
        if key in seen:
            continue
        seen.add(key)
        try:
            r = s.get(f"{BASE}/database/search", params={
                "artist": row["artist_name"], "release_title": row["album"],
                "type": "release", "per_page": 3,
            }, timeout=20)
            r.raise_for_status()
            results = r.json().get("results") or []
            stats["queried"] += 1
            if not results or dry_run:
                continue
            result, match_score = _best_release(
                results,
                row["artist_name"],
                row["album"],
            )
            if result is None or match_score < MIN_MATCH_SCORE:
                print(
                    f"[Discogs] LOW MATCH | {row['artist_name']} / "
                    f"{row['album']} | score={match_score:.3f}"
                )
                stats["low_match"] += 1
                continue
            external_id = str(result.get("id"))
            conn.execute("""
                INSERT INTO source_records
                (source,entity_type,external_id,track_id,artist_name,album_name,title,url,data_json,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(source,entity_type,external_id) DO UPDATE SET
                  track_id=excluded.track_id,artist_name=excluded.artist_name,
                  album_name=excluded.album_name,title=excluded.title,url=excluded.url,
                  data_json=excluded.data_json,observed_at=excluded.observed_at
            """, (SOURCE,"release",external_id,row["track_id"],row["artist_name"],row["album"],result.get("title"),
                  result.get("uri"),json.dumps(result,ensure_ascii=False)))
            stats["saved"] += 1
            conn.commit()
        except Exception as exc:
            stats["errors"] += 1
            print(f"[Discogs] {row['artist_name']} / {row['album']}: {type(exc).__name__}: {exc}")
    return stats
