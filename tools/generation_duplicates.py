from __future__ import annotations

import argparse
import sqlite3


def main():
    p = argparse.ArgumentParser(description="Find duplicate generated audio by SHA-256")
    p.add_argument("--db", default="db/music.db")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT fingerprint_sha256, COUNT(*) AS count,
               GROUP_CONCAT(output_id) AS output_ids
        FROM generation_outputs
        WHERE fingerprint_sha256 IS NOT NULL AND fingerprint_sha256 != ''
        GROUP BY fingerprint_sha256
        HAVING COUNT(*) > 1
        ORDER BY count DESC, fingerprint_sha256
    """).fetchall()
    conn.close()

    data = [dict(r) for r in rows]
    if args.json:
        import json
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("=== JACQUES GENERATION DUPLICATES ===")
        if not data:
            print("No duplicate fingerprints.")
        for r in data:
            print(f"{r['count']} outputs: {r['output_ids']} sha256={r['fingerprint_sha256']}")


if __name__ == "__main__":
    main()
