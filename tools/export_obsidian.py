from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path


def safe_filename(value):
    value = re.sub(r'[\\/:*?"<>|]', "_", str(value))
    return value.strip()[:180] or "Untitled"


def wiki(value):
    return f"[[{safe_filename(value)}]]"


def get_artists(conn, track_id):
    return conn.execute(
        """
        SELECT a.name
        FROM track_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.track_id = ?
        ORDER BY ta.artist_order, a.name
        """,
        (track_id,),
    ).fetchall()


def export_tracks(conn, root, limit=None):
    rows = conn.execute(
        """
        SELECT
            t.*,
            a.name AS album_name,
            a.spotify_id AS album_spotify_id
        FROM tracks t
        LEFT JOIN track_albums ta ON ta.track_id = t.track_id
        LEFT JOIN albums a ON a.album_id = ta.album_id
        ORDER BY COALESCE(t.last_played, t.saved_at, '') DESC, t.title
        """
        + (" LIMIT ?" if limit else ""),
        ((limit,) if limit else ()),
    ).fetchall()

    out = root / "Tracks"
    out.mkdir(parents=True, exist_ok=True)

    for row in rows:
        artists = [x[0] for x in get_artists(conn, row["track_id"]) if x[0]]
        lines = [
            "---",
            f'track_id: "{row["track_id"]}"',
            f'title: "{row["title"].replace(chr(34), chr(92)+chr(34))}"',
            f"spotify_url: {row['spotify_url'] or ''}",
            f"saved: {bool(row['saved'])}",
            f"last_played: {row['last_played'] or ''}",
            f"play_count: {conn.execute('SELECT COUNT(*) FROM play_history WHERE track_id = ?', (row['track_id'],)).fetchone()[0]}",
            "---",
            "",
            f"# {row['title']}",
            "",
            "## Artists",
            "",
        ]
        lines += [f"- {wiki(a)}" for a in artists] or ["- None"]
        if row["album_name"]:
            lines += ["", "## Album", "", f"- {wiki(row['album_name'])}"]
        lines += [
            "",
            "## Audio",
            "",
            f"- Duration: {row['duration_ms'] or ''} ms",
            f"- ISRC: {row['isrc'] or ''}",
            f"- Spotify: {row['spotify_url'] or ''}",
            "",
            "## Relations",
            "",
        ]
        relations = conn.execute(
            """
            SELECT r.relation_type, t.title
            FROM track_relations r
            JOIN tracks t ON t.track_id = r.related_track_id
            WHERE r.track_id = ?
            ORDER BY r.relation_type, t.title
            """,
            (row["track_id"],),
        ).fetchall()
        lines += [f"- {r[0]} → {wiki(r[1])}" for r in relations] or ["- None"]

        incoming = conn.execute(
            """
            SELECT r.relation_type, t.title
            FROM track_relations r
            JOIN tracks t ON t.track_id = r.track_id
            WHERE r.related_track_id = ?
            ORDER BY r.relation_type, t.title
            """,
            (row["track_id"],),
        ).fetchall()
        lines += [
            "",
            "## Incoming Relations",
            "",
        ]
        lines += [f"- {r[0]} ← {wiki(r[1])}" for r in incoming] or ["- None"]

        features = conn.execute(
            """
            SELECT tempo, key, mode, loudness, energy, danceability,
                   valence, acousticness, instrumentalness, speechiness, source
            FROM audio_features
            WHERE track_id = ?
            """,
            (row["track_id"],),
        ).fetchone()
        lines += [
            "",
            "## Audio Features",
            "",
        ]
        if features:
            labels = [
                ("BPM", "tempo"), ("Key", "key"), ("Mode", "mode"),
                ("Loudness", "loudness"), ("Energy", "energy"),
                ("Danceability", "danceability"), ("Valence", "valence"),
                ("Acousticness", "acousticness"),
                ("Instrumentalness", "instrumentalness"),
                ("Speechiness", "speechiness"), ("Source", "source"),
            ]
            lines += [
                f"- {label}: {features[field] if features[field] is not None else ''}"
                for label, field in labels
            ]
        else:
            lines.append("- None")

        credits = conn.execute(
            """
            SELECT person_name, role, source, confidence
            FROM track_credits
            WHERE track_id = ?
            ORDER BY role, person_name
            """,
            (row["track_id"],),
        ).fetchall()
        lines += ["", "## Credits", ""]
        lines += [
            f"- {c[0]} — {c[1]} ({c[2]})"
            for c in credits
        ] or ["- None"]

        harmony_profile = conn.execute(
            """SELECT key, mode, harmonic_rhythm, chord_change_rate,
                      loop_bars, progression_json, consensus_confidence
               FROM harmony_profiles
               WHERE track_id=?""",
            (row["track_id"],),
        ).fetchone()
        harmony_rows = conn.execute(
            """SELECT start_sec, end_sec, root, bass_note, chord_quality,
                      inversion, roman_candidate, function_candidate,
                      secondary_function, borrowed_from_mode, confidence
               FROM harmony_structures
               WHERE track_id=?
               ORDER BY start_sec""",
            (row["track_id"],),
        ).fetchall()
        lines += ["", "## Harmony", ""]
        if harmony_profile:
            lines += [
                f"- Key: {harmony_profile['key'] or ''}",
                f"- Mode: {harmony_profile['mode'] or ''}",
                f"- Harmonic rhythm: {harmony_profile['harmonic_rhythm'] or ''} sec",
                f"- Chord change rate: {harmony_profile['chord_change_rate'] or ''} / sec",
                f"- Loop estimate: {harmony_profile['loop_bars'] or ''} bars",
                f"- Consensus confidence: {harmony_profile['consensus_confidence'] or ''}",
                f"- Progression: {harmony_profile['progression_json'] or '[]'}",
            ]
        else:
            lines.append("- No normalized harmony profile")
        if harmony_rows:
            lines += ["", "### Harmony Timeline", ""]
            for h in harmony_rows:
                label = (h["root"] or "") + (h["chord_quality"] or "")
                if h["bass_note"] and h["bass_note"] != h["root"]:
                    label += f"/{h['bass_note']}"
                details = []
                if h["inversion"]:
                    details.append(h["inversion"])
                if h["roman_candidate"]:
                    details.append(h["roman_candidate"])
                if h["function_candidate"]:
                    details.append(h["function_candidate"])
                if h["secondary_function"]:
                    details.append(h["secondary_function"])
                if h["borrowed_from_mode"]:
                    details.append(f"borrowed:{h['borrowed_from_mode']}")
                suffix = f" — {', '.join(details)}" if details else ""
                lines.append(
                    f"- {h['start_sec']:.2f}–{h['end_sec']:.2f}s: **{label or 'Unknown'}** "
                    f"(confidence {h['confidence'] if h['confidence'] is not None else ''}){suffix}"
                )

        melody_rows = conn.execute(
            """SELECT section_name, start_sec, end_sec, key_candidate,
                      mode_candidate, scale_candidate, range_semitones,
                      contour, chord_tone_ratio, chromaticism, confidence
               FROM melody_analysis
               WHERE track_id=?
               ORDER BY start_sec""",
            (row["track_id"],),
        ).fetchall()
        scale_rows = conn.execute(
            """SELECT section_name, start_sec, end_sec, tonic, mode, scale,
                      modal_interchange, modulation_from, modulation_to, confidence
               FROM scale_mode_analysis
               WHERE track_id=?
               ORDER BY start_sec""",
            (row["track_id"],),
        ).fetchall()
        lines += ["", "## Melody & Scale", ""]
        if melody_rows:
            for m in melody_rows:
                lines.append(
                    f"- {m['start_sec'] if m['start_sec'] is not None else ''}–"
                    f"{m['end_sec'] if m['end_sec'] is not None else ''}s "
                    f"{m['section_name'] or ''}: key={m['key_candidate'] or ''}, "
                    f"mode={m['mode_candidate'] or ''}, scale={m['scale_candidate'] or ''}, "
                    f"range={m['range_semitones'] or ''} st, "
                    f"chord-tone-ratio={m['chord_tone_ratio'] or ''}, "
                    f"chromaticism={m['chromaticism'] or ''}, "
                    f"confidence={m['confidence'] if m['confidence'] is not None else ''}"
                )
        else:
            lines.append("- No melody analysis yet")
        if scale_rows:
            lines += ["", "### Scale / Modulation", ""]
            for s in scale_rows:
                flags = []
                if s["modal_interchange"]:
                    flags.append("modal interchange")
                if s["modulation_from"] or s["modulation_to"]:
                    flags.append(
                        f"modulation {s['modulation_from'] or '?'} → {s['modulation_to'] or '?'}"
                    )
                suffix = f" — {', '.join(flags)}" if flags else ""
                lines.append(
                    f"- {s['start_sec'] if s['start_sec'] is not None else ''}–"
                    f"{s['end_sec'] if s['end_sec'] is not None else ''}s "
                    f"{s['section_name'] or ''}: tonic={s['tonic'] or ''}, "
                    f"mode={s['mode'] or ''}, scale={s['scale'] or ''}, "
                    f"confidence={s['confidence'] if s['confidence'] is not None else ''}{suffix}"
                )

        production_rows = conn.execute(
            """SELECT stage, source, start_sec, end_sec, peak_dbfs,
                      true_peak_dbtp, rms_dbfs, lufs_integrated,
                      loudness_range_lu, vu_average, crest_factor_db,
                      dynamic_range_db, phase_correlation, stereo_width,
                      mono_compatibility, clipping_samples, confidence
               FROM production_analysis
               WHERE track_id=?
               ORDER BY stage, start_sec""",
            (row["track_id"],),
        ).fetchall()
        lines += ["", "## Production Analysis", ""]
        if production_rows:
            for p in production_rows:
                lines.append(
                    f"- {p['stage']} / {p['source']} "
                    f"{p['start_sec'] if p['start_sec'] is not None else ''}–"
                    f"{p['end_sec'] if p['end_sec'] is not None else ''}s: "
                    f"LUFS-I={p['lufs_integrated'] if p['lufs_integrated'] is not None else ''}, "
                    f"TP={p['true_peak_dbtp'] if p['true_peak_dbtp'] is not None else ''} dBTP, "
                    f"RMS={p['rms_dbfs'] if p['rms_dbfs'] is not None else ''} dBFS, "
                    f"crest={p['crest_factor_db'] if p['crest_factor_db'] is not None else ''} dB, "
                    f"dynamic={p['dynamic_range_db'] if p['dynamic_range_db'] is not None else ''} dB, "
                    f"phase={p['phase_correlation'] if p['phase_correlation'] is not None else ''}, "
                    f"stereo={p['stereo_width'] if p['stereo_width'] is not None else ''}, "
                    f"mono={p['mono_compatibility'] if p['mono_compatibility'] is not None else ''}, "
                    f"clips={p['clipping_samples'] if p['clipping_samples'] is not None else ''}, "
                    f"confidence={p['confidence'] if p['confidence'] is not None else ''}"
                )
        else:
            lines.append("- No production analysis yet")

        (out / f"{safe_filename(row['title'])}__{row['track_id'][:8]}.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    return len(rows)


def export_artists(conn, root):
    rows = conn.execute(
        "SELECT * FROM artists ORDER BY name"
    ).fetchall()
    out = root / "Artists"
    out.mkdir(parents=True, exist_ok=True)

    for row in rows:
        tracks = conn.execute(
            """
            SELECT t.title
            FROM track_artists ta
            JOIN tracks t ON t.track_id = ta.track_id
            WHERE ta.artist_id = ?
            ORDER BY t.title
            """,
            (row["artist_id"],),
        ).fetchall()

        lines = [
            "---",
            f'spotify_id: "{row["spotify_id"] or ""}"',
            f'genres: "{row["genres"] or ""}"',
            "---",
            "",
            f"# {row['name']}",
            "",
            "## Tracks",
            "",
        ]
        lines += [f"- {wiki(t[0])}" for t in tracks] or ["- None"]
        (out / f"{safe_filename(row['name'])}__{row['artist_id']}.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    return len(rows)


def export_albums(conn, root):
    rows = conn.execute(
        "SELECT * FROM albums ORDER BY name"
    ).fetchall()
    out = root / "Albums"
    out.mkdir(parents=True, exist_ok=True)

    for row in rows:
        tracks = conn.execute(
            """
            SELECT t.title
            FROM track_albums ta
            JOIN tracks t ON t.track_id = ta.track_id
            WHERE ta.album_id = ?
            ORDER BY t.title
            """,
            (row["album_id"],),
        ).fetchall()

        lines = [
            "---",
            f'spotify_id: "{row["spotify_id"] or ""}"',
            f'release_date: "{row["release_date"] or ""}"',
            f'album_type: "{row["album_type"] or ""}"',
            "---",
            "",
            f"# {row['name']}",
            "",
            "## Tracks",
            "",
        ]
        lines += [f"- {wiki(t[0])}" for t in tracks] or ["- None"]
        (out / f"{safe_filename(row['name'])}__{row['album_id']}.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    return len(rows)



def export_editorial(conn, root):
    out = root / "Editorial"
    out.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT source, chart_name, chart_date, rank, title, artist_name,
                  album_name, track_id, source_url
           FROM chart_entries
           ORDER BY chart_date DESC, source, chart_name, rank
           LIMIT 500"""
    ).fetchall()
    (out / "Chart History.md").write_text(
        "\n".join([
            "# Chart History", "",
            *[f"- {r['chart_date']} — **#{r['rank']}** {r['title']} — {r['artist_name'] or ''} "
              f"({r['source']} / {r['chart_name']})" for r in rows],
        ]) + "\n", encoding="utf-8")
    reviews = conn.execute(
        """SELECT source, title, artist_name, album_name, score, review_date,
                  author, headline, url, track_id
           FROM review_entries
           ORDER BY review_date DESC, source
           LIMIT 500"""
    ).fetchall()
    (out / "Reviews.md").write_text(
        "\n".join([
            "# Editorial Reviews", "",
            *[f"- {r['review_date'] or ''} — **{r['score'] if r['score'] is not None else ''}** "
              f"{r['artist_name'] or ''} — {r['album_name'] or r['title'] or ''} "
              f"— {r['author'] or ''} — {r['url']}" for r in reviews],
        ]) + "\n", encoding="utf-8")
    return len(rows), len(reviews)


def export_generation_assets(conn, root):
    out = root / "Generation" / "Projects"
    out.mkdir(parents=True, exist_ok=True)
    projects = conn.execute("SELECT * FROM generation_projects ORDER BY title").fetchall()
    for p in projects:
        assets = conn.execute("SELECT asset_id, asset_type, title, audio_path, fingerprint_sha256, created_at FROM generation_assets WHERE project_id=? ORDER BY asset_type, created_at", (p["project_id"],)).fetchall()
        lines = ["---", f'project_id: "{p["project_id"]}"', f'project_key: "{p["project_key"]}"', f'title: "{p["title"].replace(chr(34), chr(92)+chr(34))}"', f'root_path: "{p["root_path"].replace(chr(34), chr(92)+chr(34))}"', "---", "", f"# {p['title']}", "", "## Assets", ""]
        lines += [f"- **{a['asset_type']}** — {a['title'] or ''} — `{a['audio_path']}` — `{a['fingerprint_sha256'] or ''}`" for a in assets] or ["- None"]
        notes = conn.execute(
            "SELECT note_type, body, created_at FROM generation_project_notes WHERE project_id=? ORDER BY created_at",
            (p["project_id"],),
        ).fetchall()
        lines += ["", "## Production Notes", ""]
        lines += [f"- **{n['note_type']}** — {n['body']}" for n in notes] or ["- None"]
        lines += ["", "## Generation Outputs", ""]
        outputs = conn.execute("SELECT output_id, stage, audio_path, fingerprint_sha256, created_at FROM generation_outputs WHERE project_id=? ORDER BY created_at", (p["project_id"],)).fetchall()
        lines += [f"- **{o['stage']}** — output #{o['output_id']} — `{o['audio_path'] or ''}` — `{o['fingerprint_sha256'] or ''}`" for o in outputs] or ["- None"]
        (out / f"{safe_filename(p['title'])}__{p['project_id']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(projects)

def export_generation(conn, root):
    out = root / "Generation"
    out.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """SELECT j.*, p.title AS project_title, p.root_path AS project_root,
                  o.output_id, o.output_index, o.audio_path, o.audio_url,
                  o.duration_seconds AS output_duration, o.bpm AS output_bpm,
                  o.key_scale AS output_key, o.analysis_json, o.fingerprint_sha256,
                  o.stage AS output_stage
           FROM generation_jobs j
           LEFT JOIN generation_outputs o ON o.job_id = j.job_id
           LEFT JOIN generation_projects p ON p.project_id = COALESCE(o.project_id, j.project_id)
           ORDER BY j.created_at DESC, o.output_index"""
    ).fetchall()
    lines = ["# Generation Lineage", ""]
    for r in rows:
        lines += [
            f"## Job {r['job_id']} — {r['provider']} / {r['model'] or ''}",
            "",
            f"- Status: {r['status']}",
            f"- Project: {r['project_title'] or 'None'}",
            f"- Project root: {r['project_root'] or 'None'}",
            f"- Stage: {r['output_stage'] or 'generation'}",
            f"- Reference track: {wiki(r['reference_track_id']) if r['reference_track_id'] else 'None'}",
            f"- Parent job: {r['parent_job_id'] or 'None'}",
            f"- Prompt: {r['prompt'] or ''}",
            f"- BPM: {r['output_bpm'] or r['bpm'] or ''}",
            f"- Key: {r['output_key'] or r['key_scale'] or ''}",
            f"- Output: {r['audio_path'] or r['audio_url'] or 'None'}",
            f"- SHA-256: {r['fingerprint_sha256'] or ''}",
            "",
        ]
        relations = conn.execute(
            """SELECT relation_type, from_output_id, to_output_id, confidence, note
               FROM generation_relations
               WHERE from_output_id=? OR to_output_id=?
               ORDER BY created_at""",
            (r["output_id"], r["output_id"]),
        ).fetchall() if r["output_id"] else []
        if relations:
            lines += ["### Output Relations", ""]
            lines += [
                f"- {x['from_output_id']} --{x['relation_type']}--> {x['to_output_id']}"
                + (f" (confidence {x['confidence']})" if x["confidence"] is not None else "")
                + (f" — {x['note']}" if x["note"] else "")
                for x in relations
            ]
            lines.append("")
    (out / "Generation Lineage.md").write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Export Jacques SQLite to Obsidian")
    parser.add_argument("--db", default="db/music.db")
    parser.add_argument("--vault", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    root = Path(args.vault) / "Music"
    root.mkdir(parents=True, exist_ok=True)

    chart_rows, review_rows = export_editorial(conn, root)
    counts = {
        "tracks": export_tracks(conn, root, args.limit),
        "artists": export_artists(conn, root),
        "albums": export_albums(conn, root),
        "chart_rows": chart_rows,
        "review_rows": review_rows,
        "generation_rows": export_generation(conn, root),
        "generation_projects": export_generation_assets(conn, root),
    }

    conn.close()

    print("=== Jacques → Obsidian ===")
    for key, value in counts.items():
        print(f"{key:8}: {value}")
    print(f"vault    : {root}")


if __name__ == "__main__":
    main()
