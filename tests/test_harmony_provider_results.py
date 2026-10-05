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


def test_completed_magic_response_with_unusable_raw_segments_is_not_empty_success():
    _, state = normalize_provider_result(
        "magic_chords",
        {
            "status": "success",
            "harmony_payload": {
                "status": "completed",
                "raw_segment_count": 2,
                "invalid_segment_count": 2,
                "segments": [],
            },
        },
    )

    assert state["status"] == "invalid_result"
    assert state["raw_result_available"] is True


def test_execution_timeout_overrides_empty_completed_payload():
    _, state = normalize_provider_result(
        "magic_chords",
        {
            "status": "poll_timeout",
            "harmony_payload": {
                "status": "completed",
                "raw_segment_count": 0,
                "segments": [],
            },
        },
    )

    assert state["status"] == "timeout"


def test_empty_unrecognized_payload_is_not_reported_as_raw_data():
    _, state = normalize_provider_result(
        "methodic_truth",
        {"status": "invalid_result", "harmony_payload": {"status": "success"}},
    )

    assert state["status"] == "invalid_result"
    assert state["raw_result_available"] is False


def test_inconsistent_raw_and_invalid_counts_are_rejected():
    _, state = normalize_provider_result(
        "magic_chords",
        {
            "status": "success",
            "harmony_payload": {
                "status": "completed",
                "raw_segment_count": 1,
                "invalid_segment_count": 2,
                "segments": [],
            },
        },
    )

    assert state["status"] == "invalid_result"
    assert state["error_type"] == "InvalidTimelineMetadata"


def test_non_object_provider_result_is_rejected():
    _, state = normalize_provider_result("magic_chords", ["not", "an", "object"])

    assert state["status"] == "invalid_result"
    assert state["error_type"] == "InvalidProviderResult"


def test_invalid_provider_confidence_is_rejected():
    _, state = normalize_provider_result(
        "magic_chords",
        {
            "status": "success",
            "harmony_payload": {
                "status": "completed",
                "confidence": 1.4,
                "segments": [{"start_sec": 0, "end_sec": 4, "chord": "C"}],
            },
        },
    )

    assert state["status"] == "invalid_result"
    assert state["error_type"] == "InvalidConfidence"
