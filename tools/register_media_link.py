from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection


PROVIDERS = {
    "spotify": ("spotify.com", "spotify_stream"),
    "youtube": ("youtube.com", "youtube_link"),
    "youtube_music": ("music.youtube.com", "youtube_link"),
}


def normalize_provider(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "open.spotify.com":
        return "spotify", "spotify_stream"
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}:
        return "youtube", "youtube_link"
    raise ValueError("Only Spotify or YouTube URLs are accepted")


def register(track_id: str, url: str, primary: bool = False) -> dict:
    provider, source = normalize_provider(url)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO track_media_links
               (track_id, provider, url, media_type, is_primary, status,
                last_checked_at, updated_at)
               VALUES (?, ?, ?, 'track', ?, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(track_id, provider, url) DO UPDATE SET
                 is_primary=excluded.is_primary,
                 status='active',
                 last_checked_at=CURRENT_TIMESTAMP,
                 updated_at=CURRENT_TIMESTAMP""",
            (track_id, provider, url, 1 if primary else 0),
        )
        conn.execute(
            """INSERT INTO music_analysis_evidence
               (track_id, domain, source, source_type, method, payload_json,
                confidence, observed_at, created_at)
               VALUES (?, 'media_link', ?, 'web', 'link_registration', ?,
                       1.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (track_id, source, json.dumps({
                "provider": provider,
                "url": url,
                "streaming_only": True,
                "audio_downloaded": False,
            }, ensure_ascii=False)),
        )
        conn.commit()
    return {"track_id": track_id, "provider": provider, "source": source, "url": url}


def main() -> int:
    p = argparse.ArgumentParser(description="Register a Spotify/YouTube streaming link for a Jacques track")
    p.add_argument("track_id")
    p.add_argument("url")
    p.add_argument("--primary", action="store_true")
    args = p.parse_args()

    initialize_database()
    print(json.dumps(register(args.track_id, args.url, args.primary),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
