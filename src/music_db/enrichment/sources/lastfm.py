from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


BASE_URL = "https://ws.audioscrobbler.com/2.0/"
SOURCE = "lastfm"

load_dotenv()


def _now():
    return datetime.now(timezone.utc).isoformat()



def _set_status(
    conn,
    track_id,
    entity_type,
    status,
    attempts_delta=0,
    error=None,
):
    now = _now()

    conn.execute(
        """
        INSERT INTO enrichment_status (
            track_id,
            source,
            entity_type,
            status,
            attempts,
            last_error,
            last_attempted_at,
            completed_at,
            created_at,
            updated_at
        )
        VALUES (?, 'lastfm', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source, entity_type)
        DO UPDATE SET
            status = excluded.status,
            attempts = enrichment_status.attempts + excluded.attempts,
            last_error = excluded.last_error,
            last_attempted_at = excluded.last_attempted_at,
            completed_at = excluded.completed_at,
            updated_at = excluded.updated_at
        """,
        (
            track_id,
            entity_type,
            status,
            attempts_delta,
            error,
            now,
            now if status in ("success", "no_data", "not_found") else None,
            now,
            now,
        ),
    )


def _get_status(conn, track_id, entity_type):
    return conn.execute(
        """
        SELECT status
        FROM enrichment_status
        WHERE track_id = ?
          AND source = 'lastfm'
          AND entity_type = ?
        """,
        (track_id, entity_type),
    ).fetchone()

def _get_artists(conn, track_id):
    rows = conn.execute(
        """
        SELECT a.name
        FROM track_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.track_id = ?
        ORDER BY a.name
        """,
        (track_id,),
    ).fetchall()

    return [row[0] for row in rows if row[0]]


def _has_tags(
    conn,
    tag_type,
    track_id=None,
    artist=None,
    album=None,
):
    if tag_type == "track":
        if track_id is None:
            return False

        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM track_external_tags tet
                JOIN external_tags et
                  ON et.tag_id = tet.tag_id
                WHERE tet.track_id = ?
                  AND tet.source = 'lastfm'
                  AND et.source = 'lastfm'
                  AND et.tag_type = 'track'
            )
            """,
            (track_id,),
        ).fetchone()[0] == 1

    if tag_type == "artist":
        if not artist:
            return False

        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM track_external_tags tet
                JOIN external_tags et
                  ON et.tag_id = tet.tag_id
                JOIN track_artists ta
                  ON ta.track_id = tet.track_id
                JOIN artists a
                  ON a.artist_id = ta.artist_id
                WHERE tet.source = 'lastfm'
                  AND et.source = 'lastfm'
                  AND et.tag_type = 'artist'
                  AND LOWER(TRIM(a.name)) = LOWER(TRIM(?))
            )
            """,
            (artist,),
        ).fetchone()[0] == 1

    if tag_type == "album":
        if not artist or not album:
            return False

        return conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM track_external_tags tet
                JOIN external_tags et
                  ON et.tag_id = tet.tag_id
                JOIN tracks t
                  ON t.track_id = tet.track_id
                JOIN track_artists ta
                  ON ta.track_id = t.track_id
                JOIN artists a
                  ON a.artist_id = ta.artist_id
                WHERE tet.source = 'lastfm'
                  AND et.source = 'lastfm'
                  AND et.tag_type = 'album'
                  AND LOWER(TRIM(a.name)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(t.album)) = LOWER(TRIM(?))
            )
            """,
            (artist, album),
        ).fetchone()[0] == 1

    return False


def _save_tags(conn, track_id, tag_type, tags):
    now = _now()
    saved = 0

    for tag in tags:
        raw_tag = str(tag.get("name", "")).strip()

        if not raw_tag:
            continue

        count = tag.get("count")

        conn.execute(
            """
            INSERT OR IGNORE INTO external_tags
                (source, raw_tag, tag_type, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (SOURCE, raw_tag, tag_type, now),
        )

        tag_id = conn.execute(
            """
            SELECT tag_id
            FROM external_tags
            WHERE source = ?
              AND raw_tag = ?
              AND tag_type = ?
            """,
            (SOURCE, raw_tag, tag_type),
        ).fetchone()[0]

        conn.execute(
            """
            INSERT OR REPLACE INTO track_external_tags
                (track_id, tag_id, source, confidence, raw_value, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                track_id,
                tag_id,
                SOURCE,
                None,
                str(count) if count is not None else None,
                now,
            ),
        )

        saved += 1

    return saved


def _request_tags(session, artist, track=None, album=None):
    if track is not None:
        params = {
            "method": "track.getTopTags",
            "artist": artist,
            "track": track,
        }
    elif album is not None:
        params = {
            "method": "album.getTopTags",
            "artist": artist,
            "album": album,
        }
    else:
        params = {
            "method": "artist.getTopTags",
            "artist": artist,
        }

    params.update(
        {
            "api_key": os.environ["LASTFM_API_KEY"],
            "format": "json",
            "autocorrect": 1,
        }
    )

    response = session.get(
        BASE_URL,
        params=params,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        return {
            "status": "not_found",
            "tags": [],
            "error": data.get("message") or str(data.get("error")),
        }

    return {
        "status": "ok",
        "tags": data.get("toptags", {}).get("tag", []),
        "error": None,
    }


def enrich(conn, tracks, dry_run=False):

    api_key = os.environ.get("LASTFM_API_KEY")

    if not api_key:
        raise RuntimeError("LASTFM_API_KEY 환경변수가 없습니다.")

    session = requests.Session()

    stats = {
        "track_queries": 0,
        "track_saved": 0,
        "track_skipped": 0,
        "artist_queries": 0,
        "artist_saved": 0,
        "artist_skipped": 0,
        "album_queries": 0,
        "album_saved": 0,
        "album_skipped": 0,
        "no_tags": 0,
        "not_found": 0,
        "errors": 0,
    }

    requested_artists = set()
    requested_albums = set()

    for index, row in enumerate(tracks, 1):

        track_id = row["track_id"]
        title = row["title"]
        album = row["album"] or ""

        artists = _get_artists(conn, track_id)

        if not artists:
            stats["errors"] += 1
            print(
                f"[Last.fm {index}/{len(tracks)}] "
                f"NO ARTIST | {title}"
            )
            continue

        artist = artists[0]

        # ---------------------------------------------------------
        # TRACK
        # ---------------------------------------------------------

        track_status = _get_status(conn, track_id, "track")

        if track_status and track_status[0] in (
            "success",
            "no_data",
            "not_found",
        ):
            stats["track_skipped"] += 1
            print(
                f"[Last.fm {index}/{len(tracks)}] "
                f"TRACK STATUS={track_status[0]} | {title}"
            )

        elif _has_tags(conn, "track", track_id=track_id):
            if not dry_run:
                _set_status(
                    conn,
                    track_id,
                    "track",
                    "success",
                    attempts_delta=0,
                )
                conn.commit()

            stats["track_skipped"] += 1
            print(
                f"[Last.fm {index}/{len(tracks)}] "
                f"TRACK EXISTS | {title}"
            )

        elif dry_run:
            stats["track_queries"] += 1
            print(
                f"[Last.fm {index}/{len(tracks)}] "
                f"TRACK WOULD QUERY | {title}"
            )

        else:
            try:
                _set_status(
                    conn,
                    track_id,
                    "track",
                    "pending",
                    attempts_delta=1,
                )
                conn.commit()

                result = _request_tags(
                    session,
                    artist,
                    track=title,
                )

                stats["track_queries"] += 1

                if result["status"] == "not_found":
                    _set_status(
                        conn,
                        track_id,
                        "track",
                        "not_found",
                        attempts_delta=0,
                        error=result["error"],
                    )
                    conn.commit()

                    stats["not_found"] += 1

                    print(
                        f"[Last.fm {index}/{len(tracks)}] "
                        f"TRACK NOT FOUND | {title}"
                    )

                elif result["tags"]:
                    saved = _save_tags(
                        conn,
                        track_id,
                        "track",
                        result["tags"],
                    )

                    _set_status(
                        conn,
                        track_id,
                        "track",
                        "success",
                        attempts_delta=0,
                        error=None,
                    )

                    conn.commit()

                    stats["track_saved"] += saved

                    print(
                        f"[Last.fm {index}/{len(tracks)}] "
                        f"TRACK OK | {title} | {saved} tags"
                    )

                else:
                    _set_status(
                        conn,
                        track_id,
                        "track",
                        "no_data",
                        attempts_delta=0,
                        error=None,
                    )
                    conn.commit()

                    stats["no_tags"] += 1

                    print(
                        f"[Last.fm {index}/{len(tracks)}] "
                        f"TRACK NO TAGS | {title}"
                    )

            except Exception as exc:
                _set_status(
                    conn,
                    track_id,
                    "track",
                    "error",
                    attempts_delta=0,
                    error=str(exc),
                )
                conn.commit()

                stats["errors"] += 1

                print(
                    f"[Last.fm {index}/{len(tracks)}] "
                    f"TRACK ERROR | {title} | {exc}"
                )

        # ---------------------------------------------------------
        # ARTIST
        # ---------------------------------------------------------

        artist_key = artist.strip().casefold()

        artist_status = _get_status(conn, track_id, "artist")

        if (
            artist_status
            and artist_status[0] in ("success", "no_data", "not_found")
        ):
            stats["artist_skipped"] += 1
        elif (
            artist_key in requested_artists
            or _has_tags(
                conn,
                "artist",
                artist=artist,
            )
        ):
            stats["artist_skipped"] += 1

        elif dry_run:
            requested_artists.add(artist_key)
            stats["artist_queries"] += 1
            print(
                f"[Last.fm {index}/{len(tracks)}] "
                f"ARTIST WOULD QUERY | {artist}"
            )

        else:
            try:
                requested_artists.add(artist_key)

                tags = _request_tags(
                    session,
                    artist,
                )

                stats["artist_queries"] += 1

                if tags["status"] == "not_found":
                    _set_status(
                        conn,
                        track_id,
                        "artist",
                        "not_found",
                        attempts_delta=1,
                        error=tags["error"],
                    )
                    conn.commit()

                    stats["not_found"] += 1

                elif tags["tags"]:
                    saved = _save_tags(
                        conn,
                        track_id,
                        "artist",
                        tags["tags"],
                    )

                    _set_status(
                        conn,
                        track_id,
                        "artist",
                        "success",
                        attempts_delta=0,
                        error=None,
                    )
                    conn.commit()

                    stats["artist_saved"] += saved

                else:
                    _set_status(
                        conn,
                        track_id,
                        "artist",
                        "no_data",
                        attempts_delta=1,
                        error=None,
                    )
                    conn.commit()

                    stats["no_tags"] += 1

            except Exception as exc:
                _set_status(
                    conn,
                    track_id,
                    "artist",
                    "error",
                    attempts_delta=1,
                    error=str(exc),
                )
                conn.commit()

                stats["errors"] += 1

                print(
                    f"[Last.fm {index}/{len(tracks)}] "
                    f"ARTIST ERROR | {artist} | {exc}"
                )

        # ---------------------------------------------------------
        # ALBUM
        # ---------------------------------------------------------

        if album:

            album_key = (
                artist.strip().casefold(),
                album.strip().casefold(),
            )

            album_status = _get_status(conn, track_id, "album")

            if (
                album_status
                and album_status[0] in ("success", "no_data", "not_found")
            ):
                stats["album_skipped"] += 1
            elif (
                album_key in requested_albums
                or _has_tags(
                    conn,
                    "album",
                    artist=artist,
                    album=album,
                )
            ):
                stats["album_skipped"] += 1

            elif dry_run:
                requested_albums.add(album_key)
                stats["album_queries"] += 1
                print(
                    f"[Last.fm {index}/{len(tracks)}] "
                    f"ALBUM WOULD QUERY | {album}"
                )

            else:
                try:
                    requested_albums.add(album_key)

                    tags = _request_tags(
                        session,
                        artist,
                        album=album,
                    )

                    stats["album_queries"] += 1

                    if tags["status"] == "not_found":
                        _set_status(
                            conn,
                            track_id,
                            "album",
                            "not_found",
                            attempts_delta=1,
                            error=tags["error"],
                        )
                        conn.commit()

                        stats["not_found"] += 1

                    elif tags["tags"]:
                        saved = _save_tags(
                            conn,
                            track_id,
                            "album",
                            tags["tags"],
                        )

                        _set_status(
                            conn,
                            track_id,
                            "album",
                            "success",
                            attempts_delta=0,
                            error=None,
                        )
                        conn.commit()

                        stats["album_saved"] += saved

                    else:
                        _set_status(
                            conn,
                            track_id,
                            "album",
                            "no_data",
                            attempts_delta=1,
                            error=None,
                        )
                        conn.commit()

                        stats["no_tags"] += 1

                except Exception as exc:
                    _set_status(
                        conn,
                        track_id,
                        "error",
                        attempts_delta=1,
                        error=str(exc),
                    )
                    conn.commit()

                    stats["errors"] += 1

                    print(
                        f"[Last.fm {index}/{len(tracks)}] "
                        f"ALBUM ERROR | {album} | {exc}"
                    )

    return stats
