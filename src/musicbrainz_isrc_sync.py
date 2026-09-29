from __future__ import annotations

from music_db.metadata.musicbrainz import (
    MusicBrainzResolver,
    artist_similarity,
    duration_similarity,
    title_similarity,
)

_RESOLVER = None


def _resolver():
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = MusicBrainzResolver()
    return _RESOLVER


def search_musicbrainz(isrc):
    return {"recordings": _resolver().lookup_isrc(isrc)}


def _artist_name(recording):
    credits = recording.get("artist-credit") or []
    names = []
    for credit in credits:
        artist = credit.get("artist") or {}
        name = artist.get("name")
        if name:
            names.append(name)
    return " & ".join(names)


def choose_candidate(row, recordings):
    best = None
    best_score = 0.0
    second = 0.0
    for recording in recordings or []:
        title = recording.get("title") or ""
        artist = _artist_name(recording)
        duration = recording.get("length")
        score = (
            0.55 * title_similarity(row["title"], title)
            + 0.35 * artist_similarity(row["artist"], artist)
            + 0.10 * duration_similarity(row["duration_ms"], duration)
        )
        if score > best_score:
            second = best_score
            best_score = score
            best = recording
        elif score > second:
            second = score

    if best is None or best_score < 0.55:
        return None, "not_found", best_score

    status = "matched"
    if best_score < 0.82 or (best_score - second) < 0.05:
        status = "ambiguous"

    return best, status, best_score


def choose_release_id(album, recording):
    releases = recording.get("releases") or []
    if not releases:
        return None
    album = (album or "").strip().lower()
    if album:
        for release in releases:
            if (release.get("title") or "").strip().lower() == album:
                return release.get("id")
    return releases[0].get("id")
