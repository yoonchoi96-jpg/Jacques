"""Jacques GitHub Issues queue: Spotify execution, no public private-data logs.

Issues are PUBLIC instructions; actual Spotify credentials stay in Actions secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from music_db.spotify_api.catalog import ENDPOINTS
from music_db.spotify_api.client import SpotifyClient, SpotifyAPIError

PREFIX = "[Jacques Queue] "
ID_RE = re.compile(r"^[A-Za-z0-9]{1,128}$")
TASK_FIELDS = frozenset(("version", "target", "operation", "path_values",
    "params", "body", "all_pages", "max_pages", "public_export", "expected_snapshot_id"))
PARAM_FIELDS = frozenset(("limit", "offset", "market", "type", "time_range",
    "after", "before", "q", "fields", "uris", "additional_types", "include_groups"))
MAC_OPERATIONS = frozenset(("mac.health", "mac.db_counts"))
# Non-idempotent player control and playlist creation are CLI-only, not GitHub queued.
QUEUE_WRITES = frozenset(("playlist.add", "playlist.remove", "playlist.update",
                         "playlist.edit", "library.save", "library.remove"))
DONE = "JACQUES_RESULT: EXECUTED"

def parse_task(source):
    """Strict schema. No arbitrary command execution and no Spotify tokens."""
    if not isinstance(source, str) or len(source) > 8000:
        raise ValueError("Invalid task size")
    try:
        obj = json.loads(source)
    except (ValueError, TypeError) as exc:
        raise ValueError("Issue body must be a JSON task object") from exc
    if not isinstance(obj, dict) or set(obj) - TASK_FIELDS or obj.get("version") != 1:
        raise ValueError("Unsupported task schema")
    if type(obj["version"]) is not int:
        raise ValueError("Invalid task version")
    target, op = obj.get("target"), obj.get("operation")
    if target == "spotify":
        if op not in ENDPOINTS:
            raise ValueError("Spotify operation is not on the allowlist")
        is_write = ENDPOINTS[op].method != "GET"
        if is_write and op not in QUEUE_WRITES:
            raise ValueError("Unsafe queued write: use direct Spotify CLI instead")
    elif target == "mac":
        if op not in MAC_OPERATIONS:
            raise ValueError("Mac operation is not on the allowlist")
        is_write = False
    else:
        raise ValueError("Unsupported task target")
    paths, params, body = obj.get("path_values", {}), obj.get("params", {}), obj.get("body", {})
    if not all(isinstance(v, dict) for v in (paths, params, body)):
        raise ValueError("Task parameters must be objects")
    if not set(paths).issubset(("id", "playlist_id", "type")) or not all(
        isinstance(x, str) and ID_RE.fullmatch(x) for x in paths.values()
    ):
        raise ValueError("Invalid path arguments")
    if not set(params).issubset(PARAM_FIELDS) or len(params) > 12 or len(json.dumps(params)) > 1500:
        raise ValueError("Invalid query parameters")
    if not all(type(x) in (str, int, bool, list) for x in params.values()):
        raise ValueError("Unsupported query argument type")
    if "limit" in params and (type(params["limit"]) is not int or not 1 <= params["limit"] <= 50):
        raise ValueError("List limit must be 1..50")
    if "offset" in params and (type(params["offset"]) is not int or not 0 <= params["offset"] <= 2000):
        raise ValueError("Invalid offset")
    if len(json.dumps(body)) > 5000:
        raise ValueError("Task write payload too large")
    all_pages, public_export = obj.get("all_pages", False), obj.get("public_export", False)
    if type(all_pages) is not bool or type(public_export) is not bool:
        raise ValueError("Invalid boolean option")
    max_pages = obj.get("max_pages", 20)
    if type(max_pages) is not int or not 1 <= max_pages <= 200:
        raise ValueError("max_pages must be 1..200")
    snapshot = obj.get("expected_snapshot_id")
    if snapshot is not None and (not isinstance(snapshot, str) or
        not re.fullmatch(r"[A-Za-z0-9+/=]{1,180}", snapshot)):
        raise ValueError("Invalid snapshot")
    if target == "mac" and (paths or params or body or all_pages or public_export or snapshot):
        raise ValueError("Mac operations take no arguments")
    if target == "spotify":
        if is_write and (all_pages or public_export):
            raise ValueError("Writes cannot export results")
        if not is_write and body:
            raise ValueError("GET operations cannot carry a body")
        if public_export and (op != "playlist.items" or not all_pages):
            raise ValueError("Public export is playlist.items only")
        if op in ("playlist.add", "playlist.remove", "playlist.update", "playlist.edit") and not snapshot:
            raise ValueError("Playlist write requires expected_snapshot_id")
        if snapshot and op not in ("playlist.add", "playlist.remove", "playlist.update", "playlist.edit"):
            raise ValueError("Snapshot only applies to existing playlist edits")
    result = dict(obj)
    result.setdefault("path_values", {})
    result.setdefault("params", {})
    result.setdefault("body", {})
    result.setdefault("all_pages", False)
    result.setdefault("max_pages", 20)
    result.setdefault("public_export", False)
    result.setdefault("expected_snapshot_id", None)
    return result

def validate_issue(issue, owner):
    if (not isinstance(issue, dict) or issue.get("pull_request") or
        not issue.get("title", "").startswith(PREFIX) or
        issue.get("state") != "open" or
        issue.get("user", {}).get("login", "").casefold() != owner.casefold()):
        raise ValueError("Task Issue is not open or was not authored by owner")
    return parse_task(issue.get("body", ""))

def digest(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode()).hexdigest()

class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Unsafe repository identifier")
        if not token:
            raise RuntimeError("GH_TOKEN missing")
        self.repo = repository
        self.owner = repository.split("/")[0]
        self.token = token

    def request(self, method, resource, payload=None):
        if not resource.startswith("/") or ".." in resource:
            raise ValueError("Unsafe GitHub API path")
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            "https://api.github.com/repos/" + self.repo + resource,
            data=data, method=method, headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            })
        try:
            with urllib.request.urlopen(req, timeout=25) as res:
                raw = res.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError("GitHub API HTTP " + str(exc.code)) from None

    def issue(self, number):
        return self.request("GET", "/issues/" + str(number))

    def comments(self, number):
        result = []
        for page in range(1, 6):
            batch = self.request("GET", f"/issues/{number}/comments?per_page=100&page={page}")
            result.extend(batch)
            if len(batch) < 100:
                break
        return result

    def comment(self, number, message):
        self.request("POST", f"/issues/{number}/comments", {"body": message})

    def close(self, number):
        self.request("PATCH", f"/issues/{number}", {"state": "closed"})

def export_public_items(items):
    lines = []
    for row in items[:150]:
        track = row.get("item") or row.get("track") or {}
        if not isinstance(track, dict):
            continue
        title = str(track.get("name") or "").replace("\r", " ").replace("\n", " ")[:100]
        artists = ", ".join(str(a.get("name", ""))[:60] for a in track.get("artists", [])
                            if isinstance(a, dict))[:120]
        if title:
            lines.append(f"{len(lines)+1}. {title} — {artists}")
    return "\n".join(lines)

def spotify_job(gh, number, task, mode, approval, expected_digest):
    op = task["operation"]
    is_write = ENDPOINTS[op].method != "GET"
    if any(DONE in comment.get("body", "") for comment in gh.comments(number)):
        print("Already executed; refusing replay.")
        return
    client = SpotifyClient()
    kwargs = {"path_values": task["path_values"], "params": task["params"],
              "body": task["body"]}
    if is_write:
        if mode == "preview":
            client.call(op, **kwargs)
            gh.comment(number, "JACQUES_PREVIEW: no Spotify write occurred. "
                       "Inspect the Issue and manually run this workflow with "
                       "mode=execute, approval=APPROVE_WRITE, plan_digest=" + digest(task))
            print("Write preview; no Spotify write.")
            return
        if approval != "APPROVE_WRITE" or expected_digest != digest(task):
            raise ValueError("Manual approval and matching preview digest required")
        if task["expected_snapshot_id"]:
            cur = client.call("playlist.get",
                path_values={"playlist_id": task["path_values"]["playlist_id"]})
            if cur.get("snapshot_id") != task["expected_snapshot_id"]:
                raise ValueError("Playlist changed since planning; aborting")
        client.call(op, **kwargs, execute=True, allow_writes=True)
        gh.comment(number, DONE + " | write accepted | plan=" + digest(task))
    else:
        if mode != "execute":
            raise ValueError("Read jobs require execute mode")
        if task["all_pages"]:
            result = client.all_pages(op, path_values=task["path_values"],
                params=task["params"], max_pages=task["max_pages"])
        else:
            result = client.call(op, path_values=task["path_values"],
                                 params=task["params"])
        if task["public_export"]:
            playlist_id = task["path_values"].get("playlist_id")
            if not playlist_id:
                raise ValueError("Missing playlist ID for public export")
            playlist = client.call("playlist.get", path_values={"playlist_id": playlist_id})
            if playlist.get("public") is not True or result.get("truncated"):
                raise ValueError("Playlist not public or export incomplete")
            lines = export_public_items(result.get("items", []))
            gh.comment(number, DONE + " | opt-in PUBLIC Spotify playlist export\n"
                       + (lines or "(no tracks returned)") +
                       ("\n(Only first 150 items displayed.)"
                        if len(result.get("items", [])) > 150 else ""))
        else:
            gh.comment(number, DONE + " | Spotify read succeeded. "
                       "Private response deliberately not posted to public GitHub.")
    gh.close(number)
    print("Success. No private Spotify response was logged.")

def error_label(exc):
    if isinstance(exc, SpotifyAPIError):
        if exc.status in (401, 403):
            return "Spotify OAuth token or scope rejected, HTTP " + str(exc.status)
        return "Spotify HTTP " + str(exc.status)
    return "Rejected / failed: " + type(exc).__name__

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--mode", choices=("execute", "preview"), default="execute")
    parser.add_argument("--approval", default="")
    parser.add_argument("--plan-digest", default="")
    args = parser.parse_args()
    if args.issue < 1:
        raise SystemExit("Invalid Issue number")
    gh = None
    try:
        gh = GitHub(os.environ["GITHUB_REPOSITORY"],
                    os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
        task = validate_issue(gh.issue(args.issue), gh.owner)
        if task["target"] != "spotify":
            raise ValueError("This workflow only executes Spotify jobs")
        spotify_job(gh, args.issue, task, args.mode, args.approval, args.plan_digest)
        return 0
    except Exception as exc:
        # Raw Spotify error strings can contain user content: never print them.
        msg = error_label(exc)
        print(msg, file=sys.stderr)
        if gh is not None:
            try:
                gh.comment(args.issue, "JACQUES_FAILURE: " + msg + ". Task remains open.")
            except Exception:
                pass
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
