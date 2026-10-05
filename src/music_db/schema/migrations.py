from __future__ import annotations

from datetime import datetime, timezone


AUDIO_FEATURE_FIELDS = (
    "tempo",
    "key",
    "mode",
    "loudness",
    "energy",
    "danceability",
    "valence",
    "acousticness",
    "instrumentalness",
    "speechiness",
)


def _ensure_column(conn, table, column, definition):
    columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _normalize_songbpm_status_entity_type(conn):
    rows = conn.execute(
        """
        SELECT track_id, status, attempts, last_error, last_attempted_at,
               completed_at, created_at, updated_at, error_details
        FROM enrichment_status
        WHERE source = 'songbpm' AND entity_type = 'track'
        """
    ).fetchall()

    for row in rows:
        existing = conn.execute(
            """
            SELECT updated_at
            FROM enrichment_status
            WHERE track_id = ? AND source = 'songbpm'
              AND entity_type = 'audio_features'
            """,
            (row[0],),
        ).fetchone()
        if not existing:
            conn.execute(
                """
                INSERT INTO enrichment_status (
                    track_id, source, entity_type, status, attempts, last_error,
                    last_attempted_at, completed_at, created_at, updated_at,
                    error_details
                )
                VALUES (?, 'songbpm', 'audio_features', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row[0], *row[1:]),
            )
        elif (row[7] or "") > (existing[0] or ""):
            conn.execute(
                """
                UPDATE enrichment_status
                SET status = ?, attempts = ?, last_error = ?,
                    last_attempted_at = ?, completed_at = ?, created_at = ?,
                    updated_at = ?, error_details = ?
                WHERE track_id = ? AND source = 'songbpm'
                  AND entity_type = 'audio_features'
                """,
                (*row[1:], row[0]),
            )
        conn.execute(
            """
            DELETE FROM enrichment_status
            WHERE track_id = ? AND source = 'songbpm' AND entity_type = 'track'
            """,
            (row[0],),
        )


def apply_migrations(conn):
    _ensure_column(conn, "enrichment_status", "error_details", "TEXT")
    audio_features_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audio_features'"
    ).fetchone()
    if audio_features_exists:
        _ensure_column(conn, "audio_features", "source_by_field", "TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_priority (
            field_name TEXT NOT NULL,
            source TEXT NOT NULL,
            priority INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (field_name, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS harmony_provider_runs (
            execution_id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            source_url TEXT,
            matched_video_url TEXT,
            submitted_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            raw_result_available INTEGER NOT NULL DEFAULT 0,
            normalized_segment_count INTEGER NOT NULL DEFAULT 0,
            error_type TEXT,
            error_message TEXT,
            parser_version TEXT,
            confidence REAL,
            raw_result_json TEXT,
            FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_harmony_provider_runs_latest
        ON harmony_provider_runs(track_id, provider, execution_id)
        """
    )

    now = datetime.now(timezone.utc).isoformat()
    for field in AUDIO_FEATURE_FIELDS:
        conn.execute(
            """
            INSERT OR IGNORE INTO source_priority
                (field_name, source, priority, updated_at)
            VALUES (?, 'freqblog', 10, ?), (?, 'songbpm', 20, ?)
            """,
            (field, now, field, now),
        )

    _normalize_songbpm_status_entity_type(conn)
