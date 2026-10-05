import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from music_db.enrichment.sources.songbpm import _set_status
from music_db.enrichment.merge import refresh_canonical_audio_features
from music_db.schema.migrations import apply_migrations
from music_db.spotify_preview import collect_spotify_tracks, preview_enrichment
from tools.safe_merge_database import safe_merge


class PhaseOneStabilityTests(unittest.TestCase):
    def test_migration_adds_priorities_and_normalizes_songbpm_status(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE tracks (track_id TEXT PRIMARY KEY);
            CREATE TABLE audio_features (track_id TEXT PRIMARY KEY);
            CREATE TABLE enrichment_status (
                track_id TEXT, source TEXT, entity_type TEXT, status TEXT,
                attempts INTEGER, last_error TEXT, last_attempted_at TEXT,
                completed_at TEXT, created_at TEXT, updated_at TEXT,
                PRIMARY KEY (track_id, source, entity_type)
            );
            INSERT INTO tracks VALUES ('track-1');
            INSERT INTO enrichment_status VALUES (
                'track-1', 'songbpm', 'track', 'success', 1, NULL,
                '2026-01-01', '2026-01-01', '2026-01-01', '2026-01-01'
            );
            """
        )

        apply_migrations(conn)

        self.assertEqual(
            conn.execute(
                """
                SELECT status FROM enrichment_status
                WHERE track_id='track-1' AND source='songbpm'
                  AND entity_type='audio_features'
                """
            ).fetchone()[0],
            "success",
        )
        self.assertIsNone(
            conn.execute(
                """
                SELECT 1 FROM enrichment_status
                WHERE track_id='track-1' AND source='songbpm'
                  AND entity_type='track'
                """
            ).fetchone()
        )
        self.assertEqual(
            conn.execute(
                "SELECT priority FROM source_priority "
                "WHERE field_name='tempo' AND source='freqblog'"
            ).fetchone()[0],
            10,
        )
        self.assertIn(
            "source_by_field",
            {row[1] for row in conn.execute("PRAGMA table_info(audio_features)")},
        )
        self.assertIn(
            "error_details",
            {row[1] for row in conn.execute("PRAGMA table_info(enrichment_status)")},
        )
        self.assertIn(
            "harmony_provider_runs",
            {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            },
        )
        self.assertIn(
            "normalized_segment_count",
            {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(harmony_provider_runs)"
                )
            },
        )
        conn.close()

    def test_songbpm_status_records_structured_json(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE enrichment_status (
                track_id TEXT, source TEXT, entity_type TEXT, status TEXT,
                attempts INTEGER, last_error TEXT, error_details TEXT,
                last_attempted_at TEXT, completed_at TEXT, created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (track_id, source, entity_type)
            )
            """
        )

        _set_status(conn, "track-1", "search_failed", last_error="http=503")

        entity_type, details = conn.execute(
            "SELECT entity_type, error_details FROM enrichment_status"
        ).fetchone()
        self.assertEqual(entity_type, "audio_features")
        self.assertEqual(json.loads(details)["error"], "http=503")
        conn.close()

    def test_audio_merge_uses_field_priorities_and_persists_provenance(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE tracks (track_id TEXT PRIMARY KEY, duration_ms INTEGER);
            CREATE TABLE audio_feature_sources (
                track_id TEXT, source TEXT, tempo REAL, key TEXT, mode TEXT,
                loudness REAL, energy REAL, danceability REAL, valence REAL,
                acousticness REAL, instrumentalness REAL, speechiness REAL,
                confidence REAL, source_url TEXT, raw_data TEXT,
                created_at TEXT, updated_at TEXT, tempo_confidence REAL,
                key_confidence REAL, PRIMARY KEY (track_id, source)
            );
            CREATE TABLE audio_features (
                track_id TEXT PRIMARY KEY, duration_ms INTEGER, tempo REAL,
                key INTEGER, mode INTEGER, loudness REAL, energy REAL,
                danceability REAL, valence REAL, acousticness REAL,
                instrumentalness REAL, speechiness REAL, source TEXT,
                confidence REAL, source_by_field TEXT, updated_at TEXT
            );
            CREATE TABLE source_priority (
                field_name TEXT, source TEXT, priority INTEGER,
                updated_at TEXT, PRIMARY KEY (field_name, source)
            );
            INSERT INTO tracks VALUES ('track-1', 180000);
            INSERT INTO audio_feature_sources
                (track_id, source, tempo, danceability, confidence)
            VALUES
                ('track-1', 'freqblog', 100, NULL, 0.9),
                ('track-1', 'songbpm', 120, 0.6, 0.7);
            INSERT INTO source_priority VALUES
                ('tempo', 'freqblog', 10, 'now'),
                ('tempo', 'songbpm', 20, 'now'),
                ('danceability', 'freqblog', 10, 'now'),
                ('danceability', 'songbpm', 20, 'now');
            """
        )

        self.assertTrue(refresh_canonical_audio_features(conn, "track-1"))

        tempo, danceability, sources = conn.execute(
            """
            SELECT tempo, danceability, source_by_field
            FROM audio_features WHERE track_id='track-1'
            """
        ).fetchone()
        self.assertEqual((tempo, danceability), (100, 0.6))
        self.assertEqual(
            json.loads(sources)["danceability"],
            "songbpm",
        )

        conn.execute(
            "UPDATE source_priority SET priority=30 "
            "WHERE field_name='tempo' AND source='freqblog'"
        )
        refresh_canonical_audio_features(conn, "track-1")
        self.assertEqual(
            conn.execute(
                "SELECT tempo FROM audio_features WHERE track_id='track-1'"
            ).fetchone()[0],
            120,
        )
        conn.close()

    def test_spotify_preview_deduplicates_and_reports_candidates(self):
        class FakeSpotify:
            def current_user_saved_tracks(self, limit):
                return {
                    "items": [{"track": {"id": "one", "name": "One"}}],
                    "next": None,
                }

            def current_user_recently_played(self, limit):
                return {"items": [{"track": {"id": "one", "name": "One"}}]}

            def current_user_top_tracks(self, limit, time_range):
                return {"items": [{"id": "two", "name": "Two"}]}

        tracks = collect_spotify_tracks(FakeSpotify())
        self.assertEqual([track["id"] for track in tracks], ["one", "two"])

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE enrichment_status (
                track_id TEXT, source TEXT, entity_type TEXT, status TEXT,
                last_attempted_at TEXT
            );
            CREATE TABLE audio_features (
                track_id TEXT, tempo REAL, key INTEGER, mode INTEGER,
                loudness REAL, energy REAL, danceability REAL, valence REAL,
                acousticness REAL, instrumentalness REAL, speechiness REAL
            );
            INSERT INTO enrichment_status
                (track_id, source, entity_type, status)
            VALUES
                ('one', 'freqblog', 'audio_features', 'success');
            INSERT INTO audio_features VALUES
                ('one', 100, 1, 1, -3, 0.5, 0.6, 0.7, 0.2, 0.1, 0.05);
            """
        )
        candidates = preview_enrichment(conn, tracks)
        self.assertEqual([candidate["track_id"] for candidate in candidates], ["two"])
        self.assertEqual(len(candidates[0]["missing_fields"]), 10)
        conn.close()

    def test_safe_merge_preserves_disjoint_row_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.db"
            runner = Path(directory) / "runner.db"
            latest = Path(directory) / "latest.db"
            merged = Path(directory) / "merged.db"
            for path, values in (
                (base, ("Original", 100, "2026-01-01")),
                (runner, ("Original", 120, "2026-01-01")),
                (latest, ("Original", 100, "2026-02-01")),
            ):
                with sqlite3.connect(path) as conn:
                    conn.execute(
                        """
                        CREATE TABLE tracks (
                            track_id TEXT PRIMARY KEY, title TEXT,
                            tempo INTEGER, last_played TEXT
                        )
                        """
                    )
                    conn.execute(
                        "INSERT INTO tracks VALUES (?, ?, ?, ?)",
                        ("track-1", values[0], values[1], values[2]),
                    )

            safe_merge(base, runner, latest, merged)

            with sqlite3.connect(merged) as conn:
                self.assertEqual(
                    conn.execute("SELECT title, tempo, last_played FROM tracks").fetchone(),
                    ("Original", 120, "2026-02-01"),
                )

    def test_safe_merge_aborts_on_divergent_updates_to_same_field(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.db"
            runner = Path(directory) / "runner.db"
            latest = Path(directory) / "latest.db"
            merged = Path(directory) / "merged.db"
            for path, title in (
                (base, "Original"),
                (runner, "Runner update"),
                (latest, "Latest update"),
            ):
                with sqlite3.connect(path) as conn:
                    conn.execute(
                        "CREATE TABLE tracks (track_id TEXT PRIMARY KEY, title TEXT)"
                    )
                    conn.execute("INSERT INTO tracks VALUES ('track-1', ?)", (title,))

            with self.assertRaisesRegex(RuntimeError, "changed the same field"):
                safe_merge(base, runner, latest, merged)

            with sqlite3.connect(latest) as conn:
                self.assertEqual(
                    conn.execute("SELECT title FROM tracks").fetchone()[0],
                    "Latest update",
                )

    def test_safe_merge_aborts_on_distinct_concurrent_inserts_with_same_key(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "base.db"
            runner = Path(directory) / "runner.db"
            latest = Path(directory) / "latest.db"
            merged = Path(directory) / "merged.db"
            for path, rows in (
                (base, []),
                (runner, [(1, "runner-only")]),
                (latest, [(1, "latest-only")]),
            ):
                with sqlite3.connect(path) as conn:
                    conn.execute(
                        """
                        CREATE TABLE events (
                            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            value TEXT NOT NULL
                        )
                        """
                    )
                    conn.executemany(
                        "INSERT INTO events(event_id, value) VALUES (?, ?)",
                        rows,
                    )

            with self.assertRaisesRegex(
                RuntimeError, "Concurrent inserts reused the same primary key"
            ):
                safe_merge(base, runner, latest, merged)

            with sqlite3.connect(latest) as conn:
                self.assertEqual(
                    conn.execute("SELECT value FROM events").fetchone()[0],
                    "latest-only",
                )


if __name__ == "__main__":
    unittest.main()
