import sqlite3

from tools.analyze_top_tracks import _record_provider_status, _youtube_candidate_score


def test_generic_title_still_matches_exact_artist_channel():
    score = _youtube_candidate_score(
        "너",
        ["ycs"],
        180000,
        {
            "title": "너 — SYSTEM SEOUL",
            "channel": "ycs",
            "duration": 180,
        },
    )
    assert score > 0.45


def test_live_variant_is_penalized_when_track_is_not_live():
    score = _youtube_candidate_score(
        "TURiSTA",
        ["Bad Bunny"],
        180000,
        {
            "title": "TURiSTA (Live)",
            "channel": "Random Channel",
            "duration": 180,
        },
    )
    assert score < 0.45


def test_duration_match_adds_signal():
    short = _youtube_candidate_score(
        "TURiSTA",
        ["Bad Bunny"],
        180000,
        {
            "title": "TURiSTA",
            "channel": "Bad Bunny",
            "duration": 300,
        },
    )
    close = _youtube_candidate_score(
        "TURiSTA",
        ["Bad Bunny"],
        180000,
        {
            "title": "TURiSTA",
            "channel": "Bad Bunny",
            "duration": 182,
        },
    )
    assert close > short


def test_unmatched_youtube_video_is_persisted_as_provider_not_found():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE harmony_provider_runs (
            track_id TEXT, provider TEXT, source_url TEXT, matched_video_url TEXT,
            submitted_at TEXT, completed_at TEXT, status TEXT,
            raw_result_available INTEGER, normalized_segment_count INTEGER,
            error_type TEXT, error_message TEXT, parser_version TEXT,
            confidence REAL, raw_result_json TEXT
        )
        """
    )

    result = _record_provider_status(
        conn,
        "track-1",
        "chordidentifier",
        "not_found",
        error_type="YouTubeMatchError",
        error_message="No strong YouTube candidate.",
    )

    row = conn.execute(
        "SELECT status, raw_result_available, normalized_segment_count, error_type "
        "FROM harmony_provider_runs"
    ).fetchone()
    assert row == ("not_found", 0, 0, "YouTubeMatchError")
    assert result["matched_video_url"] is None
    assert result["source_url"] == (
        "https://chordidentifier.com/chord-finder-from-youtube/"
    )
    conn.close()
