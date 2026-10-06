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
            track_id TEXT, key TEXT, confidence REAL,
            raw_data TEXT DEFAULT '{}', observed_at TEXT
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
    conn.execute(
        "INSERT INTO harmony_sources(track_id, key, confidence) "
        "VALUES ('t1', 'C major', 0.9)"
    )
    conn.commit()

    profile = fuse_track_harmony(conn, "t1")

    assert profile["progression"] == ["Am7"]
    segment = profile["segments"][0]
    assert segment["source_count"] == 2
    assert "C6" in segment["pitch_set_equivalents"]
    assert segment["competing_evidence"][0]["chord"] == "C6"
    assert profile["consensus_policy"]["minimum_agreeing_sources"] == 2


def test_repeated_fusion_reuses_unchanged_derived_evidence():
    conn = _conn()
    rows = [
        ("t-idempotent", "source_a", None, 0.0, 4.0, "Cmaj7", 0.9),
        ("t-idempotent", "source_b", None, 0.0, 4.0, "Cmaj7", 0.9),
    ]
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.commit()

    first_profile = fuse_track_harmony(conn, "t-idempotent")
    evidence_count = conn.execute(
        """SELECT COUNT(*) FROM music_analysis_evidence
           WHERE track_id=? AND source='harmony_consensus'""",
        ("t-idempotent",),
    ).fetchone()[0]
    second_profile = fuse_track_harmony(conn, "t-idempotent")
    repeated_evidence_count = conn.execute(
        """SELECT COUNT(*) FROM music_analysis_evidence
           WHERE track_id=? AND source='harmony_consensus'""",
        ("t-idempotent",),
    ).fetchone()[0]

    assert first_profile["progression"] == second_profile["progression"]
    assert evidence_count == repeated_evidence_count == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM harmony_structures WHERE track_id=?",
        ("t-idempotent",),
    ).fetchone()[0] == 1


def test_enharmonic_spelling_can_reach_consensus_without_collapsing_source_text():
    conn = _conn()
    rows = [
        ("t2", "source_a", None, 0.0, 4.0, "C#maj7", 0.9),
        ("t2", "source_b", None, 0.1, 4.1, "Dbmaj7", 0.8),
    ]
    conn.executemany(
        "INSERT INTO harmony_segments VALUES (?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.execute(
        "INSERT INTO harmony_sources(track_id, key, confidence) "
        "VALUES ('t2', 'Db major', 0.9)"
    )
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
    conn.execute(
        "INSERT INTO harmony_sources(track_id, key, confidence) "
        "VALUES ('t3', 'C major', 0.9)"
    )
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
    conn.execute(
        "INSERT INTO harmony_sources(track_id, key, confidence) "
        "VALUES ('t4', 'C major', 0.9)"
    )
    conn.execute("INSERT INTO audio_features VALUES ('t4', 120.0)")
    conn.commit()
    profile = fuse_track_harmony(conn, "t4")
    structure = profile["structure_map"]
    assert structure[0]["section"] == "Section 01"
    assert structure[0]["start_bar"] == 1
    assert structure[0]["end_bar"] == 2
    assert structure[0]["semantic_label_evidence"] is False
    assert "[Section 01] Bars 01-02" in profile["prompt_harmony"]

def test_btc_harte_notation_normalizes_to_jacques_identity():
    assert _split("C:maj7") == ("C", "maj7", None)
    assert _split("A:min") == ("A", "m", None)
    assert _split("D:7") == ("D", "7", None)
    assert _split("F#:min7") == ("F#", "m7", None)
