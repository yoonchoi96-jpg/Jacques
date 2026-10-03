import json
import sqlite3

from src.music_db.harmony.pipeline import (
    _chord_ambiguity,
    _split,
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
