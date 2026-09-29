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
           LEFT JOIN generation_outputs o ON o.job_id = j.job_id
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
    }

    conn.close()

    print("=== Jacques → Obsidian ===")
    for key, value in counts.items():
        print(f"{key:8}: {value}")
    print(f"vault    : {root}")


if __name__ == "__main__":
    main()
