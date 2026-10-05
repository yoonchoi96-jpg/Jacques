from music_db.harmony.provider_results import normalize_provider_result


def test_provider_success_requires_valid_normalized_segments():
    result, state = normalize_provider_result(
        "chordidentifier",
        {
            "status": "success",
            "harmony_payload": {
                "status": "success",
                "segments": [
                    {"start_sec": 0, "end_sec": 4, "chord": "Am7"},
                    {"start_sec": 4, "end_sec": 8, "chord": "C"},
                ],
            },
        },
        duration_sec=10,
    )

    assert state["status"] == "success_with_data"
    assert len(state["segments"]) == 2
    assert result["harmony_payload"]["segment_count"] == 2


def test_completed_empty_result_is_not_provider_failure():
    _, state = normalize_provider_result(
        "methodic_truth",
        {
            "status": "success",
            "harmony_payload": {"status": "success", "segments": []},
        },
    )

    assert state["status"] == "success_empty"


def test_provider_timeout_is_explicit_without_data():
    _, state = normalize_provider_result(
        "magic_chords",
        {"status": "poll_timeout", "error_type": "MagicChordsTimeout"},
    )

    assert state["status"] == "timeout"
    assert state["error_type"] == "MagicChordsTimeout"


def test_provider_parse_failure_is_explicit():
    _, state = normalize_provider_result(
        "chordidentifier",
        {
            "status": "invalid_result",
            "error_type": "ChordIdentifierParseError",
            "error": "No recognized timeline was found.",
        },
    )

    assert state["status"] == "invalid_result"
    assert state["error_type"] == "ChordIdentifierParseError"


def test_malformed_provider_timeline_is_rejected():
    _, state = normalize_provider_result(
        "chordidentifier",
        {
            "status": "success",
            "harmony_payload": {
                "status": "success",
                "segments": [{"start_sec": 0, "end_sec": 4, "chord": "Hmaj7"}],
            },
        },
    )

    assert state["status"] == "invalid_result"
    assert state["invalid_segment_count"] == 1
    assert state["segments"] == []


def test_provider_count_metadata_must_be_valid():
    _, state = normalize_provider_result(
        "chordidentifier",
        {
            "status": "success",
            "harmony_payload": {
                "status": "success",
                "raw_segment_count": "many",
                "segments": [],
            },
        },
    )

    assert state["status"] == "invalid_result"
    assert state["error_type"] == "InvalidTimelineMetadata"
