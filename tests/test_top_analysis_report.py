from tools.build_top_analysis_report import make_report, markdown


def test_report_distinguishes_provisional_evidence_from_consensus():
    report = make_report(
        {
            "track_id": "track-1",
            "youtube_url": "https://youtube.com/watch?v=abcdefghijk",
            "results": [
                {
                    "provider": "chordidentifier",
                    "status": "invalid_result",
                    "normalized_segment_count": 0,
                    "error_type": "InvalidSegments",
                },
                {
                    "provider": "methodic_truth",
                    "status": "success_with_data",
                    "normalized_segment_count": 1,
                    "source_url": "https://methodictruth.com/song-analyzer",
                    "youtube_url": "https://youtube.com/watch?v=abcdefghijk",
                    "submitted_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:01:00Z",
                    "raw_result_available": True,
                    "harmony_payload": {"confidence": None, "parser_version": "parser-v1"},
                },
            ],
            "harmony_profile": {
                "analysis_status": "provisional_insufficient_evidence",
                "key": "A",
                "mode": "Minor",
                "progression": [],
                "provisional_progression": ["Am7"],
                "provisional_segments": [
                    {
                        "chord": "Am7",
                        "source": "methodic_truth",
                        "evidence_class": "provider_derived_evidence",
                        "fallback_reason": "Only one independent provider.",
                    }
                ],
                "consensus_policy": {
                    "fusion_mode": "provisional_provider_baseline",
                    "strict_consensus_segment_count": 0,
                    "strict_consensus_coverage": 0.0,
                },
                "confidence": None,
                "qc_issues": [],
            },
        },
        1,
    )

    output = markdown(report)
    assert report["harmony"]["actual_sources"] == ["methodic_truth"]
    assert report["harmony"]["provisional_provider_fallback_used"] is True
    assert report["providers"][1]["source_url"] == (
        "https://methodictruth.com/song-analyzer"
    )
    assert report["providers"][1]["matched_video_url"] == (
        "https://youtube.com/watch?v=abcdefghijk"
    )
    assert report["harmony"]["evidence_classes"] == [
        "provider_derived_evidence"
    ]
    assert "Analysis status: provisional_insufficient_evidence" in output
    assert "Provisional-only progression (not consensus): Am7" in output
    assert "chordidentifier=invalid_result (0 segments)" in output
