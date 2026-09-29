from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from music_db.database import initialize_database
from music_db.external.billboard import sync as sync_billboard
from music_db.external.pitchfork import sync as sync_pitchfork
from music_db.external.apple_music import sync_catalog as sync_apple
from music_db.external.discogs import sync as sync_discogs

DB = ROOT / "db/music.db"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    initialize_database()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    print("=== JACQUES EDITORIAL SYNC ===")
    print("Billboard :", sync_billboard(conn, dry_run=args.dry_run))
    print("Pitchfork :", sync_pitchfork(conn, dry_run=args.dry_run))
    print("AppleMusic:", sync_apple(conn, dry_run=args.dry_run))
    print("Discogs   :", sync_discogs(conn, dry_run=args.dry_run))
    conn.close()

if __name__ == "__main__":
    main()
