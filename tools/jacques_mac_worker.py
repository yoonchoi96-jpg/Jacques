"""Mac mini agent: polls owner-authored GitHub Issues, never runs arbitrary shell.

Spotify READ tasks -> manually-configured GitHub Actions workflow_dispatch.
Spotify WRITE tasks -> stop for human GitHub Actions approval.
Mac tasks -> bounded local diagnostics, private local JSON result.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jacques_queue import PREFIX, DONE, validate_issue
from music_db.spotify_api.catalog import ENDPOINTS

MARKERS = ("JACQUES_DISPATCHED:", "JACQUES_PENDING_MANUAL_WRITE:",
           "JACQUES_LOCAL_DONE:", "JACQUES_FAILURE:", DONE)
RESULT_DIR = Path.home() / "Library" / "Application Support" / "Jacques" / "results"

def gh(*args):
    executable = shutil.which("gh")
    if not executable:
        raise RuntimeError("Install GitHub CLI (gh) and authenticate with gh auth login")
    result = subprocess.run([executable, *args], capture_output=True, text=True,
                            timeout=45, check=False)
    if result.returncode:
        # Never print GH stderr: could include private task contents.
        raise RuntimeError("GitHub CLI operation failed (check gh auth status)")
    return result.stdout

def api(repo, suffix, *flags):
    return json.loads(gh("api", "-X", "GET", f"repos/{repo}/{suffix}", *flags))

def issue_comments(repo, number):
    comments = []
    for page in range(1, 6):
        part = api(repo, f"issues/{number}/comments?per_page=100&page={page}")
        comments.extend(part)
        if len(part) < 100:
            break
    return comments

def comment(repo, number, message):
    # GitHub CLI quotes body as an argument; never evaluated as shell.
    gh("issue", "comment", str(number), "-R", repo, "--body", message)

def close(repo, number):
    gh("issue", "close", str(number), "-R", repo)

def local_summary(operation):
    if operation == "mac.health":
        return {"ok": True, "platform": platform.system(),
                "python": platform.python_version(),
                "checked_at": datetime.now(timezone.utc).isoformat()}
    if operation == "mac.db_counts":
        path = ROOT / "db" / "music.db"
        if not path.is_file():
            return {"ok": False, "reason": "Local SQLite database missing"}
        uri = path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            counts = {}
            for table in ("tracks", "artists", "albums", "play_history"):
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (table,)).fetchone():
                    counts[table] = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            return {"ok": True, "counts": counts,
                    "checked_at": datetime.now(timezone.utc).isoformat()}
    raise ValueError("Mac operation is not allowlisted")

def save_local(number, result):
    RESULT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = RESULT_DIR / f"{number}.json"
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)

def process_issue(repo, issue, owner):
    number = issue["number"]
    if not issue.get("title", "").startswith(PREFIX):
        return "skip"
    try:
        task = validate_issue(issue, owner)
    except (ValueError, TypeError, KeyError):
        return "skip"
    comments = issue_comments(repo, number)
    trusted = {owner.casefold(), "github-actions[bot]"}
    if any(
        c.get("user", {}).get("login", "").casefold() in trusted and
        any(marker in str(c.get("body", "")) for marker in MARKERS)
        for c in comments
    ):
        return "handled"
    if task["target"] == "spotify":
        if ENDPOINTS[task["operation"]].method != "GET":
            comment(repo, number, "JACQUES_PENDING_MANUAL_WRITE: This is a public "
                    "queue request. Review the plan and manually dispatch "
                    "Jacques Spotify Command Queue in preview mode first. "
                    "Mac worker will NEVER auto-execute writes.")
            return "manual-approval"
        gh("workflow", "run", "jacques-spotify-command.yml", "-R", repo,
           "--ref", "main", "-f", f"issue_number={number}", "-f", "mode=execute")
        comment(repo, number, "JACQUES_DISPATCHED: Spotify read submitted "
                "to manual-only GitHub Actions workflow. Results are private "
                "by default; check the workflow status.")
        return "dispatched"
    result = local_summary(task["operation"])
    save_local(number, result)
    comment(repo, number, "JACQUES_LOCAL_DONE: Mac diagnostic result saved "
            "privately on Mac mini. No private data posted to GitHub.")
    close(repo, number)
    return "local-done"

def poll(repo):
    owner = repo.split("/")[0]
    issues = api(repo, "issues?state=open&per_page=100")
    outcomes = {"scanned": 0, "dispatched": 0, "local-done": 0,
                "manual-approval": 0, "handled": 0, "skip": 0}
    for issue in issues:
        if "pull_request" in issue:
            continue
        outcomes["scanned"] += 1
        try:
            state = process_issue(repo, issue, owner)
        except Exception:
            # No private data and no raw GH error text in logs.
            print(f"Queue issue #{issue.get('number')} had a worker error.", file=sys.stderr)
            continue
        outcomes[state] += 1
    print(json.dumps(outcomes, sort_keys=True))

def main():
    parser = argparse.ArgumentParser(description="Jacques Mac mini queue worker (one poll)")
    parser.add_argument("--repo", default="yoonchoi96-jpg/Jacques")
    args = parser.parse_args()
    if (args.repo != "yoonchoi96-jpg/Jacques" and
        os.environ.get("JACQUES_QUEUE_ALLOW_OTHER_REPO") != "1"):
        raise SystemExit("Repository override needs JACQUES_QUEUE_ALLOW_OTHER_REPO=1")
    try:
        poll(args.repo)
    except Exception as exc:
        print("Mac worker failed: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(2) from None

if __name__ == "__main__":
    main()
