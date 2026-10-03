from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import get_connection, initialize_database


def _norm_text(value: str) -> str:
    value = str(value or "").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _token_overlap(a: str, b: str) -> float:
    left = set(_norm_text(a).split())
    right = set(_norm_text(b).split())
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _youtube_candidate_score(
    title: str,
    artists: list[str],
    duration_ms: int | None,
    entry: dict,
) -> float:
    candidate_title = entry.get("title") or ""
    uploader = entry.get("channel") or entry.get("uploader") or ""
    artist_text = " ".join(artists)

    title_ratio = difflib.SequenceMatcher(
        None, _norm_text(title), _norm_text(candidate_title)
    ).ratio()
    title_overlap = _token_overlap(title, candidate_title)
    artist_overlap = _token_overlap(artist_text, f"{candidate_title} {uploader}")

    score = 0.50 * title_ratio + 0.25 * title_overlap + 0.20 * artist_overlap

    lower = _norm_text(candidate_title + " " + uploader)
    if any(token in lower.split() for token in ("official", "vevo", "topic")):
        score += 0.06

    unwanted = (
        ("live" in lower or "concert" in lower) and
        "live" not in _norm_text(title)
    ) or (
        "cover" in lower and "cover" not in _norm_text(title)
    ) or (
        "karaoke" in lower and "karaoke" not in _norm_text(title)
    ) or (
        "remix" in lower and "remix" not in _norm_text(title)
    )
    if unwanted:
        score -= 0.15

    candidate_duration = entry.get("duration")
    if duration_ms and candidate_duration:
        diff_seconds = abs(float(candidate_duration) - duration_ms / 1000.0)
        if diff_seconds <= 5:
            score += 0.10
        elif diff_seconds <= 15:
            score += 0.06
        elif diff_seconds <= 30:
            score += 0.02
        else:
            score -= min(0.12, diff_seconds / 300.0)

    return max(0.0, min(1.0, score))


def youtube_search(title: str, artists: list[str], duration_ms: int | None) -> dict:
    query = f"{title} {' '.join(artists)} official audio"
    proc = subprocess.run(
        [
            "yt-dlp",
            "--flat-playlist",
            "--skip-download",
            "--no-warnings",
            "--playlist-end",
            "8",
            "--dump-single-json",
            f"ytsearch8:{query}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    entries = [x for x in (payload.get("entries") or []) if x]
    if not entries:
        raise RuntimeError(f"No YouTube result for: {query}")

    ranked = sorted(
        (
            {
                "score": _youtube_candidate_score(title, artists, duration_ms, entry),
                "id": entry.get("id"),
                "title": entry.get("title"),
                "channel": entry.get("channel") or entry.get("uploader"),
                "duration": entry.get("duration"),
                "url": (
                    entry.get("webpage_url")
                    or entry.get("original_url")
                    or (
                        f"https://www.youtube.com/watch?v={entry.get('id')}"
                        if entry.get("id")
                        else None
                    )
                ),
            }
            for entry in entries
            if entry.get("id")
        ),
        key=lambda x: x["score"],
        reverse=True,
    )
    best = ranked[0]
    if not best["url"]:
        raise RuntimeError(f"Best YouTube result has no URL: {best}")

    print(
        "YouTube candidates: "
        + "; ".join(
            f'{x["score"]:.2f} {x["title"] or ""} [{x["channel"] or ""}]'
            for x in ranked[:5]
        )
    )
    return best


def register_streaming_link(
    track_id: str,
    provider: str,
    url: str,
    *,
    primary: bool,
    metadata: dict | None = None,
) -> None:
    source = "spotify_stream" if provider == "spotify" else "youtube_link"
    initialize_database()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO track_media_links
               (track_id, provider, url, media_type, is_primary, status,
                metadata_json, last_checked_at, updated_at)
               VALUES (?, ?, ?, 'track', ?, 'active', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(track_id, provider, url) DO UPDATE SET
                 is_primary=excluded.is_primary,
                 status='active',
                 metadata_json=excluded.metadata_json,
                 last_checked_at=CURRENT_TIMESTAMP,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                track_id,
                provider,
                url,
                1 if primary else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        exists = conn.execute(
            """SELECT 1
               FROM music_analysis_evidence
               WHERE track_id=? AND domain='media_link' AND source=?
                 AND json_extract(payload_json, '$.url')=?
               LIMIT 1""",
            (track_id, source, url),
        ).fetchone()
        if not exists:
            conn.execute(
                """INSERT INTO music_analysis_evidence
                   (track_id, domain, source, source_type, method, payload_json,
                    confidence, observed_at, created_at)
                   VALUES (?, 'media_link', ?, 'web', 'link_registration', ?,
                           1.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (
                    track_id,
                    source,
                    json.dumps(
                        {
                            "provider": provider,
                            "url": url,
                            "streaming_only": True,
                            "audio_downloaded": False,
                            "metadata": metadata or {},
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        conn.commit()


def main() -> int:
    top_n = int(os.environ.get("JACQUES_TOP_N", "3"))
    providers = os.environ.get("JACQUES_PROVIDERS", "chordidentifier,magic_chords")

    oauth = SpotifyOAuth(
        client_id=os.environ["SPOTIFY_CLIENT_ID"],
        client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
        redirect_uri="http://127.0.0.1:8888/callback",
        scope="user-top-read",
    )
    token_info = oauth.refresh_access_token(os.environ["SPOTIFY_REFRESH_TOKEN"])
    sp = spotipy.Spotify(auth=token_info["access_token"])
    items = sp.current_user_top_tracks(limit=top_n, time_range="long_term").get("items", [])

    print("\n" + "=" * 80)
    print(f"JACQUES REAL TRACK ANALYSIS — TOP {len(items)}")
    print("=" * 80)

    for rank, track in enumerate(items, start=1):
        track_id = track["id"]
        title = track["name"]
        artists = [a["name"] for a in track["artists"]]
        spotify_url = track["external_urls"]["spotify"]
        duration_ms = track.get("duration_ms")

        print(f"\n[{rank}] {title} — {', '.join(artists)}")
        print(f"Spotify: {spotify_url}")
        register_streaming_link(
            track_id,
            "spotify",
            spotify_url,
            primary=True,
            metadata={"title": title, "artists": artists, "duration_ms": duration_ms},
        )

        try:
            candidate = youtube_search(title, artists, duration_ms)
            youtube_url = candidate["url"]
        except Exception as exc:
            print(f"YouTube resolution FAILED: {type(exc).__name__}: {exc}")
            continue

        print(f"YouTube: {youtube_url}")
        print(f"YouTube match score: {candidate['score']:.3f}")
        register_streaming_link(
            track_id,
            "youtube",
            youtube_url,
            primary=False,
            metadata={
                "title": title,
                "artists": artists,
                "duration_ms": duration_ms,
                "match_score": candidate["score"],
                "candidate_title": candidate["title"],
                "candidate_channel": candidate["channel"],
                "candidate_duration": candidate["duration"],
            },
        )
        print(f"Providers: {providers}")

        env = os.environ.copy()
        env["YOUTUBE_URL"] = youtube_url
        env["TRACK_ID"] = track_id
        env["PROVIDERS"] = providers
        env.setdefault("PROVIDER_RETRIES", "2")

        subprocess.run(
            [sys.executable, "tools/youtube_provider_probe.py"],
            cwd=ROOT,
            env=env,
            check=False,
        )
        print(f"Completed provider probe for: {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
