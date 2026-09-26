from .database import get_connection


BASE_SELECT = """
    SELECT
        t.track_id,
        t.title,
        t.album,
        t.release_date,
        t.spotify_url,
        t.saved,
        t.saved_at,
        t.last_played,
        t.top_short_term,
        t.top_medium_term,
        t.top_long_term,
        af.tempo,
        af.key,
        af.mode,
        af.energy,
        af.danceability,
        af.valence,
        af.acousticness,
        af.instrumentalness,
        af.speechiness,
        GROUP_CONCAT(DISTINCT a.name) AS artists
    FROM tracks t
    LEFT JOIN track_artists ta
        ON t.track_id = ta.track_id
    LEFT JOIN artists a
        ON ta.artist_id = a.artist_id
    LEFT JOIN audio_features af
        ON t.track_id = af.track_id
"""


def search_tracks(keyword):
    keyword = f"%{keyword}%"

    with get_connection() as conn:
        rows = conn.execute(
            BASE_SELECT
            + """
            WHERE
                t.title LIKE ?
                OR t.album LIKE ?
                OR a.name LIKE ?
            GROUP BY t.track_id
            ORDER BY t.last_played DESC
            """,
            (keyword, keyword, keyword),
        ).fetchall()

    return rows


def search_by_artist(artist):
    artist = f"%{artist}%"

    with get_connection() as conn:
        rows = conn.execute(
            BASE_SELECT
            + """
            WHERE a.name LIKE ?
            GROUP BY t.track_id
            ORDER BY t.release_date DESC
            """,
            (artist,),
        ).fetchall()

    return rows


def recent_tracks(limit=20):
    with get_connection() as conn:
        rows = conn.execute(
            BASE_SELECT
            + """
            WHERE t.last_played IS NOT NULL
            GROUP BY t.track_id
            ORDER BY t.last_played DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return rows


def saved_tracks():
    with get_connection() as conn:
        rows = conn.execute(
            BASE_SELECT
            + """
            WHERE t.saved = 1
            GROUP BY t.track_id
            ORDER BY t.saved_at DESC
            """
        ).fetchall()

    return rows


def top_tracks(period="long"):
    column_map = {
        "short": "top_short_term",
        "medium": "top_medium_term",
        "long": "top_long_term",
    }

    if period not in column_map:
        raise ValueError("period must be short, medium, or long")

    column = column_map[period]

    with get_connection() as conn:
        rows = conn.execute(
            BASE_SELECT
            + f"""
            WHERE t.{column} = 1
            GROUP BY t.track_id
            ORDER BY t.release_date DESC
            """
        ).fetchall()

    return rows


def advanced_search(
    keyword=None,
    artist=None,
    min_bpm=None,
    max_bpm=None,
    min_energy=None,
    max_energy=None,
    min_danceability=None,
    max_danceability=None,
    genre=None,
    tag=None,
):
    conditions = []
    params = []

    if keyword:
        conditions.append(
            """
            (
                t.title LIKE ?
                OR t.album LIKE ?
                OR a.name LIKE ?
            )
            """
        )

        value = f"%{keyword}%"
        params.extend([value, value, value])

    if artist:
        conditions.append("a.name LIKE ?")
        params.append(f"%{artist}%")

    if min_bpm is not None:
        conditions.append("af.tempo >= ?")
        params.append(min_bpm)

    if max_bpm is not None:
        conditions.append("af.tempo <= ?")
        params.append(max_bpm)

    if min_energy is not None:
        conditions.append("af.energy >= ?")
        params.append(min_energy)

    if max_energy is not None:
        conditions.append("af.energy <= ?")
        params.append(max_energy)

    if min_danceability is not None:
        conditions.append("af.danceability >= ?")
        params.append(min_danceability)

    if max_danceability is not None:
        conditions.append("af.danceability <= ?")
        params.append(max_danceability)

    if genre:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM track_genres tg
                JOIN genres g
                    ON tg.genre_id = g.genre_id
                WHERE tg.track_id = t.track_id
                AND g.name LIKE ?
            )
            """
        )

        params.append(f"%{genre}%")

    if tag:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM track_tags tt
                JOIN tags tg
                    ON tt.tag_id = tg.tag_id
                WHERE tt.track_id = t.track_id
                AND tg.name LIKE ?
            )
            """
        )

        params.append(f"%{tag}%")

    where_clause = ""

    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    query = (
        BASE_SELECT
        + f"""
        {where_clause}
        GROUP BY t.track_id
        ORDER BY t.last_played DESC
        """
    )

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    return rows
