from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description="Jacques generation health report")
    p.add_argument("--db", default="db/music.db")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    jobs = [dict(r) for r in conn.execute("""
        SELECT provider, status, COUNT(*) AS count
        FROM generation_jobs
        GROUP BY provider, status
        ORDER BY provider, status
    """)]
    outputs = conn.execute("SELECT COUNT(*) FROM generation_outputs").fetchone()[0]
    analyzed = conn.execute("""
        SELECT COUNT(*) FROM generation_outputs
        WHERE analysis_json IS NOT NULL AND analysis_json != ''
           OR output_id IN (
               SELECT DISTINCT output_id
               FROM generation_analysis
           )
    """).fetchone()[0]
    cache_entries = conn.execute(
        "SELECT COUNT(*) FROM audio_analysis_cache"
    ).fetchone()[0]
    relations = conn.execute(
        "SELECT COUNT(*) FROM generation_relations"
    ).fetchone()[0]
    duplicate_groups = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT fingerprint_sha256
            FROM generation_outputs
            WHERE fingerprint_sha256 IS NOT NULL AND fingerprint_sha256 != ''
            GROUP BY fingerprint_sha256
            HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    fingerprinted = conn.execute("""
        SELECT COUNT(*) FROM generation_outputs
        WHERE fingerprint_sha256 IS NOT NULL AND fingerprint_sha256 != ''
    """).fetchone()[0]
    missing_files = 0
    for r in conn.execute("SELECT audio_path FROM generation_outputs WHERE audio_path IS NOT NULL"):
        if not Path(r[0]).expanduser().exists():
            missing_files += 1

    report = {
        "jobs": jobs,
        "outputs": outputs,
        "analyzed_outputs": analyzed,
        "fingerprinted_outputs": fingerprinted,
        "analysis_cache_entries": cache_entries,
        "relations": relations,
        "duplicate_fingerprint_groups": duplicate_groups,
        "missing_local_files": missing_files,
    }
    conn.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=== JACQUES GENERATION HEALTH ===")
        print(f"outputs             : {outputs}")
        print(f"analyzed_outputs    : {analyzed}")
        print(f"fingerprinted       : {fingerprinted}")
        print(f"analysis_cache      : {cache_entries}")
        print(f"relations           : {relations}")
        print(f"duplicate_groups    : {duplicate_groups}")
        print(f"missing_local_files : {missing_files}")
        for row in jobs:
            print(f"{row['provider']:14} {row['status']:12} {row['count']}")


if __name__ == "__main__":
    main()
