# NAVER Dictionary Wordbook Sync

## Architecture

GitHub Actions
  -> self-hosted runner on the user's Mac
  -> persistent Playwright profile
  -> NAVER Dictionary wordbook
  -> raw snapshots + SQLite

The NAVER password is never stored in the repository or GitHub secrets.

## One-time bootstrap

Run locally on the Mac:

```bash
python scripts/naver_wordbook_sync.py --bootstrap
```

Log into NAVER manually in the opened browser and confirm the wordbook is visible.

The persistent browser profile is stored outside the repository:

`~/.naver_wordbook/browser_profile`

## Scheduled sync

After the Mac is configured as a GitHub self-hosted runner with labels:

`self-hosted, macOS, naver-wordbook`

GitHub Actions can run the sync on schedule.

## Important

The current parser intentionally stores raw card text first. Do not treat the
SQLite rows as a final schema until the current NAVER DOM/API structure has
been inspected. The next step is to identify the current XHR/fetch response
or stable DOM fields and replace the raw-card parser with structured fields.
