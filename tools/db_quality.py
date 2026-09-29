from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DB_PATH = PROJECT_ROOT / "db/music.db"


def now():
    return datetime.now(timezone.utc).isoformat()


def add_issue(conn, entity_type, entity_id, issue_type, severity, details):
    conn.execute(
        """
        INSERT INTO data_quality_issues
            (entity_type, entity_id, issue_type, severity, details, detected_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(entity_type, entity_id, issue_type)
        DO UPDATE SET
            severity = excluded.severity,
            details = excluded.details,
            detected_at = excluded.detected_at,
            resolved_at = NULL
        """,
        (entity_type, str(entity_id), issue_type, severity, details, now()),
    )


def run_checks(conn):
    checks = []

    # Mark the previous findings from this validator as resolved first.
    # check() below re-opens any issue that is still present. This keeps
    # data_quality_issues useful as a current-state audit trail instead of
    # accumulating stale unresolved rows forever.
    issue_types = (
        "missing_track_artist_link",
        "orphan_track_artist",
        "orphan_track_album",
        "invalid_duration",
        "invalid_audio_range",
        "generation_relation_self_link",
        "generation_orphan_output",
        "generation_orphan_analysis",
        "generation_duplicate_fingerprint",
        "relation_self_link",
        "source_without_canonical_audio",
        "audio_without_source",
    )
    placeholders = ",".join("?" for _ in issue_types)
    conn.execute(
        f"""
        UPDATE data_quality_issues
        SET resolved_at = ?
        WHERE resolved_at IS NULL
          AND issue_type IN ({placeholders})
        """,
        (now(), *issue_types),
    )

    def check(name, sql, severity="error"):
        rows = conn.execute(sql).fetchall()
        for row in rows:
            add_issue(
                conn,
                row[0],
                row[1],
                name,
                severity,
                row[2],
            )
        checks.append((name, len(rows)))
        return len(rows)

    check(
        "missing_track_artist_link",
        """
        SELECT 'track', t.track_id,
               'Track has no normalized track_artists relation; Spotify repair may be unavailable'
        FROM tracks t
        WHERE NOT EXISTS (
            SELECT 1 FROM track_artists ta
            WHERE ta.track_id = t.track_id
        )
        """,
        severity="warning",
    )

    check(
        "orphan_track_artist",
        """
        SELECT 'track_artist', ta.track_id,
               'track_artists references a missing artist'
        FROM track_artists ta
        LEFT JOIN artists a ON a.artist_id = ta.artist_id
        WHERE a.artist_id IS NULL
        """,
    )

    check(
        "orphan_track_album",
        """
        SELECT 'track_album', ta.track_id,
               'track_albums references a missing album'
        FROM track_albums ta
        LEFT JOIN albums a ON a.album_id = ta.album_id
        WHERE a.album_id IS NULL
        """,
    )

    check(
        "invalid_duration",
        """
        SELECT 'track', track_id,
               'duration_ms is zero or negative'
        FROM tracks
        WHERE duration_ms IS NOT NULL
          AND duration_ms <= 0
        """,
    )

    check(
        "invalid_audio_numeric",
        """
        SELECT 'audio', af.track_id,
               'audio feature contains NaN/Inf or non-finite numeric data'
        FROM audio_features af
        WHERE (typeof(tempo) = 'real' AND (tempo > 1e308 OR tempo < -1e308))
           OR (typeof(loudness) = 'real' AND (loudness > 1e308 OR loudness < -1e308))
           OR (typeof(confidence) = 'real' AND (confidence > 1e308 OR confidence < -1e308))
        """,
    )

    check(
        "invalid_audio_range",
        """
        SELECT 'audio', af.track_id,
               'audio feature is outside expected 0..1 range'
        FROM audio_features af
        WHERE energy IS NOT NULL AND (energy < 0 OR energy > 1)
           OR danceability IS NOT NULL AND (danceability < 0 OR danceability > 1)
           OR valence IS NOT NULL AND (valence < 0 OR valence > 1)
           OR acousticness IS NOT NULL AND (acousticness < 0 OR acousticness > 1)
           OR instrumentalness IS NOT NULL AND (instrumentalness < 0 OR instrumentalness > 1)
           OR speechiness IS NOT NULL AND (speechiness < 0 OR speechiness > 1)
        """,
    )

    check(
        "generation_relation_self_link",
        """
        SELECT 'generation_relation', relation_id,
               'Generation relation points to the same output'
        FROM generation_relations
        WHERE from_output_id = to_output_id
        """,
    )

    check(
        "generation_orphan_output",
        """
        SELECT 'generation_output', o.output_id,
               'Generation output references a missing job'
        FROM generation_outputs o
        LEFT JOIN generation_jobs j ON j.job_id = o.job_id
        WHERE j.job_id IS NULL
        """,
    )

    check(
        "generation_orphan_analysis",
        """
        SELECT 'generation_analysis', a.analysis_id,
               'Generation analysis references a missing output'
        FROM generation_analysis a
        LEFT JOIN generation_outputs o ON o.output_id = a.output_id
        WHERE o.output_id IS NULL
        """,
    )

    check(
        "generation_duplicate_fingerprint",
        """
        SELECT 'generation_output', MIN(output_id),
               'Multiple generation outputs share the same audio fingerprint'
        FROM generation_outputs
        WHERE fingerprint_sha256 IS NOT NULL
          AND fingerprint_sha256 != ''
        GROUP BY fingerprint_sha256
        HAVING COUNT(*) > 1
        """,
        severity="warning",
    )

    check(
        "relation_self_link",
        """
        SELECT 'track_relation', track_id,
               'Track relation points to itself'
        FROM track_relations
        WHERE track_id = related_track_id
        """,
    )

    check(
        "source_without_canonical_audio",
        """
        SELECT 'track', s.track_id,
               'Audio source exists but canonical audio_features is missing'
        FROM audio_feature_sources s
        WHERE NOT EXISTS (
            SELECT 1 FROM audio_features af
            WHERE af.track_id = s.track_id
        )
        GROUP BY s.track_id
        """,
    )

    # A missing FreqBlog row is not automatically an error because
    # terminal not_found is allowed to fall back to SongBPM.
    check(
        "audio_without_source",
        """
        SELECT 'track', t.track_id,
               'Canonical audio_features exists but no source row exists'
        FROM tracks t
        JOIN audio_features af ON af.track_id = t.track_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM audio_feature_sources s
            WHERE s.track_id = t.track_id
        )
        """,
        severity="warning",
    )

    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()

    # initialize_database() owns Jacques' canonical DB path. Avoid
    # silently initializing a different database while validating --db.
    if db_path == DB_PATH.resolve():
        from music_db.database import initialize_database
        initialize_database()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    checks = run_checks(conn)
    conn.commit()

    summary = {
        "database": str(db_path),
        "checked_at": now(),
        "checks": [
            {"name": name, "issues": count}
            for name, count in checks
        ],
        "tracks": conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
        "artists": conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0],
        "albums": conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0],
        "track_artist_links": conn.execute("SELECT COUNT(*) FROM track_artists").fetchone()[0],
        "track_album_links": conn.execute("SELECT COUNT(*) FROM track_albums").fetchone()[0],
        "relations": conn.execute("SELECT COUNT(*) FROM track_relations").fetchone()[0],
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print("JACQUES DATABASE QUALITY")
        print("=" * 64)
        for item in summary["checks"]:
            print(f'{item["name"]:28} : {item["issues"]}')
        print()
        print(f'Tracks                    : {summary["tracks"]}')
        print(f'Artists                   : {summary["artists"]}')
        print(f'Albums                    : {summary["albums"]}')
        print(f'Track/artist links        : {summary["track_artist_links"]}')
        print(f'Track/album links         : {summary["track_album_links"]}')
        print(f'Track relations           : {summary["relations"]}')
        print("=" * 64)

    has_errors = any(
        item["issues"] > 0
        for item in summary["checks"]
        if item["name"] not in ("audio_without_source", "missing_track_artist_link")
    )

    conn.close()

    if args.fail_on_error and has_errors:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
