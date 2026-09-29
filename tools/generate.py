from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from music_db.database import initialize_database, get_connection
from music_db.generation.base import create_generation_job, update_generation_job
from music_db.generation.outputs import create_output, extract_audio_refs, find_local_audio_refs
from music_db.generation.audio_analysis import analyze_and_store


def main():
    p = argparse.ArgumentParser(description="Jacques generation runner")
    p.add_argument("--provider", choices=["mureka", "ace_step"], required=True)
    p.add_argument("--prompt", default="")
    p.add_argument("--lyrics", default="")
    p.add_argument("--model", default=None)
    p.add_argument("--reference-track-id", default=None)
    p.add_argument("--parent-job-id", type=int, default=None)
    p.add_argument("--audio", action="append", default=[],
                   help="Existing local audio to attach/analyze after generation")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    initialize_database()
    conn = get_connection()

    job_id = create_generation_job(
        conn, args.provider, model=args.model, prompt=args.prompt,
        lyrics=args.lyrics, reference_track_id=args.reference_track_id,
        parent_job_id=args.parent_job_id,
        request=vars(args),
    )
    print(f"job_id={job_id}")

    if args.dry_run:
        print("dry_run=true")
        conn.close()
        return

    if args.provider == "mureka":
        from music_db.generation.mureka import submit
        response = submit(
            conn, job_id, lyrics=args.lyrics, prompt=args.prompt,
            model=args.model or "auto",
        )
    else:
        from music_db.generation.ace_step import submit, poll
        response = submit(
            conn, job_id, prompt=args.prompt, lyrics=args.lyrics,
            model=args.model or "acestep-v15-turbo",
        )
        task_id = (response.get("data") or response).get("task_id")
        if task_id:
            result = poll(task_id)
            update_generation_job(
                conn, job_id, status="succeeded",
                response=result, completed=True,
            )
            response = result
        else:
            update_generation_job(conn, job_id, status="failed",
                                  error="ACE-Step response had no task_id",
                                  completed=True)

    refs = extract_audio_refs(response)
    for i, ref in enumerate(refs):
        local = ref.replace("file://", "")
        create_output(
            conn, job_id, output_index=i,
            audio_path=local if Path(local).exists() else None,
            audio_url=None if Path(local).exists() else ref,
        )

    for i, audio in enumerate(args.audio):
        output_id = create_output(conn, job_id, output_index=len(refs) + i,
                                  audio_path=str(Path(audio).expanduser().resolve()))
        analysis = analyze_and_store(conn, output_id, audio)
        print(json.dumps(analysis, ensure_ascii=False, indent=2))

    print(json.dumps(response, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
