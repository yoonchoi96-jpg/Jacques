#!/usr/bin/env python3
"""Run the complete local Jacques audio-analysis stack for one track."""
from __future__ import annotations

import argparse
import json

from music_db.database import get_connection, initialize_database
from music_db.harmony import analyze_track_audio, fuse_track_harmony
from music_db.analysis_audio import analyze_and_store_scale_melody
from music_db.production import analyze_and_store_production


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("track_id")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--stage", default="master")
    args = parser.parse_args()

    initialize_database()
    with get_connection() as conn:
        harmony_sources = analyze_track_audio(conn, args.track_id, args.audio)
        harmony = fuse_track_harmony(conn, args.track_id)

    production = analyze_and_store_production(
        args.track_id, args.audio, stage=args.stage
    )
    scale_melody = analyze_and_store_scale_melody(args.track_id, args.audio)

    result = {
        "track_id": args.track_id,
        "audio": args.audio,
        "harmony_sources": [x.get("method") for x in harmony_sources],
        "harmony_consensus_segments": len((harmony or {}).get("segments", [])),
        "production_evidence_id": production["evidence_id"],
        "scale_melody_evidence_id": scale_melody["evidence_id"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
