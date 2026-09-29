from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone


def main():
    p = argparse.ArgumentParser(description="Jacques source/enrichment health report")
    p.add_argument("--db", default="db/music.db")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    report = {"sources": [], "generation": [], "runs": [], "records": []}
    rows = conn.execute("""
        SELECT sr.source, sr.enabled, sr.priority, sr.role,
               COUNT(es.track_id) AS status_rows,
               SUM(CASE WHEN es.status='success' THEN 1 ELSE 0 END) AS success,
               SUM(CASE WHEN es.status='not_found' THEN 1 ELSE 0 END) AS not_found,
               SUM(CASE WHEN es.status='error' THEN 1 ELSE 0 END) AS errors
        FROM source_registry sr
        LEFT JOIN enrichment_status es ON es.source=sr.source
        GROUP BY sr.source
        ORDER BY sr.priority
    """).fetchall()
    for r in rows:
        report["sources"].append(dict(r))

    for r in conn.execute("""
        SELECT provider, status, COUNT(*) AS n
        FROM generation_jobs GROUP BY provider, status
        ORDER BY provider, status
    """):
        report["generation"].append(dict(r))

    for r in conn.execute("""
        SELECT source, status, COUNT(*) AS count,
               MAX(finished_at) AS last_finished_at
        FROM enrichment_runs
        GROUP BY source, status
        ORDER BY source, status
    """):
        report["runs"].append(dict(r))

    for r in conn.execute("""
        SELECT source, COUNT(*) AS records,
               MAX(observed_at) AS last_observed_at
        FROM source_records
        GROUP BY source
        ORDER BY source
    """):
        report["records"].append(dict(r))

    if args.json:
        import json
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=== JACQUES SOURCE HEALTH ===")
        for r in report["sources"]:
            print(f"{r['source']:14} enabled={r['enabled']} role={r['role'] or '':22} rows={r['status_rows'] or 0} success={r['success'] or 0} not_found={r['not_found'] or 0} errors={r['errors'] or 0}")
        print("\n=== ENRICHMENT RUNS ===")
        for r in report["runs"]:
            print(f"{r['source']:14} {r['status']:12} {r['count']} last={r['last_finished_at'] or '-'}")
        print("\n=== SOURCE RECORDS ===")
        for r in report["records"]:
            print(f"{r['source']:14} records={r['records']} last={r['last_observed_at'] or '-'}")
        print("\n=== GENERATION ===")
        for r in report["generation"]:
            print(f"{r['provider']:14} {r['status']:12} {r['n']}")

    conn.close()


if __name__ == "__main__":
    main()
