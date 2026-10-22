"""Jacques Spotify Web API utility. Writes default to dry-run."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass
sys.path.insert(0, str(ROOT / "src"))

from music_db.spotify_api.catalog import ENDPOINTS, FULL_SCOPES, UNAVAILABLE
from music_db.spotify_api.client import SpotifyClient, SpotifyAPIError


def decode(value, flag):
    try:
        obj = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{flag}: expected JSON object: {exc}") from exc
    if not isinstance(obj, dict):
        raise SystemExit(f"{flag}: expected JSON object")
    return obj


def main():
    parser = argparse.ArgumentParser(description="Jacques: Spotify Web API (2026)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="Display supported operation names and scopes")
    sub.add_parser("auth-check", help="Check locally configured credentials (no API call)")
    sub.add_parser("authorize", help="Reauthorize complete scopes in browser (interactive)")
    call = sub.add_parser("call", help="Read a resource, or preview/execute a write")
    call.add_argument("operation", choices=sorted(ENDPOINTS))
    call.add_argument("--path", default="{}", help='e.g. {"playlist_id":"..."}')
    call.add_argument("--params", default="{}", help='e.g. {"limit":50}')
    call.add_argument("--body", default="{}", help='e.g. {"uris":["spotify:track:..."]}')
    call.add_argument("--all-pages", action="store_true")
    call.add_argument("--max-pages", type=int, default=100)
    call.add_argument("--execute", action="store_true", help="Enable a write only with environment gate")
    args = parser.parse_args()

    if args.command == "catalog":
        result = {name: {"method": endpoint.method, "path": endpoint.path,
                         "scopes": endpoint.scopes, "note": endpoint.note}
                  for name, endpoint in ENDPOINTS.items()}
        print(json.dumps({"operations": result, "unavailable": UNAVAILABLE},
                         indent=2, ensure_ascii=False))
        return
    if args.command == "auth-check":
        required = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")
        print(json.dumps({"refresh_credentials_configured": all(os.getenv(k) for k in required),
                          "access_token_configured": bool(os.getenv("SPOTIFY_ACCESS_TOKEN")),
                          "writes_enabled": os.getenv("JACQUES_SPOTIFY_ALLOW_WRITES") == "1"}))
        return
    if args.command == "authorize":
        from spotipy.oauth2 import SpotifyOAuth
        needed = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET")
        if not all(os.getenv(k) for k in needed):
            raise SystemExit("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set")
        cache = ROOT / ".spotify_cache_full"
        auth = SpotifyOAuth(
            client_id=os.environ["SPOTIFY_CLIENT_ID"],
            client_secret=os.environ["SPOTIFY_CLIENT_SECRET"],
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            scope=FULL_SCOPES,
            cache_path=str(cache),
            open_browser=True,
        )
        url = auth.get_authorize_url()
        print("Authorize via browser URL (never paste credentials in chat):", url)
        redirect = input("Paste the full redirected URL (locally, not in chat): ").strip()
        code = auth.parse_response_code(redirect)
        if not code:
            raise SystemExit("Spotify authorization code not found")
        tokens = auth.get_access_token(code, as_dict=True, check_cache=False)
        secret_file = ROOT / ".spotify_refresh_token"
        secret_file.write_text(tokens["refresh_token"] + "\n", encoding="utf-8")
        os.chmod(secret_file, 0o600)
        print(f"Refresh token saved to protected local file: {secret_file}")
        print("Optional: gh secret set SPOTIFY_REFRESH_TOKEN < .spotify_refresh_token")
        print("The existing GitHub Actions token is not automatically updated.")
        return
    client = SpotifyClient()
    path = decode(args.path, "--path")
    params = decode(args.params, "--params")
    body = decode(args.body, "--body")
    if args.all_pages:
        if args.execute:
            raise SystemExit("--execute and --all-pages cannot be combined")
        result = client.all_pages(args.operation, path_values=path, params=params,
                                  max_pages=args.max_pages)
    else:
        result = client.call(args.operation, path_values=path, params=params, body=body,
                             execute=args.execute,
                             allow_writes=os.getenv("JACQUES_SPOTIFY_ALLOW_WRITES") == "1")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (SpotifyAPIError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
