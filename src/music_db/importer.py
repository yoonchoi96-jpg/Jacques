import json
from pathlib import Path

from .database import get_connection, initialize_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = PROJECT_ROOT / "spotify_music_data.json"


def normalize_bool(value):
    return 1 if value else 0


def import_spotify_json(json_path=DEFAULT_JSON):
    json_path = Path(json_path)

    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    tracks = data.get("tracks", [])

    initialize_database()

    imported_tracks = 0
    imported_artists = 0
    imported_history = 0

    with get_connection() as conn:

        for track in tracks:
            track_id = track.get("track_id")

            if not track_id:
                continue

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
                    last_played,
                    top_short_term,
                    top_medium_term,
                    top_long_term
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    title = excluded.title,
                    album = excluded.album,
                    release_date = excluded.release_date,
                    spotify_url = excluded.spotify_url,
                    saved = excluded.saved,
                    saved_at = excluded.saved_at,
                    last_played = excluded.last_played,
                    top_short_term = excluded.top_short_term,
                    top_medium_term = excluded.top_medium_term,
                    top_long_term = excluded.top_long_term
                """,
                (
                    track_id,
                    track.get("title"),
                    track.get("album"),
                    track.get("release_date"),
                    track.get("spotify_url"),
                    normalize_bool(track.get("saved")),
                    track.get("saved_at"),
                    track.get("last_played"),
                    normalize_bool(track.get("top_short_term")),
                    normalize_bool(track.get("top_medium_term")),
                    normalize_bool(track.get("top_long_term")),
                ),
            )

            imported_tracks += 1

            raw_artists = track.get("artists", [])

            if isinstance(raw_artists, str):
                raw_artists = [
                    artist.strip()
                    for artist in raw_artists.split(",")
                    if artist.strip()
                ]

            for order, artist_name in enumerate(raw_artists):

                if not artist_name:
                    continue

                conn.execute(
                    """
                    INSERT INTO artists (name)
                    VALUES (?)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    (artist_name,),
                )

                artist_row = conn.execute(
                    """
                    SELECT artist_id
                    FROM artists
                    WHERE name = ?
                    """,
                    (artist_name,),
                ).fetchone()

                conn.execute(
                    """
                    INSERT OR REPLACE INTO track_artists (
                        track_id,
                        artist_id,
                        artist_order
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        track_id,
                        artist_row["artist_id"],
                        order,
                    ),
                )

                imported_artists += 1

            for played_at in track.get("play_history", []):
                if not played_at:
                    continue

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO play_history (
                        track_id,
                        played_at
                    )
                    VALUES (?, ?)
                    """,
                    (track_id, played_at),
                )

                if cursor.rowcount:
                    imported_history += 1

        conn.commit()

    return {
        "tracks": imported_tracks,
        "artists": imported_artists,
        "play_history": imported_history,
    }


if __name__ == "__main__":
    result = import_spotify_json()

    print("=" * 60)
    print("Spotify JSON -> SQLite import complete")
    print("=" * 60)
    print(f"Tracks       : {result['tracks']}")
    print(f"Artists      : {result['artists']}")
    print(f"Play history : {result['play_history']}")
