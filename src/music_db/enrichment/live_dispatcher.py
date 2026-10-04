from __future__ import annotations

import sqlite3
import threading
import time


from music_db.database import initialize_database
from music_db.enrichment.sources import freqblog
from music_db.enrichment.sources import songbpm


DB_PATH = "db/music.db"

BATCH_SIZE = 15
COALESCE_SECONDS = 0.75
RETRY_COOLDOWN_SECONDS = 6 * 60 * 60


def _source_enabled(conn, source):
    row = conn.execute(
        "SELECT enabled FROM source_registry WHERE source=?",
        (source,),
    ).fetchone()
    return bool(row and row[0])


def _open_connection():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA busy_timeout=30000"
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    return conn


class LiveEnrichmentDispatcher:

    def __init__(self, dry_run=False):
        self.dry_run = dry_run

        self.pending = []

        self.lock = threading.Lock()

        self.timer = None

        initialize_database()

    # ---------------------------------------------------------
    # PUBLIC
    # ---------------------------------------------------------

    def enqueue(self, track):
        if not track:
            return

        if not track.get("track_id"):
            return

        if not self._needs_enrichment(track["track_id"]):
            return

        self._persist_track(track)

        with self.lock:

            # Avoid duplicate pending entries.
            existing_ids = {
                x["track_id"]
                for x in self.pending
            }

            if track["track_id"] in existing_ids:
                return

            self.pending.append(
                dict(track)
            )

            # Hard flush.
            if len(self.pending) >= BATCH_SIZE:
                batch = self.pending[:BATCH_SIZE]
                del self.pending[:BATCH_SIZE]

                self._cancel_timer()

                self._dispatch(batch)

                return

            # Start coalescing timer.
            if self.timer is None:

                self.timer = threading.Timer(
                    COALESCE_SECONDS,
                    self._timer_flush,
                )

                self.timer.daemon = True

                self.timer.start()

    def flush(self):

        with self.lock:

            self._cancel_timer()

            batch = list(
                self.pending
            )

            self.pending.clear()

        if batch:
            self._dispatch(batch)

    def close(self):

        self.flush()

    # ---------------------------------------------------------
    # TIMER
    # ---------------------------------------------------------

    def _timer_flush(self):

        with self.lock:

            self.timer = None

            if not self.pending:
                return

            batch = list(
                self.pending
            )

            self.pending.clear()

        self._dispatch(batch)

    def _cancel_timer(self):

        if self.timer is not None:

            self.timer.cancel()

            self.timer = None

    # ---------------------------------------------------------
    # DISPATCH
    # ---------------------------------------------------------

    def _dispatch(self, tracks):

        if not tracks:
            return

        # Split defensively.
        for start in range(
            0,
            len(tracks),
            BATCH_SIZE,
        ):

            batch = tracks[
                start:start + BATCH_SIZE
            ]

            self._process_batch(
                batch
            )

    def _process_batch(self, tracks):

        print(
            "\n"
            "[LIVE DISPATCH] "
            f"BATCH={len(tracks)}"
        )

        for track in tracks:

            print(
                "  - "
                f"{track.get('title')} | "
                f"{track.get('isrc') or 'NO ISRC'}"
            )

        conn = _open_connection()

        try:

            # -------------------------------------------------
            # FREQBLOG PRIMARY
            # -------------------------------------------------

            if _source_enabled(conn, "freqblog"):
                result = freqblog.enrich_bulk(
                    conn,
                    tracks,
                    dry_run=self.dry_run,
                )
            else:
                print("[LIVE DISPATCH] FreqBlog disabled in source registry")
                result = {"disabled": len(tracks)}

            # -------------------------------------------------
            # SONG B P M FALLBACK
            #
            # Only terminal FreqBlog misses.
            # Queued/error tracks stay in FreqBlog recovery.
            # -------------------------------------------------

            if self.dry_run:
                return

            fallback = []

            for track in tracks:

                status = conn.execute(
                    """
                    SELECT status
                    FROM enrichment_status
                    WHERE track_id = ?
                      AND source = 'freqblog'
                      AND entity_type = 'audio_features'
                    """,
                    (
                        track["track_id"],
                    ),
                ).fetchone()

                if not status:
                    continue

                if status["status"] == "not_found":
                    fallback.append(track)

            if fallback and _source_enabled(conn, "songbpm"):

                print(
                    "[LIVE SONGBPM] "
                    f"FALLBACK={len(fallback)}"
                )

                for track in fallback:

                    try:

                        songbpm.enrich_one(
                            conn,
                            track,
                            dry_run=False,
                        )

                    except AttributeError:

                        print(
                            "[LIVE SONGBPM] "
                            "enrich_one() missing | "
                            f"{track.get('title')}"
                        )

                    except Exception as exc:

                        print(
                            "[LIVE SONGBPM] "
                            f"ERROR | "
                            f"{track.get('title')} | "
                            f"{type(exc).__name__}: {exc}"
                        )

            conn.commit()

        except Exception as exc:

            print(
                "[LIVE DISPATCH] ERROR | "
                f"{type(exc).__name__}: {exc}"
            )

        finally:

            conn.close()

    def _needs_enrichment(self, track_id):
        """Avoid re-querying already-enriched tracks every Spotify run."""
        conn = _open_connection()
        try:
            freq = conn.execute(
                """
                SELECT status, last_attempted_at
                FROM enrichment_status
                WHERE track_id = ?
                  AND source = 'freqblog'
                  AND entity_type = 'audio_features'
                """,
                (track_id,),
            ).fetchone()

            song = conn.execute(
                """
                SELECT status
                FROM enrichment_status
                WHERE track_id = ?
                  AND source = 'songbpm'
                  AND entity_type = 'audio_features'
                """,
                (track_id,),
            ).fetchone()

            if freq and freq["status"] == "success":
                return False

            if song and song["status"] == "success":
                return False

            if (
                freq
                and freq["status"] == "not_found"
                and song
                and song["status"] in ("success", "no_data")
            ):
                return False

            if freq and freq["status"] == "error" and freq["last_attempted_at"]:
                try:
                    from datetime import datetime, timezone
                    attempted = datetime.fromisoformat(freq["last_attempted_at"])
                    age = (datetime.now(timezone.utc) - attempted).total_seconds()
                    if age < RETRY_COOLDOWN_SECONDS:
                        return False
                except ValueError:
                    pass

            return True
        finally:
            conn.close()

    # ---------------------------------------------------------
    # SQLITE
    # ---------------------------------------------------------

    def _persist_track(self, track):

        conn = _open_connection()

        try:

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
                    top_long_term,
                    isrc,
                    duration_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(track_id)
                DO UPDATE SET

                    title =
                        excluded.title,

                    album =
                        excluded.album,

                    release_date =
                        excluded.release_date,

                    spotify_url =
                        excluded.spotify_url,

                    -- Live enrichment receives partial track payloads.
                    -- Never let a later non-saved occurrence erase an
                    -- already saved track or its listening/top metadata.
                    saved =
                        MAX(tracks.saved, excluded.saved),

                    saved_at =
                        COALESCE(
                            tracks.saved_at,
                            excluded.saved_at
                        ),

                    last_played =
                        tracks.last_played,

                    top_short_term =
                        tracks.top_short_term,

                    top_medium_term =
                        tracks.top_medium_term,

                    top_long_term =
                        tracks.top_long_term,

                    isrc =
                        COALESCE(
                            excluded.isrc,
                            tracks.isrc
                        ),

                    duration_ms =
                        COALESCE(
                            excluded.duration_ms,
                            tracks.duration_ms
                        )
                """,
                (
                    track["track_id"],
                    track.get("title"),
                    track.get("album"),
                    track.get("release_date"),
                    track.get("spotify_url"),
                    int(
                        bool(
                            track.get("saved")
                        )
                    ),
                    track.get("saved_at"),
                    track.get("last_played"),
                    int(
                        bool(
                            track.get(
                                "top_short_term"
                            )
                        )
                    ),
                    int(
                        bool(
                            track.get(
                                "top_medium_term"
                            )
                        )
                    ),
                    int(
                        bool(
                            track.get(
                                "top_long_term"
                            )
                        )
                    ),
                    track.get("isrc"),
                    track.get("duration_ms"),
                ),
            )

            conn.commit()

        finally:

            conn.close()
