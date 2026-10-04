from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import requests

from music_db.enrichment.merge import refresh_canonical_audio_features

from music_db.enrichment.sources.songbpm_core import (
    HEADERS,
    songbpm_search,
    classify_result,
    save_match,
)

SOURCE = "songbpm"
REQUEST_INTERVAL = 1.0
TIMEOUT = 30


def _now():
    return datetime.now(timezone.utc).isoformat()


def _set_status(
    conn,
    track_id,
    status,
    attempts=1,
    last_error=None,
    completed=False,
):
    now = _now()
    error_details = json.dumps(
        {
            "source": SOURCE,
            "entity_type": "audio_features",
            "status": status,
            "attempts": attempts,
            "error": last_error,
            "completed": completed,
            "recorded_at": now,
        },
        sort_keys=True,
    )

    conn.execute(
        """
        INSERT INTO enrichment_status (
            track_id,
            source,
            entity_type,
            status,
            attempts,
            last_error,
            error_details,
            last_attempted_at,
            completed_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, 'audio_features', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source, entity_type)
        DO UPDATE SET
            status = excluded.status,
            attempts = excluded.attempts,
            last_error = excluded.last_error,
            error_details = excluded.error_details,
            last_attempted_at = excluded.last_attempted_at,
            completed_at = excluded.completed_at,
            updated_at = excluded.updated_at
        """,
        (
            track_id,
            SOURCE,
            status,
            attempts,
            last_error,
            error_details,
            now,
            now if completed else None,
            now,
            now,
        ),
    )


def _has_source(conn, track_id):
    row = conn.execute(
        """
        SELECT 1
        FROM audio_feature_sources
        WHERE track_id = ?
          AND source = ?
        LIMIT 1
        """,
        (track_id, SOURCE),
    ).fetchone()

    return row is not None


def _artist_string(conn, track_id):
    row = conn.execute(
        """
        SELECT COALESCE(
            (
                SELECT GROUP_CONCAT(a.name, '|||')
                FROM track_artists ta
                JOIN artists a
                  ON a.artist_id = ta.artist_id
                WHERE ta.track_id = t.track_id
                ORDER BY ta.artist_order
            ),
            ''
        )
        FROM tracks t
        WHERE t.track_id = ?
        """,
        (track_id,),
    ).fetchone()

    return row[0] if row else ""



def enrich_one(conn, track, dry_run=False):
    """
    Live fallback용 1곡 단위 SongBPM enrichment.

    기존 enrich()의 검색/판정/저장 로직을 그대로 사용하되,
    기존 SongBPM source가 있어도 호출되면 다시 검색한다.
    """

    track_id = track["track_id"]

    # Defensive guard: SongBPM is a fallback only. Even if a caller
    # invokes enrich_one() directly, never query it when FreqBlog has
    # not reached a terminal not_found state.
    if _has_source(conn, track_id):
        return {
            "status": "skipped_existing",
            "track_id": track_id,
        }

    freqblog_status = conn.execute(
        """
        SELECT status
        FROM enrichment_status
        WHERE track_id = ?
          AND source = 'freqblog'
          AND entity_type = 'audio_features'
        LIMIT 1
        """,
        (track_id,),
    ).fetchone()

    if not freqblog_status or freqblog_status["status"] != "not_found":
        return {
            "status": "skipped_freqblog",
            "track_id": track_id,
        }

    title = track["title"]
    artists = _artist_string(conn, track_id)
    clean_artists = artists.replace("|||", " ")
    query = f"{title} - {clean_artists}".strip()

    print(f"[SongBPM FALLBACK] {title} | {artists}")

    if dry_run:
        print(f"  WOULD SEARCH | {query}")
        return {
            "status": "dry_run",
            "track_id": track_id,
        }

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        data = songbpm_search(session, query)
    except Exception as exc:
        _set_status(
            conn,
            track_id,
            "search_failed",
            attempts=1,
            last_error=str(exc),
            completed=False,
        )
        conn.commit()

        print(f"  SEARCH FAILED | {exc}")

        return {
            "status": "search_failed",
            "track_id": track_id,
            "error": str(exc),
        }

    if data.get("status") != "ok":
        error = f"http={data.get('http_status')}"

        _set_status(
            conn,
            track_id,
            "search_failed",
            attempts=1,
            last_error=error,
            completed=False,
        )
        conn.commit()

        print(
            f"  SEARCH FAILED"
            f" | status={data.get('status')}"
            f" | http={data.get('http_status')}"
        )

        return {
            "status": "search_failed",
            "track_id": track_id,
            "error": error,
        }

    candidates = data.get("results", [])

    result = classify_result(
        track_id,
        title,
        artists,
        track["duration_ms"],
        candidates,
    )

    status = result["status"]
    candidate = result["candidate"]

    if status not in ("EXACT MATCH", "FUZZY MATCH"):
        _set_status(
            conn,
            track_id,
            "no_data",
            attempts=1,
            last_error=None,
            completed=True,
        )
        conn.commit()

        print(f"  NO MATCH | candidates={len(candidates)}")

        return {
            "status": "no_match",
            "track_id": track_id,
            "candidates": len(candidates),
        }

    item = {
        "track_id": track_id,
        "title": title,
        "artist": artists,
        "candidate": candidate,
        "url": data.get("url"),
        "confidence": result["confidence"],
        "match_method": result["match_method"],
        "duration_diff": result["duration_diff"],
    }

    save_match(conn, item)
    refresh_canonical_audio_features(conn, track_id)

    _set_status(
        conn,
        track_id,
        "success",
        attempts=1,
        last_error=None,
        completed=True,
    )

    conn.commit()

    print(
        f"  {status}"
        f" | BPM={candidate.get('bpm')}"
        f" | KEY={candidate.get('key')}"
    )

    time.sleep(REQUEST_INTERVAL)

    return {
        "status": "success",
        "track_id": track_id,
        "match_status": status,
        "bpm": candidate.get("bpm"),
        "key": candidate.get("key"),
    }


def enrich(conn, tracks, dry_run=False):
    stats = {
        "success": 0,
        "exact_match": 0,
        "fuzzy_match": 0,
        "no_match": 0,
        "search_failed": 0,
        "skipped_freqblog": 0,
        "skipped_existing": 0,
        "error": 0,
    }

    # SongBPM is a fallback, not a parallel primary source.
    # Only terminal FreqBlog not_found tracks are eligible.
    fallback_tracks = []

    for track in tracks:
        track_id = track["track_id"]

        if _has_source(conn, track_id):
            stats["skipped_existing"] += 1
            continue

        status = conn.execute(
            """
            SELECT status
            FROM enrichment_status
            WHERE track_id = ?
              AND source = 'freqblog'
              AND entity_type = 'audio_features'
            LIMIT 1
            """,
            (track_id,),
        ).fetchone()

        if not status or status["status"] != "not_found":
            stats["skipped_freqblog"] += 1
            continue

        fallback_tracks.append(track)

    session = requests.Session()
    session.headers.update(HEADERS)

    for index, track in enumerate(fallback_tracks, 1):
        track_id = track["track_id"]
        title = track["title"]

        artists = _artist_string(conn, track_id)
        clean_artists = artists.replace("|||", " ")
        query = f"{title} - {clean_artists}".strip()

        print(
            f"[SongBPM {index}/{len(fallback_tracks)}] "
            f"FALLBACK | {title}"
        )

        if dry_run:
            print(f"  WOULD SEARCH | {query}")
            continue

        try:
            data = songbpm_search(session, query)
        except Exception as exc:
            stats["search_failed"] += 1
            _set_status(
                conn,
                track_id,
                "search_failed",
                attempts=1,
                last_error=str(exc),
                completed=False,
            )
            conn.commit()
            print(f"  SEARCH FAILED | {exc}")
            time.sleep(REQUEST_INTERVAL)
            continue

        if data.get("status") != "ok":
            stats["search_failed"] += 1
            _set_status(
                conn,
                track_id,
                "search_failed",
                attempts=1,
                last_error=f"http={data.get('http_status')}",
                completed=False,
            )
            conn.commit()
            print(
                "  SEARCH FAILED"
                f" | status={data.get('status')}"
                f" | http={data.get('http_status')}"
            )
            time.sleep(REQUEST_INTERVAL)
            continue

        candidates = data.get("results", [])

        result = classify_result(
            track_id,
            title,
            artists,
            track["duration_ms"],
            candidates,
        )

        status = result["status"]
        candidate = result["candidate"]

        if status == "EXACT MATCH":
            stats["exact_match"] += 1
        elif status == "FUZZY MATCH":
            stats["fuzzy_match"] += 1
        else:
            stats["no_match"] += 1
            _set_status(
                conn,
                track_id,
                "no_data",
                attempts=1,
                last_error=None,
                completed=True,
            )
            conn.commit()
            print(f"  NO MATCH | candidates={len(candidates)}")
            time.sleep(REQUEST_INTERVAL)
            continue

        item = {
            "track_id": track_id,
            "title": title,
            "artist": artists,
            "candidate": candidate,
            "url": data.get("url"),
            "confidence": result["confidence"],
            "match_method": result["match_method"],
            "duration_diff": result["duration_diff"],
        }

        save_match(conn, item)

        _set_status(
            conn,
            track_id,
            "success",
            attempts=1,
            last_error=None,
            completed=True,
        )

        conn.commit()
        stats["success"] += 1

        print(
            f"  {status}"
            f" | BPM={candidate.get('bpm')}"
            f" | KEY={candidate.get('key')}"
        )

        time.sleep(REQUEST_INTERVAL)

    return stats
