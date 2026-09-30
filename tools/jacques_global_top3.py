from __future__ import annotations

import argparse
import os
import sqlite3
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
            conn.execute(
                """
                INSERT INTO artists(name, spotify_id, spotify_url, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(name) DO UPDATE SET
                  spotify_id=COALESCE(excluded.spotify_id, artists.spotify_id),
                  spotify_url=COALESCE(excluded.spotify_url, artists.spotify_url),
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    name,
                    artist_id,
                    (artist.get("external_urls") or {}).get("spotify"),
                ),
            )
            row = conn.execute(
                "SELECT artist_id FROM artists WHERE name=?",
                (name,),
            ).fetchone()
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
            SELECT track_id, title, isrc,
                   (SELECT group_concat(a.name, ' & ')
                      FROM track_artists ta
                      JOIN artists a ON a.artist_id=ta.artist_id
                     WHERE ta.track_id=t.track_id) AS artists
            FROM tracks t
            WHERE track_id=?
            """,
            (item["track_id"],),
        ).fetchone()
        if row:
            tracks.append(dict(row))

    if tracks:
        print(f"[Jacques] FreqBlog enrichment: {len(tracks)} tracks")
        print(freqblog.enrich(conn, tracks, dry_run=False))


def main():
    parser = argparse.ArgumentParser(description="Jacques global Top-N discovery analysis")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--lookback-days", type=int, default=7)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    initialize_database()
    chart = fetch_latest_chart(lookback_days=args.lookback_days)
    items = chart["items"][: args.limit]

    if not items:
        raise RuntimeError("Spotify Global chart returned no tracks.")

    sp = _spotify_client()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        for item in items:
            _ensure_track(conn, sp, item)
        upsert_chart(conn, chart)
        conn.commit()

        _enrich_top3(conn, items)

        analyses = [analyze_track(conn, item) for item in items]
        store_analysis(conn, chart, analyses)
        conn.commit()

        report = write_report(ROOT, chart, analyses)
        print(f"[Jacques] chart={chart['chart_date']} top={len(analyses)}")
        for item in analyses:
            dna = item["production_dna"]
            print(
                f"#{item['rank']} {item['title']} — {item['artist']} | "
                f"{dna['archetype']} | {', '.join(dna['tags'])}"
            )
        print(f"[Jacques] report={report}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
