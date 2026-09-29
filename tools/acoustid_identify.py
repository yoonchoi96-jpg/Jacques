from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from music_db.database import initialize_database, get_connection
from music_db.external.acoustid import identify_track


def main():
    p = argparse.ArgumentParser(description="Identify local audio with AcoustID")
    p.add_argument("--track-id", required=True)
    p.add_argument("audio")
    args = p.parse_args()

    initialize_database()
    conn = get_connection()
    result = identify_track(conn, args.track_id, args.audio)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
