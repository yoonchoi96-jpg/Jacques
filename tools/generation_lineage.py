from __future__ import annotations

import argparse
import json
import sqlite3


def main():
    p = argparse.ArgumentParser(description="Inspect Jacques generation lineage")
    p.add_argument("--db", default="db/music.db")
    p.add_argument("--output-id", type=int)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    where = "WHERE r.from_output_id=? OR r.to_output_id=?" if args.output_id else ""
    params = (args.output_id, args.output_id) if args.output_id else ()

    rows = conn.execute(f"""
        SELECT r.relation_id, r.relation_type, r.confidence, r.note,
               r.from_output_id, r.to_output_id,
               jf.provider AS from_provider, jf.job_id AS from_job,
               jt.provider AS to_provider, jt.job_id AS to_job,
               r.created_at
        FROM generation_relations r
        JOIN generation_outputs ofr ON ofr.output_id=r.from_output_id
        JOIN generation_jobs jf ON jf.job_id=ofr.job_id
        JOIN generation_outputs otr ON otr.output_id=r.to_output_id
        JOIN generation_jobs jt ON jt.job_id=otr.job_id
        {where}
        ORDER BY r.created_at, r.relation_id
    """, params).fetchall()
    conn.close()

    data = [dict(r) for r in rows]
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    print("=== JACQUES GENERATION LINEAGE ===")
    for r in data:
        conf = "" if r["confidence"] is None else f" confidence={r['confidence']}"
        note = "" if not r["note"] else f" note={r['note']}"
        print(
            f"output {r['from_output_id']} ({r['from_provider']}/job {r['from_job']}) "
            f"--{r['relation_type']}--> "
            f"output {r['to_output_id']} ({r['to_provider']}/job {r['to_job']})"
            f"{conf}{note}"
        )
    print(f"relations={len(data)}")


if __name__ == "__main__":
    main()
