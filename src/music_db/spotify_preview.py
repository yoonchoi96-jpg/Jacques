from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone


AUDIO_FIELDS = (
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


def collect_spotify_tracks(spotify):
    tracks = OrderedDict()

    def add(track):
        if track and track.get("id"):
            tracks[track["id"]] = track

    page = spotify.current_user_saved_tracks(limit=50)
    while page:
        for item in page.get("items", []):
            add(item.get("track"))
        if not page.get("next"):
            break
        page = spotify.next(page)

    for item in spotify.current_user_recently_played(limit=50).get("items", []):
        add(item.get("track"))

    for term in ("short_term", "medium_term", "long_term"):
        for track in spotify.current_user_top_tracks(
            limit=50, time_range=term
        ).get("items", []):
            add(track)

    return list(tracks.values())


def preview_enrichment(conn, tracks):
    candidates = []
    for track in tracks:
        track_id = track["id"]
        statuses = {
            row["source"]: row["status"]
            for row in conn.execute(
                """
                SELECT source, status
                FROM enrichment_status
                WHERE track_id = ?
                  AND (
                      entity_type = 'audio_features'
                      OR (source = 'songbpm' AND entity_type = 'track')
                  )
                  AND source IN ('freqblog', 'songbpm')
                """,
                (track_id,),
            ).fetchall()
        }
        freq_error = conn.execute(
            """
            SELECT last_attempted_at FROM enrichment_status
            WHERE track_id = ? AND source = 'freqblog'
              AND entity_type = 'audio_features' AND status = 'error'
            """,
            (track_id,),
        ).fetchone()
        if freq_error and freq_error[0]:
            try:
                attempted = datetime.fromisoformat(freq_error[0])
                if attempted.tzinfo is None:
                    attempted = attempted.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - attempted).total_seconds() < 6 * 60 * 60:
                    continue
            except (TypeError, ValueError):
                pass
        if (
            statuses.get("freqblog") == "success"
            or statuses.get("songbpm") == "success"
        ):
            continue
        if (
            statuses.get("freqblog") == "not_found"
            and statuses.get("songbpm") == "no_data"
        ):
            continue

        row = conn.execute(
            """
            SELECT tempo, key, mode, loudness, energy, danceability, valence,
                   acousticness, instrumentalness, speechiness
            FROM audio_features
            WHERE track_id = ?
            """,
            (track_id,),
        ).fetchone()
        missing = [
            field for index, field in enumerate(AUDIO_FIELDS)
            if row is None or row[index] is None
        ]
        candidates.append(
            {
                "track_id": track_id,
                "title": track.get("name", ""),
                "artists": ", ".join(
                    artist.get("name", "") for artist in track.get("artists", [])
                ),
                "missing_fields": missing,
            }
        )

    return candidates


def print_dry_run_report(tracks, candidates):
    print(f"Spotify tracks collected: {len(tracks)}")
    print(f"Enrichment candidates: {len(candidates)}")
    print("External enrichment API calls: 0")
    for candidate in candidates:
        missing = ", ".join(candidate["missing_fields"]) or "none"
        print(
            f"  {candidate['title']} — {candidate['artists']} "
            f"| missing fields: {missing}"
        )
