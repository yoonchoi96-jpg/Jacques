from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

from music_db.enrichment.merge import refresh_canonical_audio_features


BASE_URL = "https://api.freqblog.com"
LOOKUP_URL = f"{BASE_URL}/lookup"
SOURCE = "freqblog"

POLL_MAX_ATTEMPTS = 5
POLL_DEFAULT_SECONDS = 15
TIMEOUT = 30


def _now():
    return datetime.now(timezone.utc).isoformat()


def _set_status(
    conn,
    track_id,
    status,
    *,
    error=None,
):
    now = _now()

    existing = conn.execute(
        """
        SELECT attempts, created_at
        FROM enrichment_status
        WHERE track_id = ?
          AND source = ?
          AND entity_type = 'audio_features'
        """,
        (track_id, SOURCE),
    ).fetchone()

    attempts = (existing["attempts"] if existing else 0) + 1
    created_at = existing["created_at"] if existing else now

    completed_at = now if status == "success" else None

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
        VALUES (?, ?, 'audio_features', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source, entity_type)
        DO UPDATE SET
            status = excluded.status,
            attempts = excluded.attempts,
            last_error = excluded.last_error,
            last_attempted_at = excluded.last_attempted_at,
            completed_at = excluded.completed_at,
            updated_at = excluded.updated_at
        """,
        (
            track_id,
            SOURCE,
            status,
            attempts,
            error,
            now,
            completed_at,
            created_at,
            now,
        ),
    )


def _save(conn, track_id, isrc, data):
    now = _now()

    conn.execute(
        """
        INSERT INTO audio_feature_sources (
            track_id,
            source,
            tempo,
            tempo_confidence,
            key,
            key_confidence,
            loudness,
            energy,
            danceability,
            valence,
            acousticness,
            instrumentalness,
            speechiness,
            confidence,
            source_url,
            raw_data,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(track_id, source)
        DO UPDATE SET
            tempo = excluded.tempo,
            tempo_confidence = excluded.tempo_confidence,
            key = excluded.key,
            key_confidence = excluded.key_confidence,
            loudness = excluded.loudness,
            energy = excluded.energy,
            danceability = excluded.danceability,
            valence = excluded.valence,
            acousticness = excluded.acousticness,
            instrumentalness = excluded.instrumentalness,
            speechiness = excluded.speechiness,
            confidence = excluded.confidence,
            source_url = excluded.source_url,
            raw_data = excluded.raw_data,
            updated_at = excluded.updated_at
        """,
        (
            track_id,
            SOURCE,
            data.get("bpm"),
            data.get("bpm_confidence"),
            data.get("key"),
            data.get("key_confidence"),
            data.get("loudness_db"),
            data.get("energy"),
            data.get("danceability"),
            data.get("valence"),
            data.get("acousticness"),
            data.get("instrumentalness"),
            data.get("speechiness"),
            data.get("bpm_confidence"),
            f"{LOOKUP_URL}?isrc={isrc}" if isrc else LOOKUP_URL,
            json.dumps(data, ensure_ascii=False),
            now,
            now,
        ),
    )
    refresh_canonical_audio_features(conn, track_id)


def _artists(track):
    value = track.get("artists") or ""

    if isinstance(value, list):
        return [
            str(x.get("name", "")).strip()
            if isinstance(x, dict)
            else str(x).strip()
            for x in value
        ]

    return [
        x.strip()
        for x in str(value).split("|||")
        if x.strip()
    ]


def _name_params(track):
    title = str(track.get("title") or "").strip()
    artists = _artists(track)

    return {
        "track": title,
        "artist": " & ".join(artists),
        "wait": 15,
    }


def _poll_url(session, poll_url, headers):
    """
    202 응답의 poll_url을 따라간다.

    202 자체는 정상적인 queued 상태.
    Retry-After를 존중하고 최대 5회 polling.
    """

    url = urljoin(BASE_URL, poll_url)

    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        response = session.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
        )

        if response.status_code == 200:
            return {
                "kind": "success",
                "data": response.json(),
            }

        if response.status_code == 202:
            retry_after = response.headers.get("Retry-After")

            try:
                delay = int(retry_after)
            except (TypeError, ValueError):
                delay = POLL_DEFAULT_SECONDS

            print(
                f"  -> STILL QUEUED | "
                f"poll={attempt}/{POLL_MAX_ATTEMPTS} | "
                f"sleep={delay}s"
            )

            if attempt < POLL_MAX_ATTEMPTS:
                time.sleep(max(1, min(delay, 60)))

            continue

        if response.status_code == 404:
            try:
                body = response.json()
            except Exception:
                body = {}

            return {
                "kind": "terminal",
                "data": body,
            }

        if response.status_code == 429:
            return {
                "kind": "retryable",
                "error": "HTTP 429",
            }

        if response.status_code >= 500:
            return {
                "kind": "retryable",
                "error": f"HTTP {response.status_code}",
            }

        return {
            "kind": "terminal",
            "data": response.json()
            if response.content
            else {},
        }

    return {
        "kind": "retryable",
        "error": "Polling attempts exhausted",
    }


def _lookup(session, headers, params):
    """
    FreqBlog lookup 결과를
    success / terminal / retryable 로 정규화한다.
    """

    try:
        response = session.get(
            LOOKUP_URL,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return {
            "kind": "retryable",
            "error": "timeout",
        }
    except requests.exceptions.RequestException as exc:
        return {
            "kind": "retryable",
            "error": str(exc),
        }

    if response.status_code == 200:
        return {
            "kind": "success",
            "data": response.json(),
        }

    if response.status_code == 202:
        try:
            body = response.json()
        except Exception:
            body = {}

        poll_url = body.get("poll_url")

        if not poll_url:
            location = response.headers.get("Location")

            if location:
                poll_url = location

        if not poll_url:
            return {
                "kind": "retryable",
                "error": "202 without poll_url",
            }

        print("  -> QUEUED")

        return _poll_url(
            session,
            poll_url,
            headers,
        )

    if response.status_code == 404:
        try:
            body = response.json()
        except Exception:
            body = {}

        return {
            "kind": "terminal",
            "data": body,
        }

    if response.status_code == 429:
        return {
            "kind": "retryable",
            "error": "HTTP 429",
        }

    if response.status_code >= 500:
        return {
            "kind": "retryable",
            "error": f"HTTP {response.status_code}",
        }

    try:
        body = response.json()
    except Exception:
        body = {}

    return {
        "kind": "terminal",
        "data": body,
    }


def enrich_one(conn, track, dry_run=False):
    """
    Live pipeline용 단일 트랙 FreqBlog enrichment.

    순서:
      1. ISRC exact lookup
      2. ISRC miss -> title + artist lookup
      3. 202 -> poll
      4. terminal failure -> caller가 SongBPM fallback
      5. transient failure -> recovery 대상
    """

    track_id = track["track_id"]
    title = track.get("title") or ""
    isrc = (track.get("isrc") or "").strip()

    result = {
        "success": False,
        "terminal": False,
        "retryable": False,
        "reason": None,
    }

    api_key = os.environ.get("FREQBLOG_API_KEY")

    if not api_key:
        raise RuntimeError(
            "FREQBLOG_API_KEY 환경변수가 없습니다."
        )

    if dry_run:
        print(
            f"[FreqBlog LIVE] WOULD QUERY | "
            f"{title} | "
            f"ISRC={isrc or 'NONE'}"
        )

        result["reason"] = "dry_run"
        return result

    headers = {
        "X-Api-Key": api_key,
    }

    session = requests.Session()
    session.headers.update(headers)

    _set_status(
        conn,
        track_id,
        "queued",
    )
    conn.commit()

    # ---------------------------------------------------------
    # 1. ISRC exact lookup
    # ---------------------------------------------------------
    if isrc:
        print(
            f"[FreqBlog LIVE] ISRC | "
            f"{title} | {isrc}"
        )

        lookup = _lookup(
            session,
            headers,
            {
                "isrc": isrc,
            },
        )

        if lookup["kind"] == "success":
            data = lookup["data"]

            returned_isrc = data.get("isrc")

            if returned_isrc:
                normalized_requested = (
                    isrc.replace("-", "").upper()
                )
                normalized_returned = (
                    returned_isrc.replace("-", "").upper()
                )

                if normalized_requested != normalized_returned:
                    print(
                        f"  -> ISRC MISMATCH | "
                        f"requested={isrc} "
                        f"returned={returned_isrc}"
                    )
                else:
                    _save(
                        conn,
                        track_id,
                        isrc,
                        data,
                    )

                    _set_status(
                        conn,
                        track_id,
                        "success",
                    )

                    conn.commit()

                    print(
                        f"  -> OK | "
                        f"BPM={data.get('bpm')} | "
                        f"Key={data.get('key')}"
                    )

                    result["success"] = True
                    result["reason"] = "isrc"
                    return result

            else:
                _save(
                    conn,
                    track_id,
                    isrc,
                    data,
                )

                _set_status(
                    conn,
                    track_id,
                    "success",
                )

                conn.commit()

                result["success"] = True
                result["reason"] = "isrc"
                return result

        elif lookup["kind"] == "retryable":
            error = lookup.get("error")

            _set_status(
                conn,
                track_id,
                "error",
                error=error,
            )
            conn.commit()

            result["retryable"] = True
            result["reason"] = error

            print(
                f"  -> RETRYABLE | {error}"
            )

            return result

        else:
            print(
                "  -> ISRC NOT FOUND | "
                "falling back to NAME"
            )

    # ---------------------------------------------------------
    # 2. Title + Artist lookup
    # ---------------------------------------------------------
    params = _name_params(track)

    print(
        f"[FreqBlog LIVE] NAME | "
        f"{params['track']} | "
        f"{params['artist']}"
    )

    lookup = _lookup(
        session,
        headers,
        params,
    )

    if lookup["kind"] == "success":
        data = lookup["data"]

        resolved_isrc = (
            data.get("isrc")
            or isrc
            or None
        )

        _save(
            conn,
            track_id,
            resolved_isrc,
            data,
        )

        _set_status(
            conn,
            track_id,
            "success",
        )

        # Keep Spotify/DB ISRC up to date when FreqBlog resolved one.
        if data.get("isrc"):
            conn.execute(
                """
                UPDATE tracks
                SET isrc = ?
                WHERE track_id = ?
                """,
                (
                    data["isrc"],
                    track_id,
                ),
            )

        conn.commit()

        print(
            f"  -> OK | "
            f"BPM={data.get('bpm')} | "
            f"Key={data.get('key')}"
        )

        result["success"] = True
        result["reason"] = "name"
        return result

    if lookup["kind"] == "terminal":
        body = lookup.get("data") or {}

        detail = body.get(
            "detail",
            "FreqBlog terminal not found",
        )

        _set_status(
            conn,
            track_id,
            "not_found",
            error=str(detail),
        )
        conn.commit()

        print(
            f"  -> TERMINAL NOT FOUND | "
            f"{detail}"
        )

        result["terminal"] = True
        result["reason"] = str(detail)
        return result

    error = lookup.get("error")

    _set_status(
        conn,
        track_id,
        "error",
        error=error,
    )
    conn.commit()

    print(
        f"  -> RETRYABLE | {error}"
    )

    result["retryable"] = True
    result["reason"] = error

    return result


def enrich(conn, tracks, dry_run=False):
    """
    기존 batch engine 호환용.

    engine.py에서 계속 호출할 수 있다.
    """

    stats = {
        "success": 0,
        "not_found": 0,
        "rate_limit": 0,
        "timeout": 0,
        "error": 0,
        "skipped": 0,
    }

    for index, track in enumerate(tracks, 1):
        print(
            f"[FreqBlog {index}/{len(tracks)}] "
            f"{track['title']}"
        )

        try:
            result = enrich_one(
                conn,
                track,
                dry_run=dry_run,
            )

            if result["success"]:
                stats["success"] += 1

            elif result["terminal"]:
                stats["not_found"] += 1

            elif result["retryable"]:
                reason = str(
                    result.get("reason") or ""
                )

                if "429" in reason:
                    stats["rate_limit"] += 1
                elif "timeout" in reason.lower():
                    stats["timeout"] += 1
                else:
                    stats["error"] += 1

            else:
                stats["skipped"] += 1

        except Exception as exc:
            stats["error"] += 1

            print(
                f"  -> ERROR | "
                f"{type(exc).__name__}: {exc}"
            )

    return stats


def enrich_bulk(conn, tracks, dry_run=False):
    """
    Live micro-batch FreqBlog enrichment.

    최대 15곡을 한 번에 POST /bulk.
    결과별로:
      success=True              -> DB 저장
      processing/queued        -> retryable
      found=False terminal     -> SongBPM fallback 대상
    """

    stats = {
        "success": 0,
        "processing": 0,
        "not_found": 0,
        "retryable": 0,
        "error": 0,
    }

    if not tracks:
        return stats

    api_key = os.environ.get("FREQBLOG_API_KEY")

    if not api_key:
        raise RuntimeError(
            "FREQBLOG_API_KEY 환경변수가 없습니다."
        )

    if dry_run:
        print(
            f"[FreqBlog BULK] WOULD QUERY | "
            f"{len(tracks)} tracks"
        )

        for track in tracks:
            print(
                f"  - {track.get('title')} | "
                f"{track.get('isrc') or 'NO ISRC'}"
            )

        return stats

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    payload = []

    for track in tracks:
        item = {}

        isrc = (track.get("isrc") or "").strip()

        if isrc:
            item["isrc"] = isrc

        title = str(
            track.get("title") or ""
        ).strip()

        if title:
            item["track"] = title

        artists = _artists(track)

        if artists:
            item["artist"] = " & ".join(artists)

        payload.append(item)

    session = requests.Session()

    try:
        response = session.post(
            f"{BASE_URL}/bulk",
            headers=headers,
            json=payload,
            timeout=40,
        )

    except requests.exceptions.Timeout:
        stats["retryable"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error="bulk timeout",
            )

        conn.commit()

        return stats

    except requests.exceptions.RequestException as exc:
        stats["retryable"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error=str(exc),
            )

        conn.commit()

        return stats

    if response.status_code == 429:
        retry_after = response.headers.get(
            "Retry-After",
            "unknown",
        )

        print(
            f"[FreqBlog BULK] RATE LIMIT | "
            f"Retry-After={retry_after}"
        )

        stats["retryable"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error=f"HTTP 429 Retry-After={retry_after}",
            )

        conn.commit()

        return stats

    if response.status_code >= 500:
        stats["retryable"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error=f"HTTP {response.status_code}",
            )

        conn.commit()

        return stats

    if response.status_code != 200:
        error = f"HTTP {response.status_code}"

        try:
            body = response.json()
            error = body.get(
                "detail",
                error,
            )
        except Exception:
            pass

        stats["error"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error=str(error),
            )

        conn.commit()

        print(
            f"[FreqBlog BULK] ERROR | {error}"
        )

        return stats

    try:
        body = response.json()
    except Exception as exc:
        stats["error"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error=f"invalid JSON: {exc}",
            )

        conn.commit()

        return stats

    results = body.get("results")

    if not isinstance(results, list):
        stats["error"] = len(tracks)

        for track in tracks:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error="bulk response missing results",
            )

        conn.commit()

        return stats

    # Match each response item to the submitted track by stable identity.
    # Positional matching is safe only when the provider returns no identity
    # fields at all. Never let an explicit but unmatched identity silently
    # attach another track's audio features.
    def _norm(value):
        return " ".join(
            str(value or "").strip().casefold().split()
        )

    def _norm_isrc(value):
        return str(value or "").replace("-", "").strip().upper()

    by_isrc = {}
    by_title_artist = {}

    for track in tracks:
        isrc = _norm_isrc(track.get("isrc"))
        if isrc:
            by_isrc.setdefault(isrc, []).append(track)

        title = _norm(track.get("title"))
        artist = _norm(" ".join(_artists(track)))
        if title and artist:
            by_title_artist.setdefault(
                (title, artist), []
            ).append(track)

    matched_track_ids = set()

    for index, result in enumerate(results):
        if not isinstance(result, dict):
            continue

        track = None
        identity_present = False

        result_isrc = result.get("isrc")
        if result_isrc:
            identity_present = True
            candidates = by_isrc.get(
                _norm_isrc(result_isrc),
                [],
            )
            if len(candidates) == 1:
                track = candidates[0]

        result_data = result.get("result")
        result_title = result.get("track") or result.get("title")
        result_artist = result.get("artist")

        if isinstance(result_data, dict):
            result_title = (
                result_title
                or result_data.get("track")
                or result_data.get("title")
            )
            result_artist = (
                result_artist
                or result_data.get("artist")
            )

        if result_title or result_artist:
            identity_present = True

        if track is None and result_title and result_artist:
            candidates = by_title_artist.get(
                (
                    _norm(result_title),
                    _norm(result_artist),
                ),
                [],
            )
            if len(candidates) == 1:
                track = candidates[0]

        if track is None and not identity_present and index < len(tracks):
            track = tracks[index]

        if track is None:
            print(
                "[FreqBlog BULK] UNMATCHED RESPONSE | "
                f"index={index}"
            )
            continue

        matched_track_ids.add(track["track_id"])

        found = bool(
            result.get("found")
        )

        data = result.get("result")

        backfill_status = result.get(
            "backfill_status"
        )

        # -----------------------------------------------------
        # Found
        # -----------------------------------------------------
        if found and isinstance(data, dict):
            resolved_isrc = (
                data.get("isrc")
                or track.get("isrc")
                or None
            )

            _save(
                conn,
                track["track_id"],
                resolved_isrc,
                data,
            )

            _set_status(
                conn,
                track["track_id"],
                "success",
            )

            if data.get("isrc"):
                conn.execute(
                    """
                    UPDATE tracks
                    SET isrc = ?
                    WHERE track_id = ?
                    """,
                    (
                        data["isrc"],
                        track["track_id"],
                    ),
                )

            stats["success"] += 1

            print(
                f"[FreqBlog BULK] OK | "
                f"{track.get('title')} | "
                f"BPM={data.get('bpm')} | "
                f"Key={data.get('key')}"
            )

            continue

        # -----------------------------------------------------
        # Still processing
        # -----------------------------------------------------
        if backfill_status in {
            "processing",
            "queued",
        }:
            _set_status(
                conn,
                track["track_id"],
                "queued",
                error=(
                    f"backfill_status="
                    f"{backfill_status}"
                ),
            )

            stats["processing"] += 1

            print(
                f"[FreqBlog BULK] QUEUED | "
                f"{track.get('title')} | "
                f"{backfill_status}"
            )

            continue

        # -----------------------------------------------------
        # Terminal miss
        # -----------------------------------------------------
        _set_status(
            conn,
            track["track_id"],
            "not_found",
            error=(
                result.get("detail")
                or "FreqBlog bulk not found"
            ),
        )

        stats["not_found"] += 1

        print(
            f"[FreqBlog BULK] NOT FOUND | "
            f"{track.get('title')}"
        )

    # Anything not echoed is treated as retryable rather than
    # terminal. This protects us from unexpected partial responses.
    for track in tracks:
        if track["track_id"] not in matched_track_ids:
            _set_status(
                conn,
                track["track_id"],
                "error",
                error="track missing from bulk response",
            )

            stats["retryable"] += 1

    conn.commit()

    print(
        "[FreqBlog BULK] SUMMARY | "
        f"success={stats['success']} | "
        f"processing={stats['processing']} | "
        f"not_found={stats['not_found']} | "
        f"retryable={stats['retryable']} | "
        f"error={stats['error']}"
    )

    return stats
