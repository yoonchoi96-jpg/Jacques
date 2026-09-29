from __future__ import annotations

import argparse
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from music_db.database import initialize_database
from music_db.enrichment.filter import (
    classify_track,
    get_enrichment_candidates,
)
from music_db.enrichment.merge import get_preferred_audio_features
from music_db.enrichment.sources import (
    freqblog,
    lastfm,
    musicbrainz,
    songbpm,
)

DB_PATH = "db/music.db"


def print_header(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def print_stats(stats):
    for key, value in stats.items():
        print(f"{key:24}: {value}")


def open_worker_connection():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row

    # 여러 source worker가 동시에 SQLite에 접근하므로
    # writer 충돌을 줄이고 잠시 기다리도록 설정한다.
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def _record_enrichment_run(conn, source, started_at, finished_at, candidate_count, stats, status, error=None):
    conn.execute(
        """
        INSERT INTO enrichment_runs (
            source, started_at, finished_at, candidate_count,
            success_count, no_data_count, not_found_count,
            error_count, status, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source.lower().replace(".", ""),
            started_at,
            finished_at,
            candidate_count,
            int(stats.get("success", 0)),
            int(stats.get("no_data", stats.get("no_match", 0))),
            int(stats.get("not_found", 0)),
            int(
                stats.get("error", 0)
                + stats.get("search_failed", 0)
                + stats.get("retryable", 0)
                + stats.get("rate_limit", 0)
                + stats.get("timeout", 0)
            ),
            status,
            error,
        ),
    )
    conn.commit()


def _source_registry_key(name):
    return name.lower().replace(".", "").replace("-", "_").replace(" ", "_")


def _source_enabled(conn, name):
    key = _source_registry_key(name)
    row = conn.execute(
        "SELECT enabled FROM source_registry WHERE source = ? LIMIT 1",
        (key,),
    ).fetchone()
    # Unknown sources remain runnable for backwards compatibility; the
    # registry only gates sources that are explicitly registered.
    return row is None or bool(row["enabled"])


def run_source(name, worker, candidates, dry_run):
    conn = open_worker_connection()

    if not _source_enabled(conn, name):
        print(f"[{name}] SKIP | source disabled in registry")
        conn.close()
        return {
            "name": name,
            "stats": {"skipped_disabled": len(candidates)},
            "error": None,
            "elapsed_seconds": 0.0,
            "skipped": True,
        }

    print(f"[{name}] START")

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        stats = worker(
            conn,
            candidates,
            dry_run=dry_run,
        )

        elapsed = time.perf_counter() - started
        finished_at = datetime.now(timezone.utc).isoformat()

        if not dry_run:
            _record_enrichment_run(
                conn,
                name,
                started_at,
                finished_at,
                len(candidates),
                stats,
                "success",
            )

        print(f"[{name}] DONE | {elapsed:.3f}s")

        return {
            "name": name,
            "stats": stats,
            "error": None,
            "elapsed_seconds": elapsed,
        }

    except Exception as exc:
        elapsed = time.perf_counter() - started
        finished_at = datetime.now(timezone.utc).isoformat()

        if not dry_run:
            _record_enrichment_run(
                conn,
                name,
                started_at,
                finished_at,
                len(candidates),
                {},
                "error",
                error=f"{type(exc).__name__}: {exc}",
            )

        print(
            f"[{name}] ERROR | "
            f"{type(exc).__name__}: {exc} | "
            f"{elapsed:.3f}s"
        )
        traceback.print_exc()

        return {
            "name": name,
            "stats": {},
            "error": exc,
            "elapsed_seconds": elapsed,
            "skipped": False,
        }

    finally:
        conn.close()


def run_parallel_sources(candidates, dry_run):
    """
    Source order is intentional:

      1. FreqBlog primary
      2. Last.fm + MusicBrainz in parallel
      3. SongBPM fallback after FreqBlog has completed

    This prevents SongBPM from waking up for tracks that already have
    a usable FreqBlog result.
    """
    results = {}

    print_header("PRIMARY SOURCE ENRICHMENT")

    freqblog_result = run_source(
        "FREQBLOG",
        freqblog.enrich,
        candidates,
        dry_run,
    )
    results["FREQBLOG"] = freqblog_result

    print_header("SECONDARY SOURCE ENRICHMENT")

    secondary_workers = {
        "LAST.FM": lastfm.enrich,
        "MUSICBRAINZ": musicbrainz.enrich,
    }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                run_source,
                name,
                worker,
                candidates,
                dry_run,
            ): name
            for name, worker in secondary_workers.items()
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as exc:
                results[name] = {
                    "name": name,
                    "stats": {},
                    "error": exc,
                    "elapsed_seconds": 0,
                }

    print_header("FALLBACK SOURCE ENRICHMENT")

    results["SONGBPM"] = run_source(
        "SONGBPM",
        songbpm.enrich,
        candidates,
        dry_run,
    )

    return results


def print_source_summaries(results):
    order = [
        "FREQBLOG",
        "SONGBPM",
        "LAST.FM",
        "MUSICBRAINZ",
    ]

    for name in order:
        result = results.get(name)

        if result is None:
            continue

        print_header(f"{name} SUMMARY")

        if result["error"] is not None:
            print(
                f"WORKER ERROR: "
                f"{type(result['error']).__name__}: "
                f"{result['error']}"
            )
        else:
            print_stats(result["stats"])
            print(f"ELAPSED: {result['elapsed_seconds']:.3f}s")


def run_merge(candidates):
    print_header("MERGE")

    conn = open_worker_connection()

    try:
        merged = 0
        freqblog_primary = 0
        songbpm_primary = 0
        no_audio = 0

        for track in candidates:
            features = get_preferred_audio_features(
                conn,
                track["track_id"],
            )

            if features is None:
                no_audio += 1
                continue

            merged += 1

            if features["primary_source"] == "freqblog":
                freqblog_primary += 1
            elif features["primary_source"] == "songbpm":
                songbpm_primary += 1

        print(f"tracks merged          : {merged}")
        print(f"FreqBlog primary       : {freqblog_primary}")
        print(f"SongBPM fallback       : {songbpm_primary}")
        print(f"no audio feature       : {no_audio}")

        return {
            "merged": merged,
            "freqblog_primary": freqblog_primary,
            "songbpm_primary": songbpm_primary,
            "no_audio": no_audio,
        }

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Spotify Music DB enrichment engine"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 API 요청/DB 변경 없이 대상만 계산",
    )

    args = parser.parse_args()

    initialize_database()

    # Candidate selection은 main thread에서 한 번만 한다.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        print_header("MUSIC ENRICHMENT ENGINE")

        candidates = get_enrichment_candidates(conn)

        print(
            f"Enrichment candidates: {len(candidates)}"
        )

        if not candidates:
            print("No enrichment candidates.")
            return

        multi_play = 0
        single_saved = 0

        for row in candidates:
            kind = classify_track(row)

            if kind == "multi_play":
                multi_play += 1
            elif kind == "single_play_saved":
                single_saved += 1

        print(f"  2+ plays          : {multi_play}")
        print(f"  1 play + saved    : {single_saved}")

    finally:
        conn.close()

    # ==================================================
    # PARALLEL SOURCES
    # ==================================================

    results = run_parallel_sources(
        candidates,
        dry_run=args.dry_run,
    )

    print_source_summaries(results)

    # ==================================================
    # FINAL MERGE
    # ==================================================

    merge_stats = run_merge(candidates)

    # ==================================================
    # FINAL
    # ==================================================

    print_header("ENGINE SUMMARY")

    print(f"Candidates            : {len(candidates)}")
    print(f"Dry run               : {args.dry_run}")
    print(f"Sources                : 4")
    print(f"Parallel secondary workers: 2")
    print(f"FreqBlog primary       : {merge_stats['freqblog_primary']}")
    print(f"SongBPM fallback       : {merge_stats['songbpm_primary']}")
    print(f"No audio feature       : {merge_stats['no_audio']}")

    if args.dry_run:
        print()
        print(
            "DRY RUN — "
            "NO API DATA WRITTEN / NO DATABASE CHANGES"
        )


if __name__ == "__main__":
    main()
