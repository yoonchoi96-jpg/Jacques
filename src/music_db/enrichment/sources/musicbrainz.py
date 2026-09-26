from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable

import requests

from musicbrainz_isrc_sync import (
    search_musicbrainz,
    choose_candidate,
    choose_release_id,
)

BASE_URL = "https://musicbrainz.org/ws/2"
REQUEST_INTERVAL = 1.1
TIMEOUT = 30

HEADERS = {
    "User-Agent": "SpotifyMusicDB/0.1 (personal music database)",
    "Accept": "application/json",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_entity(
    session: requests.Session,
    entity_type: str,
    mbid: str,
    include: str = "tags+genres",
):
    url = f"{BASE_URL}/{entity_type}/{mbid}"

    response = session.get(
        url,
        params={
            "fmt": "json",
            "inc": include,
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"HTTP {response.status_code}: "
            f"{entity_type}/{mbid}"
        )

    return response.json()


def ensure_external_id(
    conn: sqlite3.Connection,
    track_id: str,
    entity_type: str,
    external_id: str,
    match_status: str = "matched",
    match_score: float = 1.0,
    match_method: str = "existing_mbid",
):
    timestamp = now()

    conn.execute(
        """
        INSERT INTO external_ids (
            track_id,
            source,
            entity_type,
            external_id,
            match_status,
            match_score,
            match_method,
            created_at,
            updated_at
        )
        VALUES (?, 'musicbrainz', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source, entity_type)
        DO UPDATE SET
            external_id = excluded.external_id,
            match_status = excluded.match_status,
            match_score = excluded.match_score,
            match_method = excluded.match_method,
            updated_at = excluded.updated_at
        """,
        (
            track_id,
            entity_type,
            external_id,
            match_status,
            match_score,
            match_method,
            timestamp,
            timestamp,
        ),
    )


def save_tag(
    conn: sqlite3.Connection,
    track_id: str,
    raw_tag: str,
    tag_type: str,
    count=None,
):
    if not raw_tag:
        return False

    timestamp = now()

    conn.execute(
        """
        INSERT INTO external_tags (
            source,
            raw_tag,
            tag_type,
            created_at
        )
        VALUES ('musicbrainz', ?, ?, ?)
        ON CONFLICT(source, raw_tag, tag_type)
        DO NOTHING
        """,
        (
            raw_tag,
            tag_type,
            timestamp,
        ),
    )

    row = conn.execute(
        """
        SELECT tag_id
        FROM external_tags
        WHERE source = 'musicbrainz'
          AND raw_tag = ?
          AND tag_type = ?
        """,
        (
            raw_tag,
            tag_type,
        ),
    ).fetchone()

    if row is None:
        return False

    conn.execute(
        """
        INSERT INTO track_external_tags (
            track_id,
            tag_id,
            source,
            confidence,
            raw_value,
            created_at
        )
        VALUES (?, ?, 'musicbrainz', NULL, ?, ?)
        ON CONFLICT(track_id, tag_id, source)
        DO UPDATE SET
            raw_value = excluded.raw_value
        """,
        (
            track_id,
            row["tag_id"],
            str(count) if count is not None else None,
            timestamp,
        ),
    )

    return True


def save_tags(
    conn: sqlite3.Connection,
    track_id: str,
    data: dict,
    genre_type: str,
    tag_type: str,
):
    saved = 0

    for item in data.get("genres", []) or []:
        name = item.get("name")
        if name and save_tag(
            conn,
            track_id,
            name,
            genre_type,
            item.get("count"),
        ):
            saved += 1

    for item in data.get("tags", []) or []:
        name = item.get("name")
        if name and save_tag(
            conn,
            track_id,
            name,
            tag_type,
            item.get("count"),
        ):
            saved += 1

    return saved


def has_external_id(
    conn: sqlite3.Connection,
    track_id: str,
    entity_type: str,
):
    row = conn.execute(
        """
        SELECT 1
        FROM external_ids
        WHERE track_id = ?
          AND source = 'musicbrainz'
          AND entity_type = ?
          AND external_id IS NOT NULL
          AND external_id != ''
        LIMIT 1
        """,
        (
            track_id,
            entity_type,
        ),
    ).fetchone()

    return row is not None


def get_status(
    conn: sqlite3.Connection,
    track_id: str,
    entity_type: str,
):
    return conn.execute(
        """
        SELECT status
        FROM enrichment_status
        WHERE track_id = ?
          AND source = 'musicbrainz'
          AND entity_type = ?
        """,
        (
            track_id,
            entity_type,
        ),
    ).fetchone()


def set_status(
    conn: sqlite3.Connection,
    track_id: str,
    entity_type: str,
    status: str,
    attempts_delta: int = 0,
    error: str | None = None,
):
    timestamp = now()

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
        VALUES (
            ?,
            'musicbrainz',
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        ON CONFLICT(track_id, source, entity_type)
        DO UPDATE SET
            status = excluded.status,
            attempts =
                enrichment_status.attempts
                + excluded.attempts,
            last_error = excluded.last_error,
            last_attempted_at =
                excluded.last_attempted_at,
            completed_at =
                excluded.completed_at,
            updated_at =
                excluded.updated_at
        """,
        (
            track_id,
            entity_type,
            status,
            attempts_delta,
            error,
            timestamp,
            timestamp
            if status in (
                "success",
                "no_data",
                "not_found",
                "ambiguous",
            )
            else None,
            timestamp,
            timestamp,
        ),
    )


def hydrate_candidate_rows(
    conn: sqlite3.Connection,
    candidates,
):
    """
    Enrichment filter rows only contain candidate/filter fields.
    MusicBrainz needs its own existing DB state as well, so hydrate
    each candidate from tracks without changing the shared filter.
    """

    hydrated = []

    for candidate in candidates:
        row = conn.execute(
            """
            SELECT
                t.track_id,
                t.title,
                t.album,
                t.duration_ms,
                t.isrc,
                t.musicbrainz_recording_id,
                t.musicbrainz_release_id,
                t.musicbrainz_artist_id,
                t.musicbrainz_match_score,
                t.musicbrainz_match_status,
                COALESCE(
                    (
                        SELECT GROUP_CONCAT(
                            a.name,
                            '|||'
                        )
                        FROM track_artists ta
                        JOIN artists a
                          ON a.artist_id = ta.artist_id
                        WHERE ta.track_id = t.track_id
                        ORDER BY ta.artist_order
                    ),
                    ''
                ) AS artist
            FROM tracks t
            WHERE t.track_id = ?
            """,
            (candidate["track_id"],),
        ).fetchone()

        if row is not None:
            hydrated.append(row)

    return hydrated


def build_track_rows(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT
            t.track_id,
            t.title,
            t.album,
            t.duration_ms,
            t.isrc,
            t.musicbrainz_recording_id,
            t.musicbrainz_release_id,
            t.musicbrainz_artist_id,
            t.musicbrainz_match_score,
            t.musicbrainz_match_status,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(
                        a.name,
                        '|||'
                    )
                    FROM track_artists ta
                    JOIN artists a
                      ON a.artist_id = ta.artist_id
                    WHERE ta.track_id = t.track_id
                    ORDER BY ta.artist_order
                ),
                ''
            ) AS artist
        FROM tracks t
        WHERE t.isrc IS NOT NULL
          AND t.isrc != ''
        ORDER BY t.title
        """
    ).fetchall()


def save_recording_match(
    conn: sqlite3.Connection,
    row,
    recording,
    status: str,
    score: float | None,
):
    mbid = recording.get("id")

    credits = recording.get(
        "artist-credit",
        [],
    ) or []

    artist_id = None

    if credits:
        artist = credits[0].get("artist") or {}
        artist_id = artist.get("id")

    release_id = choose_release_id(
        row["album"],
        recording,
    )

    score_int = round((score or 0) * 100)

    conn.execute(
        """
        UPDATE tracks
        SET
            musicbrainz_recording_id = ?,
            musicbrainz_release_id = ?,
            musicbrainz_artist_id = ?,
            musicbrainz_match_score = ?,
            musicbrainz_match_status = ?
        WHERE track_id = ?
        """,
        (
            mbid,
            release_id,
            artist_id,
            score_int,
            status,
            row["track_id"],
        ),
    )

    ensure_external_id(
        conn,
        row["track_id"],
        "recording",
        mbid,
        match_status=status,
        match_score=score or 0,
        match_method="isrc_candidate_score",
    )

    if artist_id:
        ensure_external_id(
            conn,
            row["track_id"],
            "artist",
            artist_id,
            match_status="matched",
            match_score=1.0,
            match_method="recording_artist_credit",
        )

    if release_id:
        ensure_external_id(
            conn,
            row["track_id"],
            "release",
            release_id,
            match_status="matched",
            match_score=1.0,
            match_method="recording_release_context",
        )


def enrich(
    conn: sqlite3.Connection,
    candidates: Iterable,
    dry_run: bool = False,
):
    """
    Integrated MusicBrainz enrichment worker.

    Reuses the existing ISRC matcher from
    musicbrainz_isrc_sync.py.
    """

    rows = hydrate_candidate_rows(
        conn,
        candidates,
    )

    session = requests.Session()
    session.headers.update(HEADERS)

    recording_cache = {}
    artist_cache = {}
    release_cache = {}
    group_cache = {}

    stats = {
        "tracks": len(rows),
        "recording_queries": 0,
        "recording_skipped": 0,
        "matched": 0,
        "ambiguous": 0,
        "not_found": 0,
        "network_error": 0,
        "artist_queries": 0,
        "artist_skipped": 0,
        "release_queries": 0,
        "release_skipped": 0,
        "release_group_queries": 0,
        "release_group_skipped": 0,
        "tags_saved": 0,
    }

    last_request_at = 0.0

    def wait_for_mb():
        nonlocal last_request_at

        elapsed = time.monotonic() - last_request_at

        if elapsed < REQUEST_INTERVAL:
            time.sleep(
                REQUEST_INTERVAL - elapsed
            )

        last_request_at = time.monotonic()

    # ========================================================
    # 1. RECORDING RESOLUTION
    # ========================================================

    for index, row in enumerate(rows, 1):
        track_id = row["track_id"]

        print(
            f"[MB {index:02d}/{len(rows)}] "
            f"{row['title']}"
        )

        recording_id = row["musicbrainz_recording_id"]

        if recording_id:
            stats["recording_skipped"] += 1

        elif not row["isrc"]:
            stats["not_found"] += 1

        elif row["musicbrainz_match_status"] in (
            "not_found",
            "ambiguous",
        ):
            # Existing result is preserved.
            # Do not repeatedly hammer MusicBrainz.
            stats["recording_skipped"] += 1

        else:
            try:
                isrc = row["isrc"]

                if isrc in recording_cache:
                    data = recording_cache[isrc]
                else:
                    if dry_run:
                        print(
                            "    -> WOULD SEARCH ISRC"
                        )
                        stats["recording_queries"] += 1
                        continue

                    wait_for_mb()

                    data = search_musicbrainz(
                        isrc
                    )

                    recording_cache[isrc] = data
                    stats["recording_queries"] += 1

                recordings = data.get(
                    "recordings",
                    [],
                )

                recording, status, score = (
                    choose_candidate(
                        row,
                        recordings,
                    )
                )

                if recording is None:
                    if not dry_run:
                        conn.execute(
                            """
                            UPDATE tracks
                            SET
                                musicbrainz_recording_id = NULL,
                                musicbrainz_release_id = NULL,
                                musicbrainz_artist_id = NULL,
                                musicbrainz_match_score = 0,
                                musicbrainz_match_status = ?
                            WHERE track_id = ?
                            """,
                            (
                                "not_found",
                                track_id,
                            ),
                        )

                        set_status(
                            conn,
                            track_id,
                            "recording",
                            "not_found",
                            attempts_delta=1,
                        )

                    stats["not_found"] += 1

                else:
                    if not dry_run:
                        save_recording_match(
                            conn,
                            row,
                            recording,
                            status,
                            score,
                        )

                        set_status(
                            conn,
                            track_id,
                            "recording",
                            status,
                            attempts_delta=1,
                        )

                    stats[status] += 1

                    print(
                        f"    -> {status.upper()} "
                        f"score={round((score or 0) * 100)}"
                    )

            except Exception as exc:
                stats["network_error"] += 1

                print(
                    "    -> ERROR "
                    f"{type(exc).__name__}: {exc}"
                )

                if not dry_run:
                    set_status(
                        conn,
                        track_id,
                        "recording",
                        "error",
                        attempts_delta=1,
                        error=str(exc),
                    )

        if not dry_run:
            conn.commit()

    # ========================================================
    # Refresh rows after recording resolution
    # ========================================================

    if not dry_run:
        rows = build_track_rows(conn)
    else:
        rows = hydrate_candidate_rows(
            conn,
            candidates,
        )

    # ========================================================
    # 2. ARTIST
    # ========================================================

    for row in rows:
        track_id = row["track_id"]
        mbid = row["musicbrainz_artist_id"]

        if not mbid:
            continue

        status_row = get_status(
            conn,
            track_id,
            "artist",
        )

        if (
            status_row
            and status_row["status"]
            in (
                "success",
                "no_data",
                "not_found",
            )
        ):
            stats["artist_skipped"] += 1
            continue

        # Existing resolved entity: no API call required.
        if has_external_id(
            conn,
            track_id,
            "artist",
        ):
            stats["artist_skipped"] += 1
            artist_cache.setdefault(
                mbid,
                None,
            )
            continue

        if mbid in artist_cache:
            data = artist_cache[mbid]
            stats["artist_skipped"] += 1
        else:
            try:
                if dry_run:
                    print(
                        f"[ARTIST] WOULD QUERY {mbid}"
                    )
                    stats["artist_queries"] += 1
                    continue

                wait_for_mb()

                data = fetch_entity(
                    session,
                    "artist",
                    mbid,
                    "tags+genres",
                )

                artist_cache[mbid] = data
                stats["artist_queries"] += 1

            except Exception as exc:
                print(
                    f"[ARTIST ERROR] "
                    f"{track_id} / {mbid} / {exc}"
                )

                if not dry_run:
                    set_status(
                        conn,
                        track_id,
                        "artist",
                        "error",
                        attempts_delta=1,
                        error=str(exc),
                    )

                continue

        if dry_run:
            continue

        ensure_external_id(
            conn,
            track_id,
            "artist",
            mbid,
        )

        saved = save_tags(
            conn,
            track_id,
            data,
            "artist_genre",
            "artist_tag",
        )

        stats["tags_saved"] += saved

        set_status(
            conn,
            track_id,
            "artist",
            "success" if saved else "no_data",
            attempts_delta=1,
        )

        conn.commit()

    # ========================================================
    # 3. RELEASE → RELEASE GROUP
    # ========================================================

    release_cache = {}
    group_cache = {}

    for row in rows:
        track_id = row["track_id"]
        release_mbid = row["musicbrainz_release_id"]

        if not release_mbid:
            continue

        # ----------------------------------------------------
        # RELEASE
        # ----------------------------------------------------

        release_status = get_status(
            conn,
            track_id,
            "release",
        )

        if (
            release_status
            and release_status["status"]
            in (
                "success",
                "no_data",
                "not_found",
            )
        ):
            stats["release_skipped"] += 1
            continue

        # Existing resolved entity: no API call required.
        if has_external_id(
            conn,
            track_id,
            "release",
        ):
            stats["release_skipped"] += 1
            release_cache.setdefault(
                release_mbid,
                None,
            )
            continue

        try:
            if release_mbid in release_cache:
                release = release_cache[
                    release_mbid
                ]
                stats["release_skipped"] += 1

            else:
                if dry_run:
                    print(
                        f"[RELEASE] WOULD QUERY "
                        f"{release_mbid}"
                    )
                    stats["release_queries"] += 1
                    continue

                wait_for_mb()

                release = fetch_entity(
                    session,
                    "release",
                    release_mbid,
                    "release-groups",
                )

                release_cache[
                    release_mbid
                ] = release

                stats["release_queries"] += 1

            if dry_run:
                continue

            ensure_external_id(
                conn,
                track_id,
                "release",
                release_mbid,
            )

            rg = release.get("release-group")

            if not rg:
                set_status(
                    conn,
                    track_id,
                    "release",
                    "no_data",
                    attempts_delta=1,
                )
                conn.commit()
                continue

            group_mbid = rg.get("id")

            if not group_mbid:
                set_status(
                    conn,
                    track_id,
                    "release",
                    "no_data",
                    attempts_delta=1,
                )
                conn.commit()
                continue

            set_status(
                conn,
                track_id,
                "release",
                "success",
                attempts_delta=1,
            )

            # ------------------------------------------------
            # RELEASE GROUP
            # ------------------------------------------------

            group_status = get_status(
                conn,
                track_id,
                "release_group",
            )

            if group_status and group_status["status"] in (
                "success",
                "no_data",
                "not_found",
            ):
                stats["release_group_skipped"] += 1
                continue

            ensure_external_id(
                conn,
                track_id,
                "release_group",
                group_mbid,
                match_method="release_to_release_group",
            )

            if group_mbid in group_cache:
                group = group_cache[
                    group_mbid
                ]
                stats["release_group_skipped"] += 1

            else:
                wait_for_mb()

                group = fetch_entity(
                    session,
                    "release-group",
                    group_mbid,
                    "tags+genres",
                )

                group_cache[
                    group_mbid
                ] = group

                stats["release_group_queries"] += 1

            saved = save_tags(
                conn,
                track_id,
                group,
                "release_group_genre",
                "release_group_tag",
            )

            stats["tags_saved"] += saved

            set_status(
                conn,
                track_id,
                "release_group",
                "success" if saved else "no_data",
                attempts_delta=1,
            )

            conn.commit()

        except Exception as exc:
            print(
                f"[RELEASE ERROR] "
                f"{track_id} / "
                f"{release_mbid} / {exc}"
            )

            if not dry_run:
                set_status(
                    conn,
                    track_id,
                    "release",
                    "error",
                    attempts_delta=1,
                    error=str(exc),
                )
                conn.commit()

    session.close()

    print()
    print("=" * 70)
    print("MUSICBRAINZ ENRICHMENT RESULT")
    print("=" * 70)

    for key, value in stats.items():
        print(
            f"{key:25}: {value}"
        )

    print("=" * 70)

    return stats


if __name__ == "__main__":
    conn = sqlite3.connect(
        "db/music.db"
    )
    conn.row_factory = sqlite3.Row

    rows = build_track_rows(conn)

    enrich(
        conn,
        rows,
        dry_run=False,
    )

    conn.close()
