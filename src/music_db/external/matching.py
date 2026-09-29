from __future__ import annotations

import re
import unicodedata

def norm(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).casefold()
    value = re.sub(r"[’'´]", "", value)
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()

def artist_match(a: str | None, b: str | None) -> bool:
    x, y = norm(a), norm(b)
    return bool(x and y and (x == y or x in y or y in x))

def find_track(conn, title, artist=None, album=None):
    if not title:
        return None
    rows = conn.execute("""
        SELECT t.track_id, t.title, t.album, a.name AS artist_name
        FROM tracks t
        LEFT JOIN track_artists ta ON ta.track_id=t.track_id AND ta.artist_order=0
        LEFT JOIN artists a ON a.artist_id=ta.artist_id
        WHERE lower(t.title)=lower(?)
        ORDER BY t.saved DESC, t.last_played DESC
        LIMIT 25
    """, (title.strip(),)).fetchall()
    for row in rows:
        if artist and not artist_match(artist, row["artist_name"]):
            continue
        if album and row["album"] and norm(album) != norm(row["album"]):
            continue
        return row["track_id"]
    return None


def find_track_by_album(conn, album, artist=None):
    if not album:
        return None
    rows = conn.execute("""
        SELECT t.track_id, t.album, a.name AS artist_name
        FROM tracks t
        LEFT JOIN track_artists ta ON ta.track_id=t.track_id AND ta.artist_order=0
        LEFT JOIN artists a ON a.artist_id=ta.artist_id
        WHERE lower(t.album)=lower(?)
        ORDER BY t.saved DESC, t.last_played DESC
        LIMIT 50
    """, (album.strip(),)).fetchall()
    for row in rows:
        if artist and not artist_match(artist, row["artist_name"]):
            continue
        return row["track_id"]
    return None
