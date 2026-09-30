from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
import sys
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from music_db.database import initialize_database
from music_db.discovery.analyzer import analyze_track, store_analysis, upsert_chart, write_report
from music_db.discovery.spotify_charts import fetch_latest_chart
from music_db.enrichment.sources import freqblog


DB = ROOT / "db" / "music.db"


def _spotify_client():
    required = (
        os.getenv("SPOTIFY_CLIENT_ID"),
        os.getenv("SPOTIFY_CLIENT_SECRET"),
        os.getenv("SPOTIFY_REFRESH_TOKEN"),
    )
    if not all(required):
        return None

    oauth = SpotifyOAuth(
        client_id=required[0],
        client_secret=required[1],
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="user-library-read user-read-recently-played user-top-read",
    )
    token = oauth.refresh_access_token(required[2])
    return spotipy.Spotify(auth=token["access_token"])


def _resolve_track_id(sp, item):
    if item.get("track_id"):
        return item["track_id"]
    if not sp:
        return None
    artist = (item.get("artist") or "").split(",")[0].strip()
    query = f"track:{item['title']}" + (f" artist:{artist}" if artist else "")
    result = sp.search(q=query, type="track", limit=5)
    tracks = (result.get("tracks") or {}).get("items") or []
    if not tracks:
        return None
    return tracks[0].get("id")


def _ensure_track(conn, sp, item):
    track_id = item["track_id"]
    existing = conn.execute(
        "SELECT track_id FROM tracks WHERE track_id=?",
        (track_id,),
    ).fetchone()
    if existing:
        return

    metadata = sp.track(track_id) if sp else None
    title = item["title"]
    album = None
    release_date = None
    spotify_url = f"https://open.spotify.com/track/{track_id}"
    isrc = None
    artists = []

    if metadata:
        title = metadata.get("name") or title
        album_obj = metadata.get("album") or {}
        album = album_obj.get("name")
        release_date = album_obj.get("release_date")
        spotify_url = (metadata.get("external_urls") or {}).get("spotify") or spotify_url
        isrc = (metadata.get("external_ids") or {}).get("isrc")
        artists = metadata.get("artists") or []

    conn.execute(
        """
        INSERT OR IGNORE INTO tracks
          (track_id, title, album, release_date, spotify_url, isrc, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            track_id,
            title,
            album,
            release_date,
            spotify_url,
            isrc,
            metadata.get("duration_ms") if metadata else None,
        ),
    )

    if metadata:
        for order, artist in enumerate(artists):
            name = artist.get("name") or ""
            artist_id = artist.get("id")
            if not name:
                continue
            artist_url = (artist.get("external_urls") or {}).get("spotify")
            row = conn.execute(
                "SELECT artist_id FROM artists WHERE name=? ORDER BY artist_id LIMIT 1",
                (name,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE artists
                    SET spotify_id=COALESCE(?, spotify_id),
                        spotify_url=COALESCE(?, spotify_url),
                        updated_at=CURRENT_TIMESTAMP
                    WHERE artist_id=?
                    """,
                    (artist_id, artist_url, row[0]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO artists(name, spotify_id, spotify_url, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (name, artist_id, artist_url),
                )
                row = conn.execute(
                    "SELECT artist_id FROM artists WHERE name=? ORDER BY artist_id DESC LIMIT 1",
                    (name,),
                ).fetchone()

            if sp and artist_id:
                try:
                    artist_meta = sp.artist(artist_id)
                    for genre in artist_meta.get("genres") or []:
                        conn.execute(
                            "INSERT OR IGNORE INTO genres(name) VALUES (?)",
                            (genre,),
                        )
                        genre_row = conn.execute(
                            "SELECT genre_id FROM genres WHERE name=?",
                            (genre,),
                        ).fetchone()
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO track_genres(track_id, genre_id)
                            VALUES (?, ?)
                            """,
                            (track_id, genre_row[0]),
                        )
                except Exception as exc:
                    print(f"[Jacques] artist genre enrichment skipped: {name}: {exc}")

            conn.execute(
                """
                INSERT OR IGNORE INTO track_artists(track_id, artist_id, artist_order)
                VALUES (?, ?, ?)
                """,
                (track_id, row[0], order),
            )


def _enrich_top3(conn, items):
    if not os.getenv("FREQBLOG_API_KEY"):
        print("[Jacques] FREQBLOG_API_KEY missing; report will use existing audio data.")
        return

    tracks = []
    for item in items:
        row = conn.execute(
            """
            SELECT track_id, title, isrc
            FROM tracks
            WHERE track_id=?
            """,
            (item["track_id"],),
        ).fetchone()
        if row:
            artist_rows = conn.execute(
                """
                SELECT a.name
                FROM track_artists ta
                JOIN artists a ON a.artist_id=ta.artist_id
                WHERE ta.track_id=?
                ORDER BY ta.artist_order
                """,
                (item["track_id"],),
            ).fetchall()
            data = dict(row)
            data["artists"] = [r[0] for r in artist_rows]
            tracks.append(data)

    if tracks:
        print(f"[Jacques] FreqBlog enrichment: {len(tracks)} tracks")
        print(freqblog.enrich(conn, tracks, dry_run=False))


def main():
    parser = argparse.ArgumentParser(description="Jacques personal listening Top-N analysis")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    initialize_database()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                ph.track_id,
                COUNT(*) AS play_count,
                MAX(ph.played_at) AS last_played
            FROM play_history ph
            GROUP BY ph.track_id
            ORDER BY play_count DESC, last_played DESC, ph.track_id
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()

        if not rows:
            raise RuntimeError("No play history is available in Jacques.")

        items = []
        for row in rows:
            track = conn.execute(
                "SELECT track_id, title, album, release_date, spotify_url, isrc FROM tracks WHERE track_id=?",
                (row["track_id"],),
            ).fetchone()
            if not track:
                continue
            artists = [
                r[0] for r in conn.execute(
                    """
                    SELECT a.name
                    FROM track_artists ta
                    JOIN artists a ON a.artist_id=ta.artist_id
                    WHERE ta.track_id=?
                    ORDER BY ta.artist_order
                    """,
                    (row["track_id"],),
                ).fetchall()
            ]
            items.append({
                "track_id": row["track_id"],
                "title": track["title"],
                "artist": ", ".join(artists),
                "play_count": row["play_count"],
                "last_played": row["last_played"],
                "spotify_uri": f"spotify:track:{row['track_id']}",
            })

        _enrich_top3(conn, items)
        analyses = [analyze_track(conn, item) for item in items]
        store_analysis(conn, {"source": "spotify_play_history", "chart_name": "personal_top", "chart_date": datetime.now(timezone.utc).date().isoformat()}, analyses)
        conn.commit()

        report = write_report(ROOT, {"source": "spotify_play_history", "chart_name": "personal_top", "chart_date": datetime.now(timezone.utc).date().isoformat()}, analyses)
        print(f"[Jacques] personal listening top={len(analyses)}")
        for item in analyses:
            print(f"#{item['personal_rank']} {item['title']} — {item['artist']} | plays={item['play_count']}")
        print(f"[Jacques] report={report}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
