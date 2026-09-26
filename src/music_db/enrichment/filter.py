from __future__ import annotations

import sqlite3


def get_enrichment_candidates(conn: sqlite3.Connection):
    """
    Enrichment 대상:
      - 2회 이상 재생
      - 또는 1회 재생 + saved
    단,
      - play_history가 없는 곡은 현재 대상에서 제외
      - 이미 필요한 enrichment가 완료된 곡은 source worker에서 skip
    """

    rows = conn.execute(
        """
        SELECT
            t.track_id,
            t.title,
            t.album,
            t.isrc,
            t.saved,
            t.duration_ms,
            COUNT(ph.track_id) AS play_count
        FROM tracks t
        JOIN play_history ph
          ON ph.track_id = t.track_id
        GROUP BY
            t.track_id,
            t.title,
            t.album,
            t.isrc,
            t.saved,
            t.duration_ms
        HAVING
            COUNT(ph.track_id) >= 2
            OR (
                COUNT(ph.track_id) = 1
                AND COALESCE(t.saved, 0) = 1
            )
        ORDER BY t.title
        """
    ).fetchall()

    return rows


def classify_track(row):
    play_count = row["play_count"]
    saved = bool(row["saved"])

    if play_count >= 2:
        return "multi_play"

    if play_count == 1 and saved:
        return "single_play_saved"

    return "skip"
