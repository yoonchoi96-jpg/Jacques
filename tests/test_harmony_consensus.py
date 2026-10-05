import json
import sqlite3
import unittest

from music_db.harmony.provider_results import normalize_provider_result
from src.music_db.harmony.pipeline import (
    _chord_ambiguity,
    _split,
    _validate_harmony_profile,
    fuse_track_harmony,
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE harmony_segments (
            track_id TEXT, source TEXT, section_name TEXT,
            start_sec REAL, end_sec REAL, chord TEXT, confidence REAL
        );
        CREATE TABLE harmony_sources (
            track_id TEXT, key TEXT, confidence REAL
        );
        CREATE TABLE audio_features (track_id TEXT, tempo REAL);
        CREATE TABLE harmony_consensus (
            track_id TEXT, section_name TEXT, start_sec REAL, end_sec REAL,
            chord TEXT, chord_family TEXT, confidence REAL, agreement REAL,
            source_count INTEGER, evidence_json TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE music_analysis_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT, domain TEXT, source TEXT, source_type TEXT,
            method TEXT, version TEXT, start_sec REAL, end_sec REAL,
            payload_json TEXT, confidence REAL, observed_at TEXT, created_at TEXT
        );
        CREATE TABLE harmony_structures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id TEXT, start_sec REAL, end_sec REAL, root TEXT,
            bass_note TEXT, chord_quality TEXT, inversion TEXT,
            upper_structure_root TEXT, upper_structure_quality TEXT,
            upper_structure_notes TEXT, pitch_classes TEXT, roman_candidate TEXT,
            function_candidate TEXT, secondary_function TEXT,
            borrowed_from_mode TEXT, confidence REAL, evidence_json TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE harmony_profiles (
            track_id TEXT PRIMARY KEY, key TEXT, mode TEXT,
            harmonic_rhythm REAL, chord_change_rate REAL, loop_bars REAL,
            progression_json TEXT, sections_json TEXT, extensions_json TEXT,
            bass_motion_json TEXT, consensus_confidence REAL,
            analysis_json TEXT, created_at TEXT, updated_at TEXT
        );
        """
    )
    return conn


def test_am7_and_c6_are_pitch_equivalent_but_not_same_identity():
    assert _split("Am7") == ("A", "m7", None)
    assert _split("C6") == ("C", "6", None)
    assert "C6" in _chord_ambiguity("Am7")
    assert "Am7" in _chord_ambiguity("C6")


def test_consensus_requires_two_sources_and_preserves_competing_label():
    conn = _conn()
    rows = [
        ("t1", "source_a", None, 0.0, 4.0, "Am7", 0.9),
        ("t1", "source_b", None, 0.1, 4.1, "Am7", 0.8),
        ("t1", "source_c", None, 0.0, 4.0, "C6", 0.95),
        ("t1", "source_d", None, 6.0, 10.0, "F", 0.9),
    ]
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.execute("INSERT INTO harmony_sources VALUES ('t1', 'C major', 0.9)")
    conn.commit()

    profile = fuse_track_harmony(conn, "t1")

    assert profile["progression"] == ["Am7"]
    segment = profile["segments"][0]
    assert segment["source_count"] == 2
    assert "C6" in segment["pitch_set_equivalents"]
    assert segment["competing_evidence"][0]["chord"] == "C6"
    assert profile["consensus_policy"]["minimum_agreeing_sources"] == 2


def test_enharmonic_spelling_can_reach_consensus_without_collapsing_source_text():
    conn = _conn()
    rows = [
        ("t2", "source_a", None, 0.0, 4.0, "C#maj7", 0.9),
        ("t2", "source_b", None, 0.1, 4.1, "Dbmaj7", 0.8),
    ]
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.execute("INSERT INTO harmony_sources VALUES ('t2', 'Db major', 0.9)")
    conn.commit()

    profile = fuse_track_harmony(conn, "t2")

    assert profile["progression"] == ["C#maj7"]
    assert profile["segments"][0]["source_count"] == 2
    assert profile["segments"][0]["evidence"][1]["chord"] == "Dbmaj7"


def test_beat_grid_is_available_and_prompt_ready():
    conn = _conn()
    rows = [
        ("t3", "source_a", None, 0.0, 2.0, "Cmaj7", 0.9),
        ("t3", "source_b", None, 0.0, 2.0, "Cmaj7", 0.9),
        ("t3", "source_a", None, 2.0, 4.0, "Am7", 0.9),
        ("t3", "source_b", None, 2.0, 4.0, "Am7", 0.9),
    ]
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.execute("INSERT INTO harmony_sources VALUES ('t3', 'C major', 0.9)")
    conn.execute("INSERT INTO audio_features VALUES ('t3', 120.0)")
    conn.commit()

    profile = fuse_track_harmony(conn, "t3")

    grid = profile["beat_grid"]
    assert grid["available"] is True
    assert grid["time_signature"] == "4/4"
    assert grid["grid_semantics"] == "relative_to_first_consensus_onset_with_onset_snapping"
    assert grid["beats"][0]["bar"] == 1
    assert grid["beats"][0]["beat"] == 1
    assert grid["beats"][0]["chord"] == "Cmaj7"
    assert profile["prompt_harmony"].startswith("TIME: 4/4")
    assert "Bar 01" in profile["prompt_harmony"]


def test_structure_map_is_bar_aware_and_never_invents_semantics():
    conn = _conn()
    rows = [
        ("t4", "source_a", None, 0.0, 4.0, "C", 0.9),
        ("t4", "source_b", None, 0.0, 4.0, "C", 0.9),
        ("t4", "source_a", None, 4.0, 8.0, "F", 0.9),
        ("t4", "source_b", None, 4.0, 8.0, "F", 0.9),
    ]
    conn.executemany("INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    conn.execute("INSERT INTO harmony_sources VALUES ('t4', 'C major', 0.9)")
    conn.execute("INSERT INTO audio_features VALUES ('t4', 120.0)")
    conn.commit()
    profile = fuse_track_harmony(conn, "t4")
    structure = profile["structure_map"]
    assert structure[0]["section"] == "Section 01"
    assert structure[0]["start_bar"] == 1
    assert structure[0]["end_bar"] == 4
    assert structure[0]["semantic_label_evidence"] is False
    assert structure[0]["source"] == "neutral_fallback"
    assert structure[0]["confidence"] is None
    assert structure[0]["evidence_class"] == "fallback_placeholder"
    assert "[Section 01] Bars 01-04" in profile["prompt_harmony"]


def test_single_provider_remains_provisional_without_confidence():
    conn = _conn()
    conn.execute(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("single", "source_a", None, 0.0, 10.0, "Am7", 0.99),
    )
    conn.commit()

    profile = fuse_track_harmony(conn, "single")

    assert profile["analysis_status"] == "provisional_insufficient_evidence"
    assert profile["progression"] == []
    assert profile["segments"] == []
    assert profile["provisional_progression"] == ["Am7"]
    assert profile["confidence"] is None


def test_neutral_fallback_is_never_counted_as_harmony_evidence():
    conn = _conn()
    conn.execute(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("fallback", "neutral_fallback", None, 0.0, 10.0, "C", 1.0),
    )
    conn.commit()

    assert fuse_track_harmony(conn, "fallback") is None


def test_conflicting_providers_do_not_create_strict_consensus():
    conn = _conn()
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("conflict", "source_a", None, 0.0, 10.0, "Am7", 0.9),
            ("conflict", "source_b", None, 0.0, 10.0, "C6", 0.9),
        ],
    )
    conn.commit()

    profile = fuse_track_harmony(conn, "conflict")

    assert profile["analysis_status"] == "provisional_insufficient_evidence"
    assert profile["consensus_policy"]["strict_consensus_segment_count"] == 0
    assert profile["progression"] == []
    assert profile["confidence"] is None


def test_strict_consensus_coverage_is_based_on_agreeing_evidence():
    conn = _conn()
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("coverage", "source_a", None, 0.0, 4.0, "C", 0.9),
            ("coverage", "source_b", None, 0.0, 4.0, "C", 0.8),
            ("coverage", "source_a", None, 6.0, 10.0, "F", 0.9),
        ],
    )
    conn.commit()

    profile = fuse_track_harmony(conn, "coverage")

    assert profile["consensus_policy"]["strict_consensus_segment_count"] == 1
    assert profile["consensus_policy"]["strict_consensus_coverage"] == 0.4
    assert profile["analysis_status"] == "strict_consensus"


def test_profile_quality_control_rejects_confidence_without_evidence():
    conn = _conn()
    issues = _validate_harmony_profile(
        conn,
        "no-evidence",
        {
            "analysis_status": "provisional_insufficient_evidence",
            "confidence": 0.8,
            "evidence_count": 0,
            "segments": [],
            "strict_consensus_segments": [],
            "provider_coverage": {},
            "consensus_policy": {
                "strict_consensus_coverage": 0,
                "evidence_duration_sec": 0,
            },
        },
    )

    assert "confidence_without_evidence" in issues
    assert "confidence_on_non_strict_profile" in issues


def test_strict_coverage_uses_known_track_duration():
    conn = _conn()
    conn.execute(
        "CREATE TABLE tracks (track_id TEXT PRIMARY KEY, duration_ms INTEGER)"
    )
    conn.execute("INSERT INTO tracks VALUES ('short-evidence', 100000)")
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("short-evidence", "source_a", None, 0.0, 4.0, "C", 0.9),
            ("short-evidence", "source_b", None, 0.0, 4.0, "C", 0.9),
        ],
    )
    conn.commit()

    profile = fuse_track_harmony(conn, "short-evidence")

    assert profile["consensus_policy"]["strict_consensus_coverage"] == 0.04
    assert profile["analysis_status"] == "provisional_insufficient_evidence"
    assert profile["progression"] == []


class HarmonyPipelineHardeningTests(unittest.TestCase):
    def test_valid_provider_timeline_has_data_success(self):
        _, state = normalize_provider_result(
            "source_a",
            {
                "status": "success",
                "harmony_payload": {
                    "status": "success",
                    "segments": [{"start_sec": 0, "end_sec": 4, "chord": "Am7"}],
                },
            },
        )
        self.assertEqual(state["status"], "success_with_data")
        self.assertEqual(len(state["segments"]), 1)

    def test_explicit_empty_provider_timeline_is_not_failure(self):
        _, state = normalize_provider_result(
            "source_a",
            {
                "status": "success",
                "harmony_payload": {"status": "success", "segments": []},
            },
        )
        self.assertEqual(state["status"], "success_empty")

    def test_provider_timeout_is_not_empty_success(self):
        _, state = normalize_provider_result(
            "source_a", {"status": "poll_timeout"}
        )
        self.assertEqual(state["status"], "timeout")

    def test_missing_timeline_is_a_parse_failure(self):
        _, state = normalize_provider_result(
            "source_a",
            {"status": "success", "harmony_payload": {"status": "success"}},
        )
        self.assertEqual(state["status"], "invalid_result")
        self.assertEqual(state["error_type"], "MissingTimeline")

    def test_malformed_segment_is_rejected(self):
        _, state = normalize_provider_result(
            "source_a",
            {
                "status": "success",
                "harmony_payload": {
                    "status": "success",
                    "segments": [{"start_sec": 0, "end_sec": 3, "chord": "H"}],
                },
            },
        )
        self.assertEqual(state["status"], "invalid_result")
        self.assertEqual(state["invalid_segment_count"], 1)

    def test_invalid_provider_confidence_is_rejected(self):
        _, state = normalize_provider_result(
            "source_a",
            {
                "status": "success",
                "harmony_payload": {
                    "status": "success",
                    "confidence": 1.5,
                    "segments": [{"start_sec": 0, "end_sec": 4, "chord": "C"}],
                },
            },
        )
        self.assertEqual(state["status"], "invalid_result")
        self.assertEqual(state["error_type"], "InvalidConfidence")

    def test_single_source_does_not_become_consensus(self):
        conn = _conn()
        conn.execute(
            "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("single-unit", "source_a", None, 0, 10, "Am7", 0.9),
        )
        profile = fuse_track_harmony(conn, "single-unit")
        self.assertEqual(profile["analysis_status"], "provisional_insufficient_evidence")
        self.assertEqual(profile["segments"], [])
        self.assertIsNone(profile["confidence"])

    def test_two_matching_sources_form_consensus(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("two-unit", "source_a", None, 0, 4, "C", 0.9),
                ("two-unit", "source_b", None, 0, 4, "C", 0.8),
            ],
        )
        profile = fuse_track_harmony(conn, "two-unit")
        self.assertEqual(profile["analysis_status"], "strict_consensus")
        self.assertEqual(profile["progression"], ["C"])

    def test_disagreeing_sources_remain_provisional(self):
        conn = _conn()
        conn.executemany(
            "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("conflict-unit", "source_a", None, 0, 10, "Am7", 0.9),
                ("conflict-unit", "source_b", None, 0, 10, "C6", 0.9),
            ],
        )
        profile = fuse_track_harmony(conn, "conflict-unit")
        self.assertEqual(profile["analysis_status"], "provisional_insufficient_evidence")
        self.assertEqual(profile["progression"], [])

    def test_fallback_source_never_contributes_consensus(self):
        conn = _conn()
        conn.execute(
            "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("fallback-unit", "neutral_fallback", None, 0, 10, "C", 1.0),
        )
        self.assertIsNone(fuse_track_harmony(conn, "fallback-unit"))

    def test_strict_consensus_coverage_uses_track_duration(self):
        conn = _conn()
        conn.execute(
            "CREATE TABLE tracks (track_id TEXT PRIMARY KEY, duration_ms INTEGER)"
        )
        conn.execute("INSERT INTO tracks VALUES ('coverage-unit', 100000)")
        conn.executemany(
            "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("coverage-unit", "source_a", None, 0, 4, "C", 0.9),
                ("coverage-unit", "source_b", None, 0, 4, "C", 0.9),
            ],
        )
        profile = fuse_track_harmony(conn, "coverage-unit")
        self.assertEqual(profile["consensus_policy"]["strict_consensus_coverage"], 0.04)
        self.assertEqual(profile["analysis_status"], "provisional_insufficient_evidence")

    def test_confidence_without_evidence_fails_profile_qc(self):
        conn = _conn()
        issues = _validate_harmony_profile(
            conn,
            "no-evidence-unit",
            {
                "analysis_status": "provisional_insufficient_evidence",
                "confidence": 0.8,
                "evidence_count": 0,
                "segments": [],
                "strict_consensus_segments": [],
                "provider_coverage": {},
                "consensus_policy": {
                    "strict_consensus_coverage": 0,
                    "strict_consensus_segment_count": 0,
                    "evidence_duration_sec": 0,
                },
            },
        )
        self.assertIn("confidence_without_evidence", issues)
