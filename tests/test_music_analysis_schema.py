import sqlite3

from music_db.database import initialize_database, get_connection


def test_music_analysis_schema_is_available():
    initialize_database()
    with get_connection() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    expected = {
        "music_analysis_evidence",
        "music_theory_concepts",
        "track_theory_observations",
        "harmony_structures",
        "melody_analysis",
        "scale_mode_analysis",
        "production_analysis",
        "production_observations",
        "production_theory_concepts",
    }
    assert expected.issubset(tables)


def test_production_schema_separates_measurements_from_interpretation():
    initialize_database()
    with get_connection() as conn:
        production_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(production_analysis)"
            ).fetchall()
        }
        observation_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(production_observations)"
            ).fetchall()
        }

    assert {"lufs_integrated", "lufs_short_term", "true_peak_dbtp", "vu_average"}.issubset(
        production_columns
    )
    assert {"parameter", "value", "unit", "reference_value", "interpretation"}.issubset(
        observation_columns
    )


def test_analysis_sources_are_registered():
    initialize_database()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT source, role FROM source_registry WHERE source IN (?, ?, ?, ?, ?)",
            (
                "production_meter",
                "spectral_analyzer",
                "stereo_analyzer",
                "melody_analyzer",
                "scale_mode_analyzer",
            ),
        ).fetchall()

    assert len(rows) == 5
    assert {row["role"] for row in rows} == {
        "production_measurement",
        "melody_analysis",
        "scale_mode_analysis",
    }
