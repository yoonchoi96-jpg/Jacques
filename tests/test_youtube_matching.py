from tools.analyze_top_tracks import _youtube_candidate_score


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
