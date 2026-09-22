import json
import os
from datetime import datetime, timezone

import spotipy
from spotipy.oauth2 import SpotifyOAuth

DATA_FILE = "spotify_music_data.json"
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REFRESH_TOKEN = os.environ["SPOTIFY_REFRESH_TOKEN"]
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "user-library-read user-read-recently-played user-top-read"


oauth = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
)

token_info = oauth.refresh_access_token(REFRESH_TOKEN)
sp = spotipy.Spotify(auth=token_info["access_token"])

old_data = {}
try:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        old_data = json.load(f)
except FileNotFoundError:
    pass

music = {}
for track in old_data.get("tracks", []) if isinstance(old_data, dict) else []:
    if not isinstance(track, dict) or not track.get("track_id"):
        continue
    track.setdefault("play_history", [])
    music[track["track_id"]] = track


def upsert(track, saved=None, saved_at=None, played_at=None):
    track_id = track["id"]
    if track_id not in music:
        music[track_id] = {
            "track_id": track_id,
            "title": track.get("name", "정보 없음"),
            "artists": ", ".join(a["name"] for a in track.get("artists", [])),
            "album": track.get("album", {}).get("name", "정보 없음"),
            "release_date": track.get("album", {}).get("release_date", "정보 없음"),
            "saved": False,
            "saved_at": None,
            "last_played": None,
            "play_history": [],
            "top_short_term": False,
            "top_medium_term": False,
            "top_long_term": False,
            "spotify_url": track.get("external_urls", {}).get("spotify", "정보 없음"),
        }

    item = music[track_id]
    item["title"] = track.get("name", item.get("title", "정보 없음"))
    item["artists"] = ", ".join(a["name"] for a in track.get("artists", []))
    item["album"] = track.get("album", {}).get("name", item.get("album", "정보 없음"))
    item["release_date"] = track.get("album", {}).get("release_date", item.get("release_date", "정보 없음"))
    item["spotify_url"] = track.get("external_urls", {}).get("spotify", item.get("spotify_url", "정보 없음"))
    item.setdefault("play_history", [])

    if saved is not None:
        item["saved"] = saved
    if saved_at is not None:
        item["saved_at"] = saved_at
    if played_at is not None:
        item["last_played"] = played_at
        if played_at not in item["play_history"]:
            item["play_history"].append(played_at)
        item["play_history"] = sorted(item["play_history"])[-500:]


# Saved tracks: fetch the full current saved library and preserve added_at.
offset = 0
saved_total = 0
current_saved_ids = set()
while True:
    page = sp.current_user_saved_tracks(limit=50, offset=offset)
    saved_total = page.get("total", saved_total)
    for entry in page.get("items", []):
        track = entry["track"]
        current_saved_ids.add(track["id"])
        upsert(track, saved=True, saved_at=entry.get("added_at"))
    if not page.get("items") or offset + len(page["items"]) >= page.get("total", 0):
        break
    offset += len(page["items"])

# Tracks we already know about that are no longer saved.
for track_id, item in music.items():
    if item.get("saved") is True and track_id not in current_saved_ids:
        item["saved"] = False

# Recent plays from the last API window.
recent = sp.current_user_recently_played(limit=50)
for entry in recent.get("items", []):
    upsert(entry["track"], played_at=entry.get("played_at"))

# Current top lists. Reset flags first so the fields reflect the latest snapshot.
for item in music.values():
    item["top_short_term"] = False
    item["top_medium_term"] = False
    item["top_long_term"] = False

for time_range in ("short_term", "medium_term", "long_term"):
    top = sp.current_user_top_tracks(time_range=time_range, limit=50)
    flag = f"top_{time_range}"
    for track in top.get("items", []):
        upsert(track)
        music[track["id"]][flag] = True

output = old_data.copy() if isinstance(old_data, dict) else {}
output["schema_version"] = 1
output["collected_at"] = datetime.now(timezone.utc).isoformat()
output["tracks"] = list(music.values())

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"좋아요 곡: {saved_total}")
print(f"최근 재생: {len(recent.get('items', []))}")
print(f"전체 누적 곡: {len(music)}")
print(f"JSON 저장 완료: {DATA_FILE}")
