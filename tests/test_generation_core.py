import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path

from music_db.database import initialize_database, get_connection, SCHEMA, run_migrations
from music_db.generation.compare import compare
from music_db.generation.outputs import create_output, link_outputs, extract_audio_refs
from music_db.generation.projects import create_project, stage_dir
from music_db.generation.assets import import_audio_asset
from music_db.generation.prompt_builder import build_prompt
from music_db.generation.http import post_json


class GenerationCoreTests(unittest.TestCase):
    def test_songbpm_cannot_run_without_terminal_freqblog_miss(self):
        from music_db.enrichment.sources.songbpm import enrich_one

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE audio_feature_sources (
                track_id TEXT,
                source TEXT,
                PRIMARY KEY (track_id, source)
            );
            CREATE TABLE enrichment_status (
                track_id TEXT,
                source TEXT,
                entity_type TEXT,
                status TEXT,
                attempts INTEGER,
                last_error TEXT,
                last_attempted_at TEXT,
                completed_at TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (track_id, source, entity_type)
            );
        """)
        track = {
            "track_id": "t1",
            "title": "Example",
            "duration_ms": 180000,
        }
        try:
            result = enrich_one(conn, track, dry_run=True)
            self.assertEqual(result["status"], "skipped_freqblog")
        finally:
            conn.close()

    def test_bulk_engine_respects_disabled_source_registry(self):
        from music_db.enrichment.engine import _source_enabled

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE source_registry (source TEXT PRIMARY KEY, enabled INTEGER)"
        )
        conn.executemany(
            "INSERT INTO source_registry(source, enabled) VALUES (?, ?)",
            [("freqblog", 0), ("songbpm", 1)],
        )
        self.assertFalse(_source_enabled(conn, "FREQBLOG"))
        self.assertTrue(_source_enabled(conn, "SONGBPM"))
        self.assertTrue(_source_enabled(conn, "LAST.FM"))
        conn.close()

    def test_source_registry_enabled_state_survives_seed_refresh(self):
        from music_db.database import SOURCE_SEED, run_migrations

        conn = sqlite3.connect(":memory:")
        try:
            run_migrations(conn)
            conn.execute(
                "UPDATE source_registry SET enabled=0 WHERE source='billboard'"
            )
            conn.executescript(SOURCE_SEED)
            enabled = conn.execute(
                "SELECT enabled FROM source_registry WHERE source='billboard'"
            ).fetchone()[0]
            self.assertEqual(enabled, 0)
        finally:
            conn.close()

    def test_compare_ignores_non_finite_values_and_matches_enharmonic_keys(self):
        out = __import__(
            "music_db.generation.compare",
            fromlist=["compare"],
        ).compare(
            {"tempo_bpm": float("nan"), "estimated_key": "F#"},
            {"tempo_bpm": 120, "estimated_key": "Gb"},
        )
        self.assertNotIn("tempo_bpm", out)
        self.assertTrue(out["estimated_key"]["same"])

        out = compare(
            {"estimated_key": "F# minor"},
            {"estimated_key": "Gb minor"},
        )
        self.assertTrue(out["estimated_key"]["same"])

    def test_discogs_best_release_requires_strong_artist_album_match(self):
        from music_db.external.discogs import _best_release

        result, score = _best_release(
            [
                {"id": 1, "title": "Wrong Artist - Target Album"},
                {"id": 2, "title": "Right Artist - Target Album"},
            ],
            "Right Artist",
            "Target Album",
        )
        self.assertEqual(result["id"], 2)
        self.assertEqual(score, 1.0)

    def test_compare_extended_metrics(self):
        a = {"tempo_bpm": 120, "spectral_flatness": 0.1, "silence_ratio": 0.2}
        b = {"tempo_bpm": 130, "spectral_flatness": 0.2, "silence_ratio": 0.1}
        out = compare(a, b)
        self.assertEqual(out["tempo_bpm"]["delta"], 10)
        self.assertAlmostEqual(out["silence_ratio"]["delta"], -0.1)

        vector = compare(
            {"mfcc_mean": [1.0, 2.0, 3.0]},
            {"mfcc_mean": [2.0, 2.0, 5.0]},
        )
        self.assertAlmostEqual(vector["mfcc_mean"]["mean_absolute_delta"], 1.0)
        self.assertAlmostEqual(
            vector["mfcc_mean"]["euclidean_distance"],
            5 ** 0.5,
        )

    def test_prompt_uses_v4_features(self):
        prompt = build_prompt({
            "tempo_bpm": 128,
            "estimated_key": "F#",
            "dynamic_range_estimate_db": 16,
            "spectral_flatness": 0.1,
            "silence_ratio": 0.2,
            "key_confidence": 0.03,
            "onset_rate_per_second": 4,
        })
        self.assertIn("wide dynamics", prompt)
        self.assertIn("tonal/harmonic texture", prompt)
        self.assertIn("noticeable negative space", prompt)
        self.assertIn("ambiguous tonal center", prompt)

    @patch("music_db.generation.http.time.sleep")
    @patch("music_db.generation.http.requests.post")
    def test_http_retries_429_and_honors_retry_after(self, post, sleep):
        first = Mock(status_code=429, headers={"Retry-After": "3"})
        second = Mock(status_code=200, headers={})
        post.side_effect = [first, second]

        response = post_json("https://example.test/generate", json_body={"x": 1})

        self.assertIs(response, second)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(3.0)

    @patch("music_db.generation.http.time.sleep")
    @patch("music_db.generation.http.requests.post")
    def test_http_returns_final_retryable_response(self, post, sleep):
        responses = [Mock(status_code=503, headers={}) for _ in range(3)]
        post.side_effect = responses

        response = post_json("https://example.test/generate", attempts=3)

        self.assertIs(response, responses[-1])
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)



    def test_download_audio_ref_infers_extension_from_content_type(self, get):
        from music_db.generation.outputs import download_audio_ref

        class Response:
            headers = {"Content-Type": "audio/mpeg; charset=binary"}
            def raise_for_status(self): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, chunk_size):
                yield b"audio"

        get.return_value = Response()
        with tempfile.TemporaryDirectory() as td:
            path = download_audio_ref(
                "https://cdn.test/generated",
                td,
                filename="track.bin",
            )
            self.assertTrue(path.endswith(".mp3"))
            self.assertEqual(Path(path).read_bytes(), b"audio")

    @patch("requests.get")
    def test_download_audio_ref_rejects_image_response(self, get):
        from music_db.generation.outputs import download_audio_ref

        class Response:
            headers = {"Content-Type": "image/jpeg"}
            def raise_for_status(self): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass

        get.return_value = Response()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                download_audio_ref(
                    "https://cdn.test/generated.mp3",
                    td,
                )
        get.assert_called_once()

    def test_extract_audio_refs_ignores_generic_image_urls(self):
        response = {
            "audio_url": "https://cdn.test/song.mp3",
            "cover_url": "https://cdn.test/cover.jpg",
            "url": "https://cdn.test/page",
            "nested": {"output": "https://cdn.test/result.wav"},
        }
        self.assertEqual(
            extract_audio_refs(response),
            ["https://cdn.test/song.mp3", "https://cdn.test/result.wav"],
        )

    def test_generation_project_creates_staged_directories_and_links_output(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            run_migrations(db)
            project_id, root = create_project(
                db, "My Test Track", root_dir=td, reference_track_id=None
            )
            self.assertTrue((root / "reference").is_dir())
            self.assertTrue((root / "generations").is_dir())
            self.assertTrue((root / "edits").is_dir())
            self.assertTrue((root / "final").is_dir())

            from music_db.generation.base import create_generation_job
            job_id = create_generation_job(
                db, "test", project_id=project_id, request={"x": 1}
            )
            output_id = create_output(
                db, job_id, project_id=project_id, stage="generations"
            )
            row = db.execute(
                """SELECT j.project_id, o.project_id, o.stage
                   FROM generation_jobs j
                   JOIN generation_outputs o ON o.job_id=j.job_id
                   WHERE o.output_id=?""",
                (output_id,),
            ).fetchone()
            self.assertEqual(row[0], project_id)
            self.assertEqual(row[1], project_id)
            self.assertEqual(row[2], "generations")
            self.assertEqual(stage_dir(db, project_id, "final"), root / "final")
            db.close()


    def test_generation_project_does_not_overwrite_same_named_asset(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            run_migrations(db)
            project_id, root = create_project(db, "Collision Test", root_dir=td)
            source_a = Path(td) / "track.wav"
            source_b = Path(td) / "track2.wav"
            source_a.write_bytes(b"first")
            source_b.write_bytes(b"second")
            first_id, first_path = import_audio_asset(db, project_id, source_a)
            source_b.rename(Path(td) / "track.wav")
            second_id, second_path = import_audio_asset(
                db, project_id, Path(td) / "track.wav"
            )
            self.assertNotEqual(first_id, second_id)
            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_bytes(), b"first")
            self.assertEqual(second_path.read_bytes(), b"second")
            db.close()

    def test_generation_project_status_rejects_unknown_project(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA)
        run_migrations(db)
        from music_db.generation.projects import update_project_status
        with self.assertRaises(ValueError):
            update_project_status(db, 999999, "completed")
        db.close()

    def test_generation_project_imports_reference_asset_with_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            run_migrations(db)
            project_id, root = create_project(db, "Asset Test", root_dir=td)
            source = Path(td) / "reference.wav"
            source.write_bytes(b"reference-audio")
            asset_id, destination = import_audio_asset(db, project_id, source)
            row = db.execute("SELECT asset_type, audio_path, fingerprint_sha256 FROM generation_assets WHERE asset_id=?", (asset_id,)).fetchone()
            self.assertEqual(row[0], "reference")
            self.assertEqual(Path(row[1]), destination)
            self.assertEqual(row[2], sha256_file(str(destination)))
            self.assertTrue(destination.parent.name == "reference")
            db.close()

    def test_output_fingerprint_column(self):
        initialize_database()
        with get_connection() as conn:
            jid = conn.execute(
                "INSERT INTO generation_jobs(provider,status,created_at) "
                "VALUES ('test','test','2026-01-01T00:00:00Z')"
            ).lastrowid
            oid = create_output(conn, jid, audio_path=None)
            row = conn.execute(
                "SELECT fingerprint_sha256 FROM generation_outputs WHERE output_id=?",
                (oid,),
            ).fetchone()
            self.assertIsNone(row[0])


    def test_edited_derivative_creates_new_output_and_lineage(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            run_migrations(db)
            project_id, _ = create_project(db, "Edit Test", root_dir=td)
            from music_db.generation.base import create_generation_job
            job_id = create_generation_job(db, "mureka", project_id=project_id)
            source_path = Path(td) / "source.wav"
            edit_path = Path(td) / "edit.wav"
            source_path.write_bytes(b"source")
            edit_path.write_bytes(b"edited")
            source_id = create_output(
                db, job_id, audio_path=str(source_path),
                project_id=project_id, stage="generations"
            )
            from music_db.generation.outputs import create_derivative_output
            edited_id = create_derivative_output(
                db, source_id, str(edit_path), title="Edited take", note="less reverb"
            )
            self.assertNotEqual(source_id, edited_id)
            row = db.execute(
                """SELECT o.project_id, o.stage, j.provider
                   FROM generation_outputs o
                   JOIN generation_jobs j ON j.job_id=o.job_id
                   WHERE o.output_id=?""",
                (edited_id,),
            ).fetchone()
            self.assertEqual(row["project_id"], project_id)
            self.assertEqual(row["stage"], "edits")
            self.assertEqual(row["provider"], "local_edit")
            rel = db.execute(
                """SELECT relation_type, note FROM generation_relations
                   WHERE from_output_id=? AND to_output_id=?""",
                (edited_id, source_id),
            ).fetchone()
            self.assertEqual(rel["relation_type"], "edited_from")
            self.assertEqual(rel["note"], "less reverb")
            db.close()

    def test_relation_upsert(self):
        initialize_database()
        with get_connection() as conn:
            jid = conn.execute(
                "INSERT INTO generation_jobs(provider,status,created_at) "
                "VALUES ('test','test','2026-01-01T00:00:00Z')"
            ).lastrowid
            a = create_output(conn, jid)
            b = create_output(conn, jid, output_index=1)
            link_outputs(conn, a, b, "variant_of", confidence=0.8, note="x")
            link_outputs(conn, a, b, "variant_of", confidence=0.9, note="y")
            row = conn.execute(
                "SELECT confidence,note FROM generation_relations "
                "WHERE from_output_id=? AND to_output_id=?",
                (a, b),
            ).fetchone()
            self.assertEqual(tuple(row), (0.9, "y"))


if __name__ == "__main__":
    unittest.main()
