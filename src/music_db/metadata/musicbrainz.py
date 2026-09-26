from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests


MB_BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "spotify-music-db/0.1 (music metadata research)"


@dataclass
class MusicBrainzMatch:
    status: str
    score: float
    recording_id: Optional[str] = None
    release_id: Optional[str] = None
    release_group_id: Optional[str] = None

    recording_title: Optional[str] = None
    release_title: Optional[str] = None
    artist: Optional[str] = None

    recording_length: Optional[int] = None
    method: Optional[str] = None


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""

    value = value.lower().strip()

    # MusicBrainz / Spotify title variants
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\[[^\]]*\]", " ", value)

    # Everything / 걱정하지마 -> Everything
    value = re.split(r"\s*/\s*", value)[0]

    # feat / featuring / with
    value = re.sub(
        r"\b(feat|featuring|with)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    # punctuation
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)

    # whitespace
    value = re.sub(r"\s+", " ", value).strip()

    return value


def normalize_artist(value: Optional[str]) -> str:
    if not value:
        return ""

    value = value.lower().strip()

    value = re.sub(
        r"\b(feat|featuring|with)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def title_similarity(a: str, b: str) -> float:
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.90

    a_words = set(a.split())
    b_words = set(b.split())

    if not a_words or not b_words:
        return 0.0

    intersection = len(a_words & b_words)
    union = len(a_words | b_words)

    return intersection / union


def artist_similarity(a: str, b: str) -> float:
    a = normalize_artist(a)
    b = normalize_artist(b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        return 0.90

    return 0.0


def duration_similarity(
    spotify_ms: Optional[int],
    mb_ms: Optional[int],
) -> float:
    if not spotify_ms or not mb_ms:
        return 0.0

    diff = abs(spotify_ms - mb_ms)

    if diff <= 500:
        return 1.0
    if diff <= 1500:
        return 0.90
    if diff <= 3000:
        return 0.70
    if diff <= 5000:
        return 0.40
    if diff <= 10000:
        return 0.10

    return 0.0


class MusicBrainzResolver:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )

        self.last_request_time = 0.0

    def _get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:

        # MusicBrainz asks clients to keep request rate reasonable.
        elapsed = time.monotonic() - self.last_request_time

        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)

        url = f"{MB_BASE_URL}/{path.lstrip('/')}"

        max_retries = 4
        retry_statuses = {429, 500, 502, 503, 504}

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=30,
                )

                self.last_request_time = time.monotonic()

                if response.status_code in retry_statuses:
                    if attempt < max_retries:
                        wait = 2 ** (attempt - 1)
                        print(
                            f"MusicBrainz temporary error "
                            f"{response.status_code}; "
                            f"retry {attempt}/{max_retries - 1} "
                            f"in {wait}s..."
                        )
                        time.sleep(wait)
                        continue

                response.raise_for_status()

                return response.json()

            except requests.RequestException:
                self.last_request_time = time.monotonic()

                if attempt >= max_retries:
                    raise

                wait = 2 ** (attempt - 1)

                print(
                    f"MusicBrainz request error; "
                    f"retry {attempt}/{max_retries - 1} "
                    f"in {wait}s..."
                )

                time.sleep(wait)

        raise RuntimeError("MusicBrainz request failed")

    # ------------------------------------------------------------
    # 1. ISRC lookup
    # ------------------------------------------------------------

    def lookup_isrc(self, isrc: str) -> list[dict[str, Any]]:
        data = self._get(
            f"isrc/{isrc}",
            {
                "inc": "releases+release-groups+artist-credits",
                "fmt": "json",
            },
        )

        return data.get("recordings", [])

    # ------------------------------------------------------------
    # 2. Release search
    # ------------------------------------------------------------

    def search_releases(
        self,
        artist: str,
        album: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:

        query = (
            f'artist:"{artist}" '
            f'AND release:"{album}"'
        )

        data = self._get(
            "release/",
            {
                "query": query,
                "fmt": "json",
                "limit": limit,
            },
        )

        return data.get("releases", [])

    # ------------------------------------------------------------
    # 3. Release lookup + tracklist
    # ------------------------------------------------------------

    def lookup_release(
        self,
        release_id: str,
    ) -> dict[str, Any]:

        return self._get(
            f"release/{release_id}",
            {
                "fmt": "json",
                "inc": "recordings+artist-credits+release-groups",
            },
        )

    # ------------------------------------------------------------
    # 4. Find recording inside release
    # ------------------------------------------------------------

    def match_release_tracks(
        self,
        release: dict[str, Any],
        title: str,
        artist: str,
        duration_ms: Optional[int],
    ) -> list[MusicBrainzMatch]:

        candidates: list[MusicBrainzMatch] = []

        release_title = release.get("title")
        release_id = release.get("id")

        release_group = release.get("release-group") or {}
        release_group_id = release_group.get("id")

        for medium in release.get("media", []):

            for track in medium.get("tracks", []):

                recording = track.get("recording", {})

                recording_title = recording.get("title")
                recording_id = recording.get("id")
                recording_length = recording.get("length")

                artist_names = []

                for credit in recording.get(
                    "artist-credit",
                    [],
                ):
                    artist_obj = credit.get("artist", {})

                    name = artist_obj.get("name")

                    if name:
                        artist_names.append(name)

                recording_artist = " ".join(artist_names)

                t_score = title_similarity(
                    title,
                    recording_title or "",
                )

                a_score = artist_similarity(
                    artist,
                    recording_artist,
                )

                d_score = duration_similarity(
                    duration_ms,
                    recording_length,
                )

                # When a candidate comes from the release found by
                # Spotify artist + album search, title + duration can be
                # stronger evidence than the track-level artist string.
                # This is important for localized artist names such as
                # "The Black Skirts" vs "검정치마".
                if t_score >= 0.95 and d_score >= 0.95:
                    score = (
                        t_score * 0.50
                        + d_score * 0.35
                        + a_score * 0.15
                    )
                else:
                    score = (
                        t_score * 0.45
                        + a_score * 0.30
                        + d_score * 0.25
                    )

                if score <= 0:
                    continue

                candidates.append(
                    MusicBrainzMatch(
                        status="candidate",
                        score=score,
                        recording_id=recording_id,
                        release_id=release_id,
                        release_group_id=release_group_id,
                        recording_title=recording_title,
                        release_title=release_title,
                        artist=recording_artist,
                        recording_length=recording_length,
                        method="release_tracklist",
                    )
                )

        candidates.sort(
            key=lambda x: x.score,
            reverse=True,
        )

        return candidates

    # ------------------------------------------------------------
    # 5. Resolve via album/release
    # ------------------------------------------------------------

    def resolve_via_release(
        self,
        title: str,
        artist: str,
        album: str,
        duration_ms: Optional[int],
    ) -> MusicBrainzMatch:

        releases = self.search_releases(
            artist=artist,
            album=album,
        )

        if not releases:
            return MusicBrainzMatch(
                status="not_found",
                score=0.0,
                method="release_search",
            )

        all_candidates: list[MusicBrainzMatch] = []

        for release in releases:

            release_id = release.get("id")

            if not release_id:
                continue

            try:
                detail = self.lookup_release(
                    release_id
                )

                candidates = self.match_release_tracks(
                    detail,
                    title,
                    artist,
                    duration_ms,
                )

                all_candidates.extend(candidates)

            except requests.RequestException:
                continue

        if not all_candidates:
            return MusicBrainzMatch(
                status="not_found",
                score=0.0,
                method="release_tracklist",
            )

        best = all_candidates[0]

        if best.score >= 0.90:
            best.status = "matched"

        elif best.score >= 0.75:
            best.status = "matched"

        else:
            best.status = "ambiguous"

        return best

    # ------------------------------------------------------------
    # Main resolver
    # ------------------------------------------------------------

    def resolve(
        self,
        title: str,
        artist: str,
        album: str,
        duration_ms: Optional[int] = None,
        isrc: Optional[str] = None,
    ) -> MusicBrainzMatch:

        # --------------------------------------------------------
        # Phase 1: ISRC
        # --------------------------------------------------------

        if isrc:

            try:
                recordings = self.lookup_isrc(isrc)

                if recordings:

                    candidates = []

                    for recording in recordings:

                        recording_title = recording.get("title")
                        recording_length = recording.get("length")

                        artists = []

                        for credit in recording.get(
                            "artist-credit",
                            [],
                        ):
                            artist_obj = credit.get(
                                "artist",
                                {}
                            )

                            name = artist_obj.get("name")

                            if name:
                                artists.append(name)

                        recording_artist = " ".join(artists)

                        t_score = title_similarity(
                            title,
                            recording_title or "",
                        )

                        a_score = artist_similarity(
                            artist,
                            recording_artist,
                        )

                        d_score = duration_similarity(
                            duration_ms,
                            recording_length,
                        )

                        score = (
                            t_score * 0.45
                            + a_score * 0.30
                            + d_score * 0.25
                        )

                        candidates.append(
                            MusicBrainzMatch(
                                status="candidate",
                                score=score,
                                recording_id=recording.get("id"),
                                recording_title=recording_title,
                                artist=recording_artist,
                                recording_length=recording_length,
                                method="isrc",
                            )
                        )

                    candidates.sort(
                        key=lambda x: x.score,
                        reverse=True,
                    )

                    if candidates:

                        best = candidates[0]

                        if best.score >= 0.90:
                            best.status = "matched"
                            return best

            except requests.RequestException:
                pass

        # --------------------------------------------------------
        # Phase 2: Artist + Album → Release → Tracklist
        # --------------------------------------------------------

        return self.resolve_via_release(
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
        )
