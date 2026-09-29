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

    def enabled(source):
        row = conn.execute(
            "SELECT enabled FROM source_registry WHERE source=?",
            (source,),
        ).fetchone()
        return bool(row and row[0])

    def run(source, label, worker):
        if not enabled(source):
            result = {"status": "disabled", "source": source}
            print(f"{label}: {result}")
            return result
        result = worker(conn, dry_run=args.dry_run)
        print(f"{label}: {result}")
        return result

    print("=== JACQUES EDITORIAL SYNC ===")
    run("billboard", "Billboard ", sync_billboard)
    run("pitchfork", "Pitchfork ", sync_pitchfork)
    run("apple_music", "AppleMusic", sync_apple)
    run("discogs", "Discogs   ", sync_discogs)
    conn.close()

if __name__ == "__main__":
    main()
