"""Spotify API HTTP client with OAuth refresh and 2026 endpoint validation."""
from __future__ import annotations

import base64
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .catalog import ENDPOINTS

BASE_URL = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ID_PATTERN = re.compile(r"^[A-Za-z0-9]{1,128}$")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
URI_PATTERN = re.compile(r"^spotify:(track|album|episode|show|audiobook|user|playlist):[A-Za-z0-9]+$")

class SpotifyAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"Spotify API {status}: {message}")

def _json_error(response):
    try:
        info = response.json().get("error", {})
        if isinstance(info, dict):
            return info.get("reason") or info.get("message") or "Request failed"
        return str(info)[:200]
    except (ValueError, AttributeError):
        return f"HTTP {response.status_code}"

def _uris(value, limit):
    if isinstance(value, list):
        value = ",".join(value)
    if not isinstance(value, str) or not value:
        raise ValueError("uris must be a nonempty Spotify URI string or list")
    values = value.split(",")
    if len(values) > limit or not all(URI_PATTERN.fullmatch(x) for x in values):
        raise ValueError(f"Expected 1..{limit} valid Spotify URIs")
    return ",".join(values)

def _validate_payload(operation, params, body):
    params, body = dict(params or {}), dict(body or {})
    if operation == "profile.top" and params.get("type") not in (None, "artists", "tracks"):
        raise ValueError("top type must be artists or tracks")
    if operation == "catalog.search" and "limit" in params:
        if not 1 <= int(params["limit"]) <= 10:
            raise ValueError("2026 Development Mode search limit is 1..10")
    if operation in ("library.save", "library.remove", "library.contains"):
        if "uris" in body and "uris" not in params:
            params["uris"] = body.pop("uris")
        params["uris"] = _uris(params.get("uris"), 40)
    if operation in ("playlist.add", "playlist.update"):
        if "uris" in body:
            uris = body["uris"]
            if not isinstance(uris, list) or len(uris) > 100:
                raise ValueError("Playlist uris must be a list of up to 100 elements")
            _uris(uris, 100) if uris else None
    if operation == "playlist.remove":
        items = body.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or not all(
            isinstance(x, dict) and URI_PATTERN.fullmatch(str(x.get("uri", ""))) for x in items
        ):
            raise ValueError("playlist.remove requires 1..100 items [{uri: spotify:track:...}]")
    if operation == "playlist.update" and "uris" in body and (
        "range_start" in body or "insert_before" in body
    ):
        raise ValueError("Playlist replace and reorder are mutually exclusive")
    if operation == "playlist.upload_cover" and not body.get("jpeg_base64"):
        raise ValueError("upload_cover requires body.jpeg_base64")
    return params, body

class SpotifyClient:
    def __init__(self, access_token=None, *, client_id=None, client_secret=None,
                 refresh_token=None, session=None, sleeper=None):
        self.access_token = access_token or os.getenv("SPOTIFY_ACCESS_TOKEN")
        self.client_id = client_id or os.getenv("SPOTIFY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("SPOTIFY_CLIENT_SECRET")
        self.refresh_token = refresh_token or os.getenv("SPOTIFY_REFRESH_TOKEN")
        if not self.refresh_token:
            local_file = Path(__file__).resolve().parents[3] / ".spotify_refresh_token"
            if local_file.is_file():
                self.refresh_token = local_file.read_text(encoding="utf-8").strip()
        self.session = session or requests.Session()
        self.sleep = sleeper or time.sleep

    def token(self):
        if self.access_token:
            return self.access_token
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise RuntimeError("Configure SPOTIFY_ACCESS_TOKEN or CLIENT_ID/CLIENT_SECRET/REFRESH_TOKEN")
        response = self.session.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            timeout=20,
        )
        if not response.ok:
            raise SpotifyAPIError(response.status_code, _json_error(response))
        payload = response.json()
        self.access_token = payload["access_token"]
        return self.access_token

    def prepare(self, operation, path_values=None, params=None, body=None):
        if operation not in ENDPOINTS:
            raise ValueError(f"Unsupported Spotify operation: {operation}")
        endpoint = ENDPOINTS[operation]
        path_values = dict(path_values or {})
        params, body = _validate_payload(operation, params, body)
        def sub(match):
            key = match.group(1)
            val = str(path_values.get(key, ""))
            if not ID_PATTERN.fullmatch(val):
                raise ValueError(f"Missing or unsafe Spotify path argument: {key}")
            if key == "type" and val not in ("artists", "tracks"):
                raise ValueError("type must be artists or tracks")
            return quote(val, safe="")
        path = PLACEHOLDER.sub(sub, endpoint.path)
        if operation == "library.followed_artists":
            params.setdefault("type", "artist")
        if operation == "playlist.upload_cover":
            raw = body.get("jpeg_base64", "")
            try:
                data = base64.b64decode(raw, validate=True)
            except (ValueError, Exception) as exc:
                raise ValueError("Invalid base64 JPEG payload") from exc
            if not data.startswith(b"\xff\xd8\xff") or len(data) > 256 * 1024:
                raise ValueError("Playlist cover must be JPEG and <= 256 KB")
        return endpoint, path, params, body

    def call(self, operation, *, path_values=None, params=None, body=None,
             execute=False, allow_writes=False):
        endpoint, path, params, body = self.prepare(operation, path_values, params, body)
        if endpoint.method != "GET" and not (execute and allow_writes):
            return {
                "dry_run": True, "operation": operation, "method": endpoint.method,
                "path": path, "params": params,
                "body": {k: "<base64 JPEG hidden>" if k == "jpeg_base64" else v
                         for k, v in body.items()},
                "to_execute": "Pass --execute AND JACQUES_SPOTIFY_ALLOW_WRITES=1",
            }
        headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
        kwargs = {"params": params or None, "headers": headers, "timeout": 25}
        if operation == "playlist.upload_cover":
            headers["Content-Type"] = "image/jpeg"
            kwargs["data"] = body["jpeg_base64"].encode("ascii")
        elif endpoint.method in ("POST", "PUT", "DELETE") and body:
            kwargs["json"] = body
        response = None
        for retry in range(3):
            response = self.session.request(endpoint.method, BASE_URL + path, **kwargs)
            if response.status_code == 429:
                reason = _json_error(response)
                if reason == "QUOTA_EXCEEDED":
                    raise SpotifyAPIError(429, "QUOTA_EXCEEDED (shared developer quota)")
                if retry < 2:
                    try:
                        wait = min(max(float(response.headers.get("Retry-After", "2")), 0.0), 30.0)
                    except ValueError:
                        wait = 2.0
                    self.sleep(wait)
                    continue
            if response.status_code in (500, 502, 503, 504) and retry < 2:
                self.sleep(2 ** retry)
                continue
            break
        if response.status_code >= 400:
            raise SpotifyAPIError(response.status_code, _json_error(response))
        if response.status_code == 204 or not response.content:
            return {"ok": True, "status": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {"ok": True, "status": response.status_code}

    def all_pages(self, operation, *, path_values=None, params=None, max_pages=100):
        """Offset pagination for documented paged resources; never follow untrusted next URLs."""
        if ENDPOINTS[operation].method != "GET":
            raise ValueError("Pagination is read-only")
        if not 1 <= max_pages <= 200:
            raise ValueError("max_pages must be 1..200")
        # Recently played / followed artists are cursor-paginated, not offset-paginated.
        if operation in ("player.recent", "library.followed_artists"):
            raise ValueError(f"{operation} uses cursor pagination; use individual calls with after/before")
        params = dict(params or {})
        if operation == "catalog.search":
            kind = str(params.get("type", ""))
            if "," in kind or kind not in {
                "album", "artist", "playlist", "track", "show", "episode", "audiobook"
            }:
                raise ValueError("Search pagination needs exactly one type")
            bucket = kind + "s"
        else:
            bucket = None
        params.setdefault("limit", 10 if bucket else 50)
        offset = int(params.get("offset", 0))
        all_items = []
        for page_number in range(1, max_pages + 1):
            params["offset"] = offset
            page = self.call(operation, path_values=path_values, params=params)
            page = page.get(bucket, {}) if bucket else page
            if not isinstance(page, dict):
                raise ValueError("Pagination expected a response object")
            items = page.get("items")
            if not isinstance(items, list):
                raise ValueError(f"{operation} is not an items-paginated endpoint")
            all_items.extend(items)
            if not page.get("next") or not items:
                return {"items": all_items, "total": page.get("total", len(all_items)),
                        "truncated": False, "pages_fetched": page_number}
            offset += len(items)
        return {"items": all_items, "truncated": True, "pages_fetched": max_pages}
