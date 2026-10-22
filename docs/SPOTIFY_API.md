# Jacques Spotify Web API extension — October 2026

## What this is

A user-authorized Spotify Web API gateway for Jacques, preserving the existing
Spotify-to-SQLite workflow. The interface covers **the currently documented
Development Mode Web API**: catalog, playlists (including shared playlist items
for the owner/collaborator), library, personal history, queue and Spotify
Connect playback control. See:

- https://developer.spotify.com/documentation/web-api/references/changes/february-2026
- https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- https://developer.spotify.com/documentation/web-api/references/changes/july-2026
- https://developer.spotify.com/documentation/web-api/concepts/scopes

This is **not** Spotify's internal app API. Per-track emoji reactions are NOT
available from the documented Spotify Web API, and no private endpoint
scraping/reverse engineering has been added.

New/changed endpoints in 2026:
- Playlist item CRUD: /playlists/{id}/items, NOT /tracks.
- Save/unfollow: PUT/DELETE /me/library?uris=..., NOT per-type mutations.
- Playlist contents in Dev Mode: only owned or collaborative playlists.
- Search limit <= 10 per request. Metadata bulk endpoints mostly removed.
- Development Mode requires the Spotify developer account to be Premium.
- Per-account shared quota; 429 QUOTA_EXCEEDED is not retried.

## Install & check

From Jacques repository root, with Python >=3.11:

    python -m pip install requests spotipy
    python tools/spotify_api.py catalog
    python tools/spotify_api.py auth-check
    PYTHONPATH=src python -m unittest tests.test_spotify_api -v

The existing GitHub Actions secrets SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET,
SPOTIFY_REFRESH_TOKEN are reused. They must be set in the actual execution
environment. A GitHub Actions secret is **not automatically available** to a
local command or to ChatGPT.

## New OAuth consent / scope expansion

The existing refresh token was originally requested for library-read, recently
played and top items. A refresh cannot add playlist or write scopes. To request
the full Web API scope set, the user must re-authorize in their own terminal:

    export SPOTIFY_CLIENT_ID="from Spotify Developer dashboard"
    export SPOTIFY_CLIENT_SECRET="from Spotify Developer dashboard"
    python tools/spotify_api.py authorize

This opens Spotify's official OAuth consent URL, prompts for the local
redirected URL, and writes .spotify_refresh_token (mode 0600, gitignored).
Never put client secrets, access tokens or redirect URLs containing codes
into Git, a public Actions log, a public MCP URL or a chat.

After consent, local CLI calls read the protected .spotify_refresh_token file automatically (or use environment variables). A user who wants to update GitHub Actions can run locally:

    gh secret set SPOTIFY_REFRESH_TOKEN < .spotify_refresh_token

The redirect URI must match the URI registered in the Spotify app
(default http://127.0.0.1:8888/callback). Keep the other 3 existing scopes
for the current Spotify-to-DB pipeline.

## CLI examples

Read the current authenticated account:

    python tools/spotify_api.py call profile.me

List the account's playlists:

    python tools/spotify_api.py call playlist.list --all-pages

Read every available item in the collaborative band playlist:

    python tools/spotify_api.py call playlist.items \
      --path '{"playlist_id":"1U4uM0l6mwcyCdiFoV3RPV"}' --all-pages

Search catalog (limit 10 or below under Dev Mode):

    python tools/spotify_api.py call catalog.search \
      --params '{"q":"Radiohead","type":"track","limit":10}'

Preview (no API write, even if authenticated):

    python tools/spotify_api.py call playlist.add \
      --path '{"playlist_id":"1U4uM0l6mwcyCdiFoV3RPV"}' \
      --body '{"uris":["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]}'

Actual write requires both the explicit --execute flag and process-local
JACQUES_SPOTIFY_ALLOW_WRITES=1. This two-key control prevents an unattended
music sync from editing a playlist:

    JACQUES_SPOTIFY_ALLOW_WRITES=1 python tools/spotify_api.py call playlist.add \
      --path '{"playlist_id":"1U4uM0l6mwcyCdiFoV3RPV"}' \
      --body '{"uris":["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]}' --execute

To reorder, use playlist.update with range_start, insert_before and optional
snapshot_id. To replace ALL items, use playlist.update with the uris list:
this is destructive; obtain the owner's permission and a snapshot first.

For library.save/remove/contains, pass URI(s) in --params, e.g.
--params '{"uris":["spotify:track:4iV5W9uYEdYUVa79Axb7Rh"]}'.
Upload playlist art with playlist.upload_cover by providing a base64 JPEG
under 256 KB in body.jpeg_base64, user authorized.

## Local MCP adapter (optional)

    python -m pip install "mcp>=1,<2"
    PYTHONPATH=src python -m music_db.spotify_api.mcp_server

Exposes spotify_capabilities, spotify_api_call, spotify_playlist_all_items.
This is a **local stdio MCP**; it will not automatically make Spotify tools
appear in ChatGPT cloud. A separate authenticated remote MCP deployment and
ChatGPT connection are needed for access here. Do NOT expose Spotify bearer
tokens or an unauthenticated write proxy on the public Internet.

## Known limitations

- Spotify's Web API has **no documented per-song collaborative emoji reactions**.
- Development Mode now restricts the endpoints and metadata described in the
  Spotify February 2026 migration guide; existing extended quota mode differs.
- Spotify Connect playback needs an active device and compatible Premium plan.
- The gateway is explicitly opt-in and does not alter existing Spotify-to-DB
  schedule, canonical schema, snapshots, or enrichment steps.
- There is no live Spotify account test until OAuth credentials are available
  in the execution environment and the user completes consent.
