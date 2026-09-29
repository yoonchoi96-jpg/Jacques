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
from music_db.generation.outputs import create_output, extract_audio_refs, download_audio_ref
from music_db.generation.projects import create_project, stage_dir, STAGES
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
    p.add_argument("--download-dir", default=str(ROOT / "generated_audio"), help="Legacy output directory when --project is omitted")
    p.add_argument("--project", default=None, help="Project title/key; enables projects/<project>/{reference,generations,edits,final}")
    p.add_argument("--stage", choices=STAGES, default="generations")
    p.add_argument("--timeout", type=int, default=1800)
    args = p.parse_args()

    initialize_database()
    conn = get_connection()

    project_id = None
    download_dir = Path(args.download_dir)
    if args.project:
        project_id, project_root = create_project(
            conn, args.project, reference_track_id=args.reference_track_id
        )
        download_dir = stage_dir(conn, project_id, args.stage)
        print(f"project_id={project_id}")
        print(f"project_root={project_root}")
        print(f"project_stage={args.stage}")

    job_id = create_generation_job(
        conn, args.provider, model=args.model, prompt=args.prompt,
        lyrics=args.lyrics, reference_track_id=args.reference_track_id,
        parent_job_id=args.parent_job_id,
        project_id=project_id,
        request=vars(args),
    )
    print(f"job_id={job_id}")

    if args.dry_run:
        print("dry_run=true")
        conn.close()
        return

    try:
        if args.provider == "mureka":
            from music_db.generation.mureka import submit
            response = submit(
                conn, job_id, lyrics=args.lyrics, prompt=args.prompt,
                model=args.model or "auto",
            )
            update_generation_job(conn, job_id, status="succeeded",
                                  response=response, completed=True)
        else:
            from music_db.generation.ace_step import submit, poll
            response = submit(
                conn, job_id, prompt=args.prompt, lyrics=args.lyrics,
                model=args.model or "acestep-v15-turbo",
            )
            task_id = (response.get("data") or response).get("task_id")
            if not task_id:
                raise RuntimeError("ACE-Step response had no task_id")
            result = poll(task_id, timeout_seconds=args.timeout)
            update_generation_job(
                conn, job_id, status="succeeded",
                response=result, completed=True,
            )
            response = result
    except Exception as exc:
        update_generation_job(conn, job_id, status="failed",
                              error=exc, completed=True)
        raise

    refs = extract_audio_refs(response)
    for i, ref in enumerate(refs):
        local = download_audio_ref(
            ref, download_dir,
            filename=f"job_{job_id:06d}_output_{i:02d}" + Path(ref.split("?", 1)[0]).suffix,
        )
        output_id = create_output(
            conn, job_id, output_index=i,
            audio_path=local,
            audio_url=None if local else ref,
            project_id=project_id,
            stage=args.stage,
        )
        if local:
            try:
                analysis = analyze_and_store(conn, output_id, local)
                print(json.dumps({"output_id": output_id, "analysis": analysis},
                                 ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f"analysis_failed output_id={output_id}: {exc}", file=sys.stderr)

    for i, audio in enumerate(args.audio):
        output_id = create_output(conn, job_id, output_index=len(refs) + i,
                                  audio_path=str(Path(audio).expanduser().resolve()),
                                  project_id=project_id,
                                  stage=args.stage)
        analysis = analyze_and_store(conn, output_id, audio)
        print(json.dumps(analysis, ensure_ascii=False, indent=2))

    print(json.dumps(response, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
