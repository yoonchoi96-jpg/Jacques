from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _artist_names(conn, track_id: str) -> str:
    rows = conn.execute(
        """
        SELECT a.name
        FROM track_artists ta
        JOIN artists a ON a.artist_id = ta.artist_id
        WHERE ta.track_id=?
        ORDER BY ta.artist_order
        """,
        (track_id,),
    ).fetchall()
    return ", ".join(row[0] for row in rows)


def _profile(features: dict) -> list[str]:
    tags = []
    bpm = _num(features.get("tempo"))
    energy = _num(features.get("energy"))
    dance = _num(features.get("danceability"))
    loudness = _num(features.get("loudness"))
    valence = _num(features.get("valence"))
    acoustic = _num(features.get("acousticness"))
    instrumental = _num(features.get("instrumentalness"))

    if bpm is not None:
        if bpm >= 150:
            tags.append("high-tempo / double-time potential")
        elif bpm >= 125:
            tags.append("dancefloor tempo")
        elif bpm <= 85:
            tags.append("slow / half-time feel")
        else:
            tags.append("mid-tempo pocket")

    if energy is not None:
        tags.append("high energy" if energy >= 0.72 else "controlled energy" if energy <= 0.48 else "moderate energy")

    if dance is not None:
        tags.append("strong rhythmic drive" if dance >= 0.72 else "looser groove" if dance <= 0.45 else "balanced groove")

    if loudness is not None:
        tags.append("dense mastered level" if loudness >= -7 else "moderate mastered level")

    if valence is not None:
        tags.append("bright affect" if valence >= 0.65 else "dark affect" if valence <= 0.35 else "mixed affect")

    if acoustic is not None and acoustic >= 0.55:
        tags.append("acoustic timbral component")

    if instrumental is not None and instrumental >= 0.35:
        tags.append("instrumental-forward texture")

    return tags


def _production_dna(features: dict, genres: list[str]) -> dict:
    tags = _profile(features)
    genre_text = " / ".join(genres[:5])

    bpm = _num(features.get("tempo"))
    energy = _num(features.get("energy"))
    dance = _num(features.get("danceability"))

    if bpm is not None and energy is not None and dance is not None:
        if 85 <= bpm <= 105 and energy >= 0.85 and dance >= 0.65:
            archetype = "mid-tempo pop impact / contrast-driven"
        elif 105 <= bpm <= 130 and dance >= 0.72 and energy < 0.72:
            archetype = "groove-led / rhythm-forward hybrid"
        elif bpm >= 135 and energy >= 0.75 and dance >= 0.68:
            archetype = "high-tempo / impact-driven"
        elif energy >= 0.72 and dance >= 0.65:
            archetype = "rhythm-first / impact-driven"
        else:
            archetype = "hybrid / context-dependent"
    elif "high energy" in tags and "strong rhythmic drive" in tags:
        archetype = "rhythm-first / impact-driven"
    elif "dark affect" in tags and "controlled energy" in tags:
        archetype = "dark / tension-led"
    elif "bright affect" in tags and "dancefloor tempo" in tags:
        archetype = "bright / dance-oriented"
    else:
        archetype = "hybrid / context-dependent"

    return {
        "archetype": archetype,
        "tags": tags,
        "genre_context": genre_text,
        "generation_use": (
            "Preserve the rhythmic pocket and energy contour; "
            "change melody, lyrics, and identifiable musical material."
        ),
    }


def analyze_track(conn, chart_item: dict) -> dict:
    track_id = chart_item["track_id"]

    track = conn.execute(
        "SELECT * FROM tracks WHERE track_id=?",
        (track_id,),
    ).fetchone()

    features = conn.execute(
        "SELECT * FROM audio_features WHERE track_id=?",
        (track_id,),
    ).fetchone()

    genres = [
        row[0]
        for row in conn.execute(
            """
            SELECT g.name
            FROM track_genres tg
            JOIN genres g ON g.genre_id=tg.genre_id
            WHERE tg.track_id=?
            ORDER BY g.name
            """,
            (track_id,),
        ).fetchall()
    ]

    payload = {
        "track_id": track_id,
        "title": chart_item["title"],
        "artist": chart_item["artist"],
        "rank": chart_item["rank"],
        "streams": chart_item.get("streams"),
        "previous_rank": chart_item.get("previous_rank"),
        "peak_rank": chart_item.get("peak_rank"),
        "days_on_chart": chart_item.get("days_on_chart"),
        "spotify_uri": chart_item.get("spotify_uri"),
        "audio": dict(features) if features else None,
        "genres": genres,
    }

    if features:
        payload["production_dna"] = _production_dna(dict(features), genres)
    else:
        payload["production_dna"] = {
            "archetype": "insufficient audio-feature data",
            "tags": [],
            "genre_context": " / ".join(genres[:5]),
            "generation_use": "Await canonical audio enrichment before production inference.",
        }

    if track:
        payload["album"] = track["album"]
        payload["release_date"] = track["release_date"]
        payload["spotify_url"] = track["spotify_url"]

    return payload


def upsert_chart(conn, chart: dict, limit: int = 200) -> None:
    for item in chart["items"][:limit]:
        conn.execute(
            """
            INSERT INTO chart_entries
              (source, chart_name, chart_date, rank, previous_rank, peak_rank,
               weeks_on_chart, days_on_chart, title, artist_name, track_id, source_url, raw_data, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(source, chart_name, chart_date, rank) DO UPDATE SET
              previous_rank=excluded.previous_rank,
              peak_rank=excluded.peak_rank,
              weeks_on_chart=excluded.weeks_on_chart,
              days_on_chart=excluded.days_on_chart,
              title=excluded.title,
              artist_name=excluded.artist_name,
              track_id=excluded.track_id,
              source_url=excluded.source_url,
              raw_data=excluded.raw_data,
              observed_at=excluded.observed_at
            """,
            (
                chart["source"],
                chart["chart_name"],
                chart["chart_date"],
                item["rank"],
                item.get("previous_rank"),
                item.get("peak_rank"),
                item.get("weeks_on_chart"),
                item.get("days_on_chart"),
                item["title"],
                item["artist"],
                item["track_id"],
                chart["url"],
                json.dumps(item, ensure_ascii=False),
            ),
        )


def render_report(chart: dict, analyses: list[dict]) -> str:
    lines = [
        f"# Jacques Global Top {len(analyses)} — {chart['chart_date']}",
        "",
        f"Source: Spotify Global Daily Chart ({chart['chart_date']})",
        "",
    ]

    for item in analyses:
        lines.extend(
            [
                f"## #{item['rank']} — {item['title']} — {item['artist']}",
                "",
                f"- Streams: {item.get('streams'):,}" if item.get("streams") is not None else "- Streams: unavailable",
                f"- Previous rank: {item.get('previous_rank')}",
                f"- Peak rank: {item.get('peak_rank')}",
                f"- Days on chart: {item.get('days_on_chart')}",
            ]
        )

        audio = item.get("audio")
        if audio:
            lines.extend(
                [
                    "",
                    "### Audio profile",
                    f"- BPM: {audio.get('tempo')}",
                    f"- Key: {audio.get('key')}",
                    f"- Loudness: {audio.get('loudness')}",
                    f"- Energy: {audio.get('energy')}",
                    f"- Danceability: {audio.get('danceability')}",
                    f"- Valence: {audio.get('valence')}",
                    f"- Acousticness: {audio.get('acousticness')}",
                    f"- Instrumentalness: {audio.get('instrumentalness')}",
                ]
            )

        dna = item["production_dna"]
        lines.extend(
            [
                "",
                "### Production DNA",
                f"- Archetype: {dna['archetype']}",
                f"- Signals: {', '.join(dna['tags']) or 'insufficient signals'}",
                f"- Genre context: {dna['genre_context'] or 'not available'}",
                f"- Generation use: {dna['generation_use']}",
                "",
            ]
        )

    lines.extend(
        [
            "---",
            "",
            "Jacques rule: chart position is market context, not a production-quality score.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(root: Path, chart: dict, analyses: list[dict]) -> Path:
    report_dir = root / "reports" / "discovery"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"global_top_{len(analyses)}_{chart['chart_date']}.md"
    path.write_text(render_report(chart, analyses), encoding="utf-8")
    return path


def store_analysis(conn, chart: dict, analyses: list[dict]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_analyses (
            analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            chart_name TEXT NOT NULL,
            chart_date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            track_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source, chart_name, chart_date, rank, track_id)
        )
        """
    )
    for item in analyses:
        conn.execute(
            """
            INSERT INTO discovery_analyses
              (source, chart_name, chart_date, rank, track_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, chart_name, chart_date, rank, track_id)
            DO UPDATE SET payload_json=excluded.payload_json, created_at=excluded.created_at
            """,
            (
                chart["source"],
                chart["chart_name"],
                chart["chart_date"],
                item["rank"],
                item["track_id"],
                json.dumps(item, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
