from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from music_db.database import get_connection, initialize_database
from music_db.generation.fingerprint import attach_fingerprint


def main():
    p = argparse.ArgumentParser(description="Fingerprint a Jacques generation output")
    p.add_argument("--output-id", type=int, required=True)
    p.add_argument("--audio", required=True)
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        fp = attach_fingerprint(conn, args.output_id, args.audio)
    print(fp["sha256"])


if __name__ == "__main__":
    main()
