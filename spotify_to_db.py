import argparse
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import spotipy
from spotipy.oauth2 import SpotifyOAuth

import sys

parser = argparse.ArgumentParser()
parser.add_argument("--dry-run", action="store_true")
DRY_RUN = parser.parse_args().dry_run

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from music_db.database import initialize_database
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

if not DRY_RUN:
    initialize_database()


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

if DRY_RUN:
    from music_db.spotify_preview import (
        collect_spotify_tracks,
        preview_enrichment,
        print_dry_run_report,
    )

    preview_conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
    )
    preview_conn.row_factory = sqlite3.Row
    try:
        preview_tracks = collect_spotify_tracks(sp)
        candidates = preview_enrichment(preview_conn, preview_tracks)
        print_dry_run_report(preview_tracks, candidates)
    finally:
        preview_conn.close()
    raise SystemExit(0)


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
    # Album normalization
    # -----------------------------------------------------

    album = track.get("album", {}) or {}
    album_id = album.get("id")

    if album_id:
        conn.execute(
            """
            INSERT INTO albums (
                spotify_id, name, release_date, album_type,
                spotify_url, image_url, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                name = excluded.name,
                release_date = excluded.release_date,
                album_type = excluded.album_type,
                spotify_url = excluded.spotify_url,
                image_url = excluded.image_url,
                updated_at = excluded.updated_at
            """,
            (
                album_id,
                album.get("name"),
                album.get("release_date"),
                album.get("album_type"),
                album.get("external_urls", {}).get("spotify"),
                (album.get("images") or [{}])[0].get("url"),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        normalized_album = conn.execute(
            "SELECT album_id FROM albums WHERE spotify_id = ?",
            (album_id,),
        ).fetchone()

        if normalized_album:
            conn.execute(
                """
                INSERT OR IGNORE INTO track_albums
                    (track_id, album_id)
                VALUES (?, ?)
                """,
                (track_id, normalized_album[0]),
            )

    # -----------------------------------------------------
    # Artists + canonical track/artist links
    # -----------------------------------------------------

    for artist_order, artist in enumerate(track.get("artists", [])):
        artist_id = artist.get("id")

        if not artist_id:
            continue

        existing = conn.execute(
            """
            SELECT 1 FROM artists WHERE spotify_id = ?
            """,
            (artist_id,),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO artists (name, spotify_id, spotify_url)
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

        db_artist = conn.execute(
            "SELECT artist_id FROM artists WHERE spotify_id = ?",
            (artist_id,),
        ).fetchone()

        if db_artist:
            conn.execute(
                """
                INSERT INTO track_artists
                    (track_id, artist_id, artist_order)
                VALUES (?, ?, ?)
                ON CONFLICT(track_id, artist_id)
                DO UPDATE SET artist_order = excluded.artist_order
                """,
                (track_id, db_artist[0], artist_order),
            )

        if existing is None:
            artists_created += 1

    # -----------------------------------------------------
    # Pending enrichment payload
    # -----------------------------------------------------

    # A track can appear in Saved, Recently Played, and Top Tracks
    # during the same run. Preserve the strongest saved state when the
    # later Spotify pass encounters the same track again.
    existing_pending = pending_tracks.get(track_id, {})

    pending_tracks[track_id] = {
        "track_id": track_id,
        "title": track.get("name"),
        "album": track.get("album", {}).get("name"),
        "release_date": track.get("album", {}).get("release_date"),
        "spotify_url": track.get("external_urls", {}).get("spotify"),
        "saved": max(
            int(saved),
            int(existing_pending.get("saved", 0)),
        ),
        "saved_at": saved_at or existing_pending.get("saved_at"),
        "duration_ms": track.get("duration_ms"),
        "isrc": isrc or existing_pending.get("isrc"),
    }


# =========================================================
# Repair normalized Spotify relations
# =========================================================

def repair_missing_normalized_links():
    rows = conn.execute(
        """
        SELECT t.track_id
        FROM tracks t
        WHERE NOT EXISTS (
            SELECT 1 FROM track_artists ta
            WHERE ta.track_id = t.track_id
        )
        OR NOT EXISTS (
            SELECT 1 FROM track_albums ta
            WHERE ta.track_id = t.track_id
        )
        """
    ).fetchall()

    if not rows:
        return 0

    repaired = 0

    print(f"정규화 링크 복구 대상: {len(rows)}")

    for row in rows:
        track_id = row["track_id"] if hasattr(row, "keys") else row[0]

        try:
            track = sp.track(track_id)
            if track and track.get("id"):
                upsert_track(track)
                repaired += 1
        except Exception as exc:
            print(
                f"  relation repair failed | "
                f"{track_id} | {type(exc).__name__}: {exc}"
            )

    return repaired


# =========================================================
# Saved tracks
# =========================================================

print("=== 1. Saved Tracks ===")

# Saved/top flags are current Spotify snapshots, not historical facts.
# Reset the snapshot before ingesting the current paginated results so
# unliked tracks and tracks that fell out of a top range do not remain
# falsely marked forever.
conn.execute(
    """
    UPDATE tracks
    SET saved = 0,
        saved_at = NULL,
        top_short_term = 0,
        top_medium_term = 0,
        top_long_term = 0
    """
)

saved_items = []
saved_page = sp.current_user_saved_tracks(limit=50)

# Spotify returns paginated saved-track results. Walk every page so
# Jacques does not silently stop at the first 50 liked tracks.
while saved_page:
    page_items = saved_page.get("items", [])
    saved_items.extend(page_items)

    if not saved_page.get("next"):
        break

    saved_page = sp.next(saved_page)

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

repaired_links = repair_missing_normalized_links()
if repaired_links:
    print(f"  정규화 링크 복구: {repaired_links}")

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

        conn.execute(
            """
            UPDATE tracks
            SET last_played = CASE
                WHEN last_played IS NULL OR last_played < ?
                THEN ?
                ELSE last_played
            END
            WHERE track_id = ?
            """,
            (played_at, played_at, track["id"]),
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

    flag_column = {
        "short_term": "top_short_term",
        "medium_term": "top_medium_term",
        "long_term": "top_long_term",
    }[term]

    for track in items:
        upsert_track(track)

        conn.execute(
            f"""
            UPDATE tracks
            SET {flag_column} = 1
            WHERE track_id = ?
            """,
            (track["id"],),
        )



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

# =========================================================
# Final WAL checkpoint
# GitHub Actions commits only db/music.db
# =========================================================

conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
