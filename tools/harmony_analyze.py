from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from music_db.database import initialize_database
from music_db.harmony import analyze_track_audio, import_external_harmony, fuse_track_harmony

def main():
    parser = argparse.ArgumentParser(description="Jacques multi-source harmony analysis")
    parser.add_argument("track_id")
    parser.add_argument("--audio", help="Path to legally available local audio")
    parser.add_argument("--external-json", action="append", help="Normalized external harmony JSON")
    args = parser.parse_args()

    initialize_database()
    conn = sqlite3.connect(ROOT / "db" / "music.db")
    conn.row_factory = sqlite3.Row
    try:
        if args.audio:
            print(f"[Jacques Harmony] audio={args.audio}")
            analyze_track_audio(conn, args.track_id, args.audio)
        for path in args.external_json or []:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            count = import_external_harmony(conn, args.track_id, payload)
            print(f"[Jacques Harmony] external={payload.get('source')} segments={count}")
        profile = fuse_track_harmony(conn, args.track_id)
        if profile is None:
            print("[Jacques Harmony] no harmony evidence")
            return 0
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
