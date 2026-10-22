"""Offline unit tests: no credentials, Spotify account or network needed."""
import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from music_db.spotify_api.catalog import ENDPOINTS, FULL_SCOPES
from music_db.spotify_api.client import SpotifyClient, SpotifyAPIError

class SpotifyAPITest(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = SpotifyClient(access_token="fake", session=self.session,
                                    sleeper=lambda seconds: None)

    def response(self, status, value=None, headers=None):
        r = Mock()
        r.status_code = status
        r.headers = headers or {}
        r.content = b"{}" if value is not None else b""
        r.json.return_value = value
        return r

    def test_catalog_covers_core_2026_operations(self):
        for name in ("playlist.items", "playlist.remove", "playlist.add", "playlist.update",
                     "library.save", "library.contains", "player.queue", "player.transfer",
                     "profile.top", "catalog.search", "playlist.upload_cover"):
            self.assertIn(name, ENDPOINTS)
        self.assertIn("playlist-read-collaborative", FULL_SCOPES)
        self.assertTrue(all("/tracks" not in v.path or v.method == "GET"
                            for v in ENDPOINTS.values() if v.path.startswith("/playlists/")))

    def test_get_playlist_items_owner_collaborator_route(self):
        self.session.request.return_value = self.response(200, {"items": [], "next": None})
        result = self.client.call("playlist.items", path_values={"playlist_id": "1U4uM0l6mwcyCdiFoV3RPV"},
                                  params={"limit": 50})
        self.assertEqual(result["items"], [])
        args, kwargs = self.session.request.call_args
        self.assertEqual(args, ("GET", "https://api.spotify.com/v1/playlists/1U4uM0l6mwcyCdiFoV3RPV/items"))
        self.assertEqual(kwargs["params"], {"limit": 50})

    def test_previews_no_network(self):
        result = self.client.call("playlist.remove",
            path_values={"playlist_id": "ABC123"},
            body={"items": [{"uri": "spotify:track:ABC123"}]}, execute=True, allow_writes=False)
        self.assertTrue(result["dry_run"])
        self.session.request.assert_not_called()

    def test_actual_write_requires_both_gates(self):
        self.session.request.return_value = self.response(200, {"snapshot_id": "new"})
        result = self.client.call("playlist.add", path_values={"playlist_id": "ABC123"},
            body={"uris": ["spotify:track:ABC123"]}, execute=True, allow_writes=True)
        self.assertEqual(result["snapshot_id"], "new")
        args, kwargs = self.session.request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json"]["uris"], ["spotify:track:ABC123"])

    def test_paths_disallow_injection(self):
        with self.assertRaises(ValueError):
            self.client.call("playlist.items", path_values={"playlist_id": "../me"})
        with self.assertRaises(ValueError):
            self.client.call("profile.top", path_values={"type": "incorrect"})

    def test_playlist_update_does_not_mix_replace_and_reorder(self):
        with self.assertRaises(ValueError):
            self.client.call("playlist.update", path_values={"playlist_id": "ABC123"},
                 body={"uris": ["spotify:track:ABC123"], "range_start": 0})

    def test_2026_library_uses_uris_query(self):
        result = self.client.call("library.save", params={"uris": ["spotify:track:ABC123"]})
        self.assertEqual(result["path"], "/me/library")
        self.assertEqual(result["params"]["uris"], "spotify:track:ABC123")

    def test_search_limit(self):
        with self.assertRaises(ValueError):
            self.client.call("catalog.search", params={"q": "rock", "type": "track", "limit": 50})

    def test_pagination_uses_offset_and_stops(self):
        self.session.request.side_effect = [
            self.response(200, {"items": [{"item": {"name": "A"}}], "next": "next", "total": 2}),
            self.response(200, {"items": [{"item": {"name": "B"}}], "next": None, "total": 2}),
        ]
        result = self.client.all_pages("playlist.items", path_values={"playlist_id": "ABC123"})
        self.assertEqual(len(result["items"]), 2)
        self.assertFalse(result["truncated"])
        self.assertEqual(self.session.request.call_args_list[1].kwargs["params"]["offset"], 1)

    def test_nested_search_pagination(self):
        self.session.request.side_effect = [
            self.response(200, {"tracks": {"items": [{"id": "A"}], "next": "next", "total": 2}}),
            self.response(200, {"tracks": {"items": [{"id": "B"}], "next": None, "total": 2}}),
        ]
        result = self.client.all_pages("catalog.search", params={"type": "track", "q": "band"})
        self.assertEqual([x["id"] for x in result["items"]], ["A", "B"])
        self.assertEqual(result["pages_fetched"], 2)

    def test_cursor_endpoint_cannot_offset_loop(self):
        with self.assertRaisesRegex(ValueError, "cursor pagination"):
            self.client.all_pages("player.recent")

    def test_shared_quota_never_retries(self):
        self.session.request.return_value = self.response(429, {"error": {"reason": "QUOTA_EXCEEDED"}})
        with self.assertRaisesRegex(SpotifyAPIError, "QUOTA_EXCEEDED"):
            self.client.call("profile.me")
        self.assertEqual(self.session.request.call_count, 1)

    def test_429_rate_limit_one_retry(self):
        self.session.request.side_effect = [
            self.response(429, {"error": {"message": "Rate limited"}}, {"Retry-After": "0"}),
            self.response(200, {"id": "me"}),
        ]
        self.assertEqual(self.client.call("profile.me")["id"], "me")

    def test_no_auth_for_preview(self):
        client = SpotifyClient(session=self.session, access_token="")
        client.access_token = None
        result = client.call("playlist.create", body={"name": "New"})
        self.assertTrue(result["dry_run"])

    def test_cover_upload_requires_jpeg(self):
        encoded = base64.b64encode(b"bad image").decode()
        with self.assertRaises(ValueError):
            self.client.call("playlist.upload_cover", path_values={"playlist_id": "ABC123"},
                             body={"jpeg_base64": encoded})

if __name__ == "__main__":
    unittest.main()
