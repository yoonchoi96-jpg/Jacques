from __future__ import annotations

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
            speechiness
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

    if not freqblog and not songbpm:
        return None

    result = {}

    for field in FIELD_ORDER:
        value = None

        if freqblog is not None:
            value = freqblog[field]

        if value is None and songbpm is not None:
            value = songbpm[field]

        result[field] = value

    if freqblog is not None:
        result["primary_source"] = "freqblog"
    else:
        result["primary_source"] = "songbpm"

    return result


def refresh_canonical_audio_features(conn, track_id):
    """Materialize FreqBlog-first audio features into the canonical table."""
    row = conn.execute(
        """
        SELECT
            t.duration_ms,
            f.tempo AS f_tempo, s.tempo AS s_tempo,
            f.key AS f_key, s.key AS s_key,
            f.mode AS f_mode, s.mode AS s_mode,
            f.loudness AS f_loudness, s.loudness AS s_loudness,
            f.energy AS f_energy, s.energy AS s_energy,
            f.danceability AS f_danceability, s.danceability AS s_danceability,
            f.valence AS f_valence, s.valence AS s_valence,
            f.acousticness AS f_acousticness, s.acousticness AS s_acousticness,
            f.instrumentalness AS f_instrumentalness, s.instrumentalness AS s_instrumentalness,
            f.speechiness AS f_speechiness, s.speechiness AS s_speechiness,
            f.confidence AS f_confidence, s.confidence AS s_confidence,
            CASE WHEN f.track_id IS NOT NULL THEN 'freqblog' ELSE 'songbpm' END AS source
        FROM tracks t
        LEFT JOIN audio_feature_sources f
          ON f.track_id = t.track_id AND f.source = 'freqblog'
        LEFT JOIN audio_feature_sources s
          ON s.track_id = t.track_id AND s.source = 'songbpm'
        WHERE t.track_id = ?
        """,
        (track_id,),
    ).fetchone()

    if row is None or (row["f_tempo"] is None and row["s_tempo"] is None):
        return False

    def first(field):
        return row[f"f_{field}"] if row[f"f_{field}"] is not None else row[f"s_{field}"]

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
        (
            track_id, row["duration_ms"], first("tempo"), first("key"),
            first("mode"), first("loudness"), first("energy"),
            first("danceability"), first("valence"), first("acousticness"),
            first("instrumentalness"), first("speechiness"), row["source"],
            row["f_confidence"] if row["f_confidence"] is not None else row["s_confidence"],
        ),
    )
    return True
