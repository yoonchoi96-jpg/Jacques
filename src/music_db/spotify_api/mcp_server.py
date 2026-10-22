"""Optional local stdio MCP adapter for Jacques Spotify.

Install optional dependency: pip install 'mcp>=1,<2'
Run locally: PYTHONPATH=src python -m music_db.spotify_api.mcp_server
Not a remotely hosted connector; ChatGPT must be separately connected to a
private hosted MCP service to use these tools from chat.
"""
import json
import os
from .catalog import ENDPOINTS, UNAVAILABLE
from .client import SpotifyClient

def build_server():
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("Jacques Spotify")

    @server.tool()
    def spotify_capabilities() -> dict:
        """List the Spotify Web API operations Jacques supports (2026)."""
        return {"operations": {name: {"method": v.method, "path": v.path, "scopes": v.scopes}
                               for name, v in ENDPOINTS.items()}, "unavailable": UNAVAILABLE}

    @server.tool()
    def spotify_api_call(operation: str, path_json: str = "{}",
                         params_json: str = "{}", body_json: str = "{}",
                         execute: bool = False) -> dict:
        """Call a named operation. All writes are previews unless explicitly enabled."""
        if operation not in ENDPOINTS:
            raise ValueError("Unsupported operation")
        path = json.loads(path_json)
        params = json.loads(params_json)
        body = json.loads(body_json)
        if not all(isinstance(x, dict) for x in (path, params, body)):
            raise ValueError("path_json, params_json, body_json must be objects")
        return SpotifyClient().call(
            operation, path_values=path, params=params, body=body, execute=execute,
            allow_writes=os.getenv("JACQUES_SPOTIFY_ALLOW_WRITES") == "1",
        )

    @server.tool()
    def spotify_playlist_all_items(playlist_id: str, max_pages: int = 100) -> dict:
        """Read every visible track/episode and contributor from owned/collab playlist."""
        return SpotifyClient().all_pages("playlist.items",
                                          path_values={"playlist_id": playlist_id},
                                          max_pages=max_pages)
    return server

if __name__ == "__main__":
    build_server().run(transport="stdio")
