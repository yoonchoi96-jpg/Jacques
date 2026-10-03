"""Ingest locally owned/accessible audio into Jacques and run analysis.

The ingest layer intentionally does not download copyrighted audio. It scans a
user-configured local folder, matches files to Spotify tracks by ISRC first and
then conservative title/artist matching, and runs the persisted analysis stack.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg", ".opus"}

def _norm(value: str | None) -> str:
    value = (value or "").lower()
    value = re.sub(r"[([{].*?[)]}]", " ", value)
    value = re.sub(r"[^a-z0-9가-힣]+", " ", value)
    return re.sub(r"s+", " ", value).strip()

def _fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def _metadata(path: Path) -> dict[str, str]:
    try:
        from mutagen import File
        audio = File(path, easy=True)
        if audio:
            def one(key):
                value = audio.get(key)
                return str(value[0]) if value else ""
            return {
                "title": one("title"),
                "artist": one("artist"),
                "album": one("album"),
                "isrc": one("isrc"),
            }
    except Exception:
        pass
    return {"title": "", "artist": "", "album": "", "isrc": ""}

def match_file(conn, path: Path) -> tuple[str | None, str]:
    meta = _metadata(path)
    if meta["isrc"]:
        row = conn.execute(
            "SELECT track_id FROM tracks WHERE lower(isrc)=lower(?) LIMIT 1",
            (meta["isrc"],),
        ).fetchone()
        if row:
            return row[0], "isrc"

    stem = _norm(path.stem)
    rows = conn.execute(
        """SELECT t.track_id, t.title,
                  GROUP_CONCAT(a.name, ' ') AS artists
           FROM tracks t
           LEFT JOIN track_artists ta ON ta.track_id=t.track_id
           LEFT JOIN artists a ON a.artist_id=ta.artist_id
           GROUP BY t.track_id"""
    ).fetchall()

    candidates = []
    for row in rows:
        title = _norm(row["title"] if hasattr(row, "keys") else row[1])
        artists = _norm(row["artists"] if hasattr(row, "keys") else row[2])
        if not title or title not in stem:
            continue
        score = 0.7
        if artists and artists in stem:
            score += 0.25
        candidates.append((score, row["track_id"] if hasattr(row, "keys") else row[0]))
    candidates.sort(reverse=True)
    if len(candidates) == 1 and candidates[0][0] >= 0.7:
        return candidates[0][1], "filename"
    if candidates and candidates[0][0] >= 0.95 and (
        len(candidates) == 1 or candidates[0][0] > candidates[1][0]
    ):
        return candidates[0][1], "filename"
    return None, "unmatched"

def ingest_audio_folder(audio_root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    from .database import get_connection, initialize_database
    from .production import analyze_and_store_production
    from .analysis_audio import analyze_and_store_scale_melody
    from .harmony import analyze_track_audio, fuse_track_harmony

    initialize_database()
    root = Path(audio_root).expanduser()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    result = {"scanned": len(files), "matched": 0, "analyzed": 0, "unmatched": [], "errors": []}

    with get_connection() as conn:
        for path in files:
            track_id, method = match_file(conn, path)
            if not track_id:
                result["unmatched"].append(str(path))
                continue
            result["matched"] += 1
            if dry_run:
                continue
            try:
                fingerprint = _fingerprint(path)
                conn.execute(
                    """INSERT INTO music_analysis_evidence
                       (track_id, domain, source, source_type, method, version,
                        payload_json, confidence, observed_at, created_at)
                       VALUES (?, 'audio_file', 'local_library', 'local',
                               'audio_ingest', 'audio_ingest_v1', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                    (
                        track_id,
                        json.dumps({
                            "path": str(path),
                            "fingerprint_sha256": fingerprint,
                            "match_method": method,
                        }, ensure_ascii=False),
                        1.0 if method == "isrc" else 0.85,
                    ),
                )
                conn.commit()

                # Harmony is allowed to fail independently; the production
                # and melodic measurements remain useful if chord transcription
                # dependencies/models are unavailable.
                try:
                    harmony_sources = analyze_track_audio(conn, track_id, str(path))
                    fuse_track_harmony(conn, track_id)
                except Exception as exc:
                    harmony_sources = []
                    result["errors"].append({
                        "file": str(path), "stage": "harmony",
                        "error": f"{type(exc).__name__}: {exc}",
                    })

                analyze_and_store_scale_melody(track_id, path)
                analyze_and_store_production(track_id, path, stage="master")
                result["analyzed"] += 1
            except Exception as exc:
                result["errors"].append({
                    "file": str(path), "stage": "analysis",
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-root", default=os.getenv("JACQUES_AUDIO_ROOT", "~/Music/Jacques"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(ingest_audio_folder(args.audio_root, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
