# Jacques GitHub Command Queue (no remote MCP)

Architecture: ChatGPT/GPT-6 creates a public GitHub Issue command -> Mac mini
polls the issue via `gh` -> Mac mini dispatches existing GitHub-hosted Actions
for **Spotify reads** -> Actions accesses existing Spotify GitHub Secrets ->
status comment appears on the issue -> ChatGPT reads status from GitHub.

Spotify **writes** are never automatically dispatched. They require the user
to manually preview and approve the exact issue plan through GitHub Actions.

## Critical: Public repo privacy

Jacques is PUBLIC. Never include secrets, refresh tokens, private listening
history, personal details, or sensitive queries in the Issue title/body.
Actions prints only task status, never private API responses. GitHub Issues and
workflow logs are not secure personal-data storage.

Default Spotify reads post ONLY success/failure (not private track lists).
**ChatGPT cannot extract private playlists from this public results channel.**
A private repo/Drive destination would be needed for private song-list retrieval.

For an explicitly PUBLIC Spotify playlist only, set `public_export: true`.
The executor independently verifies `playlist.public == true`. It will then
publish up to 150 songs' titles/artists directly in the public GitHub Issue.
This option must be chosen knowingly; everyone can see that Issue.

## Current GitHub Secrets

Existing SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN are
injected into the Actions job; they are not copied onto Mac mini. GitHub
Secrets are not viewable after creation. Refreshing an old token **does not**
grant unapproved playlist or write OAuth scopes. A Spotify HTTP 403 may require
one-time reauthorization, which cannot be bypassed by code patches.

## Mac mini, one-time opt-in installation

Use existing checkout (its exact location can vary):

    cd /Users/yoon/Documents/Jacques
    git pull --ff-only
    gh auth status
    python tools/jacques_mac_worker.py
    python tools/install_jacques_worker.py --install

Install GitHub CLI if missing: `brew install gh`; authenticate with
`gh auth login`. The worker needs repository issue-write and actions-write
access under the same GitHub owner account.

LaunchAgent polls **once per minute**, consumes no new server/MCP, and keeps
the previous *manual-only* GitHub Actions policy: no new Actions schedule,
issues or push triggers were added. Explicit workflow_dispatch by the opted-in
Mac worker is the only runner trigger.

Stop the worker:

    python tools/install_jacques_worker.py --uninstall

Mac-only diagnostic output is local (mode 0600), at:
`~/Library/Application Support/Jacques/results/<issue-number>.json`.
Only generic status is public. No arbitrary GitHub issue command or shell
script can be executed by the worker.

## Creating a command

Create a GitHub issue in `yoonchoi96-jpg/Jacques` **as the repo owner
yoonchoi96-jpg**. Title must start with `[Jacques Queue] `. Issue body
must consist of one JSON object, e.g.:

```json
{
  "version": 1,
  "target": "spotify",
  "operation": "playlist.items",
  "path_values": {"playlist_id": "1U4uM0l6mwcyCdiFoV3RPV"},
  "params": {"limit": 50},
  "all_pages": true,
  "max_pages": 20
}
```

The worker will dispatch `jacques-spotify-command.yml`. Actions will run
against the existing repo Spotify secrets and post a sanitized status back
to the Issue. On success, the Issue is closed.

**Mac offline?** Open Actions > Jacques Spotify Command Queue > Run workflow,
provide `issue_number`, choose mode `execute`. GitHub itself will perform
the Spotify call. No Mac needed if run manually.

An explicit public-song-list export is allowed only if Spotify confirms the
playlist is public. Add `"public_export": true` to the example, with
`"all_pages": true`. This publishes names on the publicly visible Issue.
It cannot access emoji reactions; Spotify documents no reaction endpoint.

## Writing to Spotify: user-only approval

Spotify API gateway in Jacques already supports playlist & player writes.
The GitHub queue separates any write from auto-execution:
- Post an owner-authored Issue with an exact operation, path and body.
- For existing-playlist modifications also include `expected_snapshot_id`
  from a fresh playlist.get read (stale snapshots cause failure).
- Open GitHub Actions > Jacques Spotify Command Queue > Run workflow,
  choose mode `preview` and enter the issue number. It validates without
  making a Spotify write and posts a SHA-256 plan digest.
- Inspect the full Issue body; manually rerun with mode `execute`,
  approval `APPROVE_WRITE`, and the exact `plan_digest`.
- Completed Issues are not replayed; ambiguous failure after a possible
  write still requires checking the Spotify state before any retry.

The Mac mini worker NEVER auto-dispatches writes.

Example write Issue body (replace placeholders with real values):
```json
{
  "version": 1,
  "target": "spotify",
  "operation": "playlist.add",
  "path_values": {"playlist_id": "PLAYLIST_ID"},
  "body": {"uris": ["spotify:track:TRACK_ID"]},
  "expected_snapshot_id": "CURRENT_SNAPSHOT_ID"
}
```

The queue has **no arbitrary code execution**, no remote shell, and no
implicit Spotify OAuth scope escalation. Existing Mac-only commands are
limited to `mac.health` and `mac.db_counts`. To create a Mac diagnostic
Issue use `{"version":1,"target":"mac","operation":"mac.health"}`.

## Testing & limitations

    PYTHONPATH=src python -m unittest discover -s tests -p test_jacques_queue.py -v

Live OAuth checks and the Mac worker's LaunchAgent must be tested in their
respective environments; GitHub commits alone do not prove those integrations
are active. Spotify's rate limits and GitHub Actions usage quotas still apply.
Do not publish private Spotify response data in this public repository.
