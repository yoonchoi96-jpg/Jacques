from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import spotipy
from spotipy.oauth2 import SpotifyOAuth

ROOT = Path(__file__).resolve().parents[1]


def youtube_search(query: str) -> str:
    proc = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", "1", f"ytsearch1:{query}"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in proc.stdout.splitlines():
        data = json.loads(line)
        video_id = data.get("id")
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    raise RuntimeError(f"No YouTube result for: {query}")


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
        artists = ", ".join(a["name"] for a in track["artists"])
        spotify_url = track["external_urls"]["spotify"]
        query = f"{title} {artists} official audio"

        print(f"\n[{rank}] {title} — {artists}")
        print(f"Spotify: {spotify_url}")

        try:
            youtube_url = youtube_search(query)
        except Exception as exc:
            print(f"YouTube resolution FAILED: {type(exc).__name__}: {exc}")
            continue

        print(f"YouTube: {youtube_url}")
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
