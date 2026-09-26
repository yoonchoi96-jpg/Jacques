import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import spotipy
from spotipy.oauth2 import SpotifyOAuth

import sys

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from music_db.enrichment.live_dispatcher import LiveEnrichmentDispatcher


# =========================================================
# .env loader
# =========================================================

def load_env_file(path=".env"):
    env_path = Path(path)

    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip()

        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()


# =========================================================
# Configuration
# =========================================================

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

REDIRECT_URI = "http://127.0.0.1:8888/callback"

SCOPE = (
    "user-library-read "
    "user-read-recently-played "
    "user-top-read"
)

CACHE_PATH = str(PROJECT_ROOT / ".spotify_cache")
DB_PATH = str(PROJECT_ROOT / "db" / "music.db")


if not CLIENT_ID:
    raise RuntimeError("SPOTIFY_CLIENT_ID is missing from .env")

if not CLIENT_SECRET:
    raise RuntimeError("SPOTIFY_CLIENT_SECRET is missing from .env")

if not REFRESH_TOKEN:
    raise RuntimeError("SPOTIFY_REFRESH_TOKEN is missing from .env")


# =========================================================
# Spotify connection
# =========================================================

oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    cache_path=CACHE_PATH,
)

# GitHub Actions 등 비대화형 환경에서는 refresh token을 사용한다.
token_info = oauth.refresh_access_token(REFRESH_TOKEN)

sp = spotipy.Spotify(
    auth=token_info["access_token"]
)


# =========================================================
# DB connection
# =========================================================

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")
conn.execute("PRAGMA busy_timeout = 30000")
conn.execute("PRAGMA journal_mode = WAL")


# =========================================================
# Statistics
# =========================================================

tracks_upserted = 0
artists_created = 0
artist_links = 0
play_history_added = 0

pending_tracks = {}


# =========================================================
# Track storage
# =========================================================

def upsert_track(track, saved=False, saved_at=None):
    global tracks_upserted
    global artists_created
    global artist_links

    if not track or not track.get("id"):
        return

    track_id = track["id"]

    isrc = (
        track.get("external_ids", {}) or {}
    ).get("isrc")

    conn.execute(
        """
        INSERT INTO tracks (
            track_id,
            title,
            album,
            release_date,
            spotify_url,
            saved,
            saved_at,
            duration_ms,
            isrc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(track_id) DO UPDATE SET
            title = excluded.title,
            album = excluded.album,
            release_date = excluded.release_date,
            spotify_url = excluded.spotify_url,
            duration_ms = excluded.duration_ms,

            isrc = COALESCE(
                excluded.isrc,
                tracks.isrc
            ),

            saved = CASE
                WHEN excluded.saved = 1 THEN 1
                ELSE tracks.saved
            END,

            saved_at = COALESCE(
                excluded.saved_at,
                tracks.saved_at
            )
        """,
        (
            track_id,
            track.get("name"),
            track.get("album", {}).get("name"),
            track.get("album", {}).get("release_date"),
            track.get("external_urls", {}).get("spotify"),
            int(saved),
            saved_at,
            track.get("duration_ms"),
            isrc,
        ),
    )

    tracks_upserted += 1


    # -----------------------------------------------------
    # Artists
    # -----------------------------------------------------

    for artist in track.get("artists", []):
        artist_id = artist.get("id")

        if not artist_id:
            continue

        existing = conn.execute(
            """
            SELECT 1
            FROM artists
            WHERE spotify_id = ?
            """,
            (artist_id,),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO artists (
                name,
                spotify_id,
                spotify_url
            )
            VALUES (?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                name = excluded.name,
                spotify_url = excluded.spotify_url
            """,
            (
                artist.get("name"),
                artist_id,
                artist.get("external_urls", {}).get("spotify"),
            ),
        )

        if existing is None:
            artists_created += 1

    # -----------------------------------------------------
    # Pending enrichment payload
    # -----------------------------------------------------

    pending_tracks[track_id] = {
        "track_id": track_id,
        "title": track.get("name"),
        "album": track.get("album", {}).get("name"),
        "release_date": track.get("album", {}).get("release_date"),
        "spotify_url": track.get("external_urls", {}).get("spotify"),
        "saved": int(saved),
        "saved_at": saved_at,
        "duration_ms": track.get("duration_ms"),
        "isrc": isrc,
    }


# =========================================================
# Saved tracks
# =========================================================

print("=== 1. Saved Tracks ===")

saved_total = sp.current_user_saved_tracks(limit=50)

saved_items = saved_total.get("items", [])

print(f"Spotify 좋아요 전체: {len(saved_items)}")

for item in saved_items:
    track = item.get("track")

    if not track:
        continue

    added_at = item.get("added_at")

    upsert_track(
        track,
        saved=True,
        saved_at=added_at,
    )

print(f"  수집: {len(saved_items)}/{len(saved_items)}")


# =========================================================
# Recently Played
# =========================================================

print("\n=== 2. Recently Played ===")

recent = sp.current_user_recently_played(limit=50)

recent_items = recent.get("items", [])

print(f"최근 재생 수: {len(recent_items)}")

for item in recent_items:
    track = item.get("track")

    if not track:
        continue

    upsert_track(track)

    played_at = item.get("played_at")

    if played_at:
        conn.execute(
            """
            INSERT OR IGNORE INTO play_history (
                track_id,
                played_at
            )
            VALUES (?, ?)
            """,
            (
                track["id"],
                played_at,
            ),
        )

        play_history_added += 1


# =========================================================
# Top Tracks
# =========================================================

print("\n=== 3. Top Tracks ===")

for term in [
    "short_term",
    "medium_term",
    "long_term",
]:
    result = sp.current_user_top_tracks(
        limit=50,
        time_range=term,
    )

    items = result.get("items", [])

    print(f"  {term}")
    print(f"    {len(items)} tracks")

    for track in items:
        upsert_track(track)


# =========================================================
# Commit Spotify data FIRST
# =========================================================

conn.commit()


# =========================================================
# Live enrichment
# =========================================================

print("\n=== 4. Live Enrichment ===")
print(f"Unique tracks queued: {len(pending_tracks)}")

dispatcher = LiveEnrichmentDispatcher(
    dry_run=False
)

try:
    for track in pending_tracks.values():
        dispatcher.enqueue(track)

    dispatcher.flush()

finally:
    dispatcher.close()


# =========================================================
# Final commit
# =========================================================

conn.commit()


# =========================================================
# Final statistics
# =========================================================

tracks_count = conn.execute(
    "SELECT COUNT(*) FROM tracks"
).fetchone()[0]

artists_count = conn.execute(
    "SELECT COUNT(*) FROM artists"
).fetchone()[0]

history_count = conn.execute(
    "SELECT COUNT(*) FROM play_history"
).fetchone()[0]

saved_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM tracks
    WHERE saved = 1
    """
).fetchone()[0]

top_long_count = conn.execute(
    """
    SELECT COUNT(*)
    FROM tracks
    WHERE top_long_term = 1
    """
).fetchone()[0]


print("\n")
print("=" * 60)
print("Spotify → SQLite + Live Enrichment 완료")
print("=" * 60)

print(f"Tracks          : {tracks_count}")
print(f"Artists         : {artists_count}")
print(f"Play history    : {history_count}")
print(f"Saved tracks    : {saved_count}")
print(f"Top long-term   : {top_long_count}")

print("\n이번 실행")
print(f"Tracks upserted : {tracks_upserted}")
print(f"Artists created : {artists_created}")
print(f"Artist links    : {artist_links}")
print(f"History added   : {play_history_added}")
print(f"Enrichment queued: {len(pending_tracks)}")

print("=" * 60)


conn.close()
