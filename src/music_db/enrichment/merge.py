from __future__ import annotations

import json
import sqlite3


# Source precedence:
#
#   FreqBlog
#       ↓
#   SongBPM
#
# FreqBlog is the canonical audio-feature source whenever
# it has a value. SongBPM is a fallback only.


FIELD_ORDER = (
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


def _source_row(conn, track_id, source):
    return conn.execute(
        """
        SELECT
            tempo,
            key,
            mode,
            loudness,
            energy,
            danceability,
            valence,
            acousticness,
            instrumentalness,
            speechiness,
            confidence
        FROM audio_feature_sources
        WHERE track_id = ?
          AND source = ?
        LIMIT 1
        """,
        (track_id, source),
    ).fetchone()


def get_preferred_audio_features(conn, track_id):
    freqblog = _source_row(conn, track_id, "freqblog")
    songbpm = _source_row(conn, track_id, "songbpm")

    rows = {
        source: _source_row(conn, track_id, source)
        for (source,) in conn.execute(
            "SELECT DISTINCT source FROM audio_feature_sources WHERE track_id = ?",
            (track_id,),
        ).fetchall()
    }
    rows = {source: row for source, row in rows.items() if row is not None}
    if not rows:
        return None

    priority_table = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'source_priority'
        """
    ).fetchone()
    priorities = {}
    if priority_table:
        priorities = {
            (field, source): priority
            for field, source, priority in conn.execute(
                "SELECT field_name, source, priority FROM source_priority"
            ).fetchall()
        }

    result = {}
    source_by_field = {}

    for field in FIELD_ORDER:
        available = [
            source for source, row in rows.items() if row[field] is not None
        ]
        source = (
            min(
                available,
                key=lambda candidate: (
                    priorities.get(
                        (field, candidate),
                        10 if candidate == "freqblog" else 20,
                    ),
                    candidate,
                ),
            )
            if available
            else None
        )
        value = rows[source][field] if source else None
        result[field] = value
        source_by_field[field] = source

    if freqblog is not None:
        result["primary_source"] = "freqblog"
    elif songbpm is not None:
        result["primary_source"] = "songbpm"
    else:
        result["primary_source"] = next(iter(rows))
    result["source_by_field"] = source_by_field

    return result


def refresh_canonical_audio_features(conn, track_id):
    """Materialize FreqBlog-first audio features into the canonical table."""
    features = get_preferred_audio_features(conn, track_id)
    if features is None or not any(features[field] is not None for field in FIELD_ORDER):
        return False

    duration = conn.execute(
        "SELECT duration_ms FROM tracks WHERE track_id = ?", (track_id,)
    ).fetchone()
    if duration is None:
        return False
    source_rows = {
        source: _source_row(conn, track_id, source)
        for source in set(features["source_by_field"].values()) - {None}
    }
    distinct_sources = set(features["source_by_field"].values()) - {None}
    confidence_source = (
        features["source_by_field"].get("tempo")
        or next(iter(distinct_sources), None)
    )
    confidence = (
        source_rows.get(confidence_source)["confidence"]
        if source_rows.get(confidence_source) is not None
        else None
    )
    source = (
        next(iter(distinct_sources))
        if len(distinct_sources) == 1
        else "mixed"
    )
    values = [features[field] for field in FIELD_ORDER]
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(audio_features)")
    }
    source_by_field = json.dumps(features["source_by_field"], sort_keys=True)

    if "source_by_field" in columns:
        conn.execute(
            """
            INSERT INTO audio_features (
                track_id, duration_ms, tempo, key, mode, loudness,
                energy, danceability, valence, acousticness, instrumentalness,
                speechiness, source, confidence, source_by_field, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(track_id) DO UPDATE SET
                duration_ms=excluded.duration_ms, tempo=excluded.tempo,
                key=excluded.key, mode=excluded.mode, loudness=excluded.loudness,
                energy=excluded.energy, danceability=excluded.danceability,
                valence=excluded.valence, acousticness=excluded.acousticness,
                instrumentalness=excluded.instrumentalness,
                speechiness=excluded.speechiness, source=excluded.source,
                confidence=excluded.confidence,
                source_by_field=excluded.source_by_field,
                updated_at=excluded.updated_at
            """,
            (track_id, duration[0], *values, source, confidence, source_by_field),
        )
    else:
        conn.execute(
            """
            INSERT INTO audio_features (
                track_id, duration_ms, tempo, key, mode, loudness,
                energy, danceability, valence, acousticness,
                instrumentalness, speechiness, source, confidence, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(track_id) DO UPDATE SET
                duration_ms=excluded.duration_ms, tempo=excluded.tempo,
                key=excluded.key, mode=excluded.mode, loudness=excluded.loudness,
                energy=excluded.energy, danceability=excluded.danceability,
                valence=excluded.valence, acousticness=excluded.acousticness,
                instrumentalness=excluded.instrumentalness,
                speechiness=excluded.speechiness, source=excluded.source,
                confidence=excluded.confidence, updated_at=excluded.updated_at
            """,
            (track_id, duration[0], *values, source, confidence),
        )
    return True
