"""Current Development Mode Spotify Web API operation catalog (October 2026).

The 2026 migration removed legacy /tracks playlist writes and per-type save
operations. Only the published, supported endpoint surface is exposed here.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    scopes: tuple[str, ...] = ()
    note: str = ""

def e(method, path, scopes="", note=""):
    return Endpoint(method, path, tuple(scopes.split()) if scopes else (), note)

ENDPOINTS = {
    # Current authenticated user
    "profile.me": e("GET", "/me"),
    "profile.top": e("GET", "/me/top/{type}", "user-top-read"),
    # Catalog
    "catalog.search": e("GET", "/search"),
    "catalog.track": e("GET", "/tracks/{id}"),
    "catalog.album": e("GET", "/albums/{id}"),
    "catalog.album_tracks": e("GET", "/albums/{id}/tracks"),
    "catalog.artist": e("GET", "/artists/{id}"),
    "catalog.artist_albums": e("GET", "/artists/{id}/albums"),
    "catalog.audiobook": e("GET", "/audiobooks/{id}"),
    "catalog.audiobook_chapters": e("GET", "/audiobooks/{id}/chapters"),
    "catalog.chapter": e("GET", "/chapters/{id}"),
    "catalog.episode": e("GET", "/episodes/{id}"),
    "catalog.show": e("GET", "/shows/{id}"),
    "catalog.show_episodes": e("GET", "/shows/{id}/episodes"),
    # Saved content and following; 2026 uses generic URI-based mutations
    "library.tracks": e("GET", "/me/tracks", "user-library-read"),
    "library.albums": e("GET", "/me/albums", "user-library-read"),
    "library.shows": e("GET", "/me/shows", "user-library-read"),
    "library.episodes": e("GET", "/me/episodes", "user-library-read"),
    "library.audiobooks": e("GET", "/me/audiobooks", "user-library-read"),
    "library.followed_artists": e("GET", "/me/following", "user-follow-read"),
    "library.contains": e("GET", "/me/library/contains", "user-library-read"),
    "library.save": e("PUT", "/me/library", "user-library-modify"),
    "library.remove": e("DELETE", "/me/library", "user-library-modify"),
    # Playlists
    "playlist.list": e("GET", "/me/playlists", "playlist-read-private playlist-read-collaborative"),
    "playlist.get": e("GET", "/playlists/{playlist_id}", "playlist-read-private"),
    "playlist.items": e("GET", "/playlists/{playlist_id}/items", "playlist-read-private",
                        "Only owner/collaborator may access item lists in Dev Mode"),
    "playlist.cover": e("GET", "/playlists/{playlist_id}/images"),
    "playlist.create": e("POST", "/me/playlists", "playlist-modify-public playlist-modify-private"),
    "playlist.edit": e("PUT", "/playlists/{playlist_id}", "playlist-modify-public playlist-modify-private"),
    "playlist.add": e("POST", "/playlists/{playlist_id}/items", "playlist-modify-public playlist-modify-private"),
    "playlist.remove": e("DELETE", "/playlists/{playlist_id}/items", "playlist-modify-public playlist-modify-private"),
    "playlist.update": e("PUT", "/playlists/{playlist_id}/items", "playlist-modify-public playlist-modify-private",
                         "Reorder or replace; replacing overwrites existing items"),
    "playlist.upload_cover": e("PUT", "/playlists/{playlist_id}/images", "ugc-image-upload"),
    # Playback / Spotify Connect
    "player.state": e("GET", "/me/player", "user-read-playback-state"),
    "player.devices": e("GET", "/me/player/devices", "user-read-playback-state"),
    "player.current": e("GET", "/me/player/currently-playing", "user-read-currently-playing"),
    "player.recent": e("GET", "/me/player/recently-played", "user-read-recently-played"),
    "player.queue": e("GET", "/me/player/queue", "user-read-playback-state"),
    "player.transfer": e("PUT", "/me/player", "user-modify-playback-state"),
    "player.play": e("PUT", "/me/player/play", "user-modify-playback-state"),
    "player.pause": e("PUT", "/me/player/pause", "user-modify-playback-state"),
    "player.next": e("POST", "/me/player/next", "user-modify-playback-state"),
    "player.previous": e("POST", "/me/player/previous", "user-modify-playback-state"),
    "player.seek": e("PUT", "/me/player/seek", "user-modify-playback-state"),
    "player.repeat": e("PUT", "/me/player/repeat", "user-modify-playback-state"),
    "player.volume": e("PUT", "/me/player/volume", "user-modify-playback-state"),
    "player.shuffle": e("PUT", "/me/player/shuffle", "user-modify-playback-state"),
    "player.enqueue": e("POST", "/me/player/queue", "user-modify-playback-state"),
}
# Scopes to request when explicitly authorizing a full Jacques installation.
# Player streaming SDK, Spotify Open Access, and obsolete scopes are NOT Web API.
FULL_SCOPES = (
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public "
    "user-library-read user-library-modify "
    "user-follow-read user-follow-modify "
    "user-read-recently-played user-top-read user-read-playback-position "
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing ugc-image-upload"
)
UNAVAILABLE = {
    "playlist_emoji_reactions": "No documented Web API endpoint for song emoji reactions.",
    "audio_features": "Restricted/removed for Development Mode apps.",
    "audio_analysis": "Restricted/removed for Development Mode apps.",
    "recommendations": "Restricted/removed for Development Mode apps.",
    "other_users_playlists": "Dev Mode no longer exposes /users/{id}/playlists.",
    "batch_catalog": "Dev Mode removed several batch lookup endpoints.",
}
