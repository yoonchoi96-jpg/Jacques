from __future__ import annotations

import argparse
import sqlite3


def main():
    p = argparse.ArgumentParser(description="Jacques source/enrichment health report")
    p.add_argument("--db", default="db/music.db")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    print("=== JACQUES SOURCE HEALTH ===")
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
        print(
            f"{r['source']:14} enabled={r['enabled']} role={r['role'] or '':22} "
            f"rows={r['status_rows'] or 0} success={r['success'] or 0} "
            f"not_found={r['not_found'] or 0} errors={r['errors'] or 0}"
        )

    print("\n=== GENERATION ===")
    for r in conn.execute("""
        SELECT provider, status, COUNT(*) AS n
        FROM generation_jobs GROUP BY provider, status
        ORDER BY provider, status
    """):
        print(f"{r['provider']:14} {r['status']:12} {r['n']}")

    conn.close()


if __name__ == "__main__":
    main()
