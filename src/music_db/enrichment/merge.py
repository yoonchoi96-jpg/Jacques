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
