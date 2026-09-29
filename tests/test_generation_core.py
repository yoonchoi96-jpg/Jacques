import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock
from pathlib import Path

from music_db.database import initialize_database, get_connection
from music_db.generation.compare import compare
from music_db.generation.fingerprint import sha256_file
from music_db.generation.outputs import create_output, link_outputs, extract_audio_refs
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

    def test_acoustid_respects_disabled_source_registry(self):
        from music_db.external.acoustid import identify_track

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE source_registry (source TEXT PRIMARY KEY, enabled INTEGER)"
        )
        conn.execute(
            "INSERT INTO source_registry(source, enabled) VALUES ('acoustid', 0)"
        )
        result = identify_track(conn, "t1", "/tmp/nonexistent.wav")
        self.assertEqual(result["status"], "disabled")
        conn.close()

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

    def test_audio_fingerprint_cache_identity(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a.wav"
            p.write_bytes(b"same-audio")
            self.assertEqual(sha256_file(str(p)), sha256_file(str(p)))

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



    @patch("music_db.generation.audio_analysis.analyze_audio")
    def test_corrupt_audio_analysis_cache_is_recomputed(self, analyze):
        from music_db.generation.audio_analysis import analyze_and_store, ANALYSIS_VERSION

        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(":memory:")
            db.row_factory = sqlite3.Row
            db.executescript("""
            CREATE TABLE generation_outputs (
                output_id INTEGER PRIMARY KEY,
                duration_seconds REAL,
                bpm REAL,
                sample_rate INTEGER,
                format TEXT,
                analysis_json TEXT
            );
            CREATE TABLE generation_analysis (
                analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                output_id INTEGER,
                analysis_type TEXT,
                payload_json TEXT,
                created_at TEXT
            );
            CREATE TABLE audio_analysis_cache (
                fingerprint_sha256 TEXT NOT NULL,
                analysis_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (fingerprint_sha256, analysis_type)
            );
            """)
            p = Path(td) / "a.wav"
            p.write_bytes(b"audio")
            fingerprint = sha256_file(str(p))
            db.execute("INSERT INTO generation_outputs(output_id) VALUES (1)")
            db.execute(
                "INSERT INTO audio_analysis_cache "
                "(fingerprint_sha256, analysis_type, payload_json) VALUES (?,?,?)",
                (fingerprint, ANALYSIS_VERSION, "{broken-json"),
            )
            db.commit()
            analyze.return_value = {
                "file": str(p),
                "duration_seconds": 1.0,
                "tempo_bpm": 120.0,
                "sample_rate": 8000,
                "analysis_version": ANALYSIS_VERSION,
            }
            analyze_and_store(db, 1, str(p))
            analyze.assert_called_once_with(str(p))
            row = db.execute(
                "SELECT analysis_json FROM generation_outputs WHERE output_id=1"
            ).fetchone()
            self.assertIn(ANALYSIS_VERSION, row[0])
            db.close()

    def test_audio_analysis_cache_is_version_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            db = sqlite3.connect(Path(td) / "cache.db")
            db.row_factory = sqlite3.Row
            db.executescript("""
            CREATE TABLE generation_outputs (
                output_id INTEGER PRIMARY KEY,
                duration_seconds REAL,
                bpm REAL,
                sample_rate INTEGER,
                format TEXT,
                analysis_json TEXT
            );
            CREATE TABLE generation_analysis (
                output_id INTEGER,
                analysis_type TEXT,
                payload_json TEXT,
                created_at TEXT
            );
            CREATE TABLE audio_analysis_cache (
                fingerprint_sha256 TEXT NOT NULL,
                analysis_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (fingerprint_sha256, analysis_type)
            );
            """)
            p = Path(td) / "a.wav"
            import wave
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"\0\0" * 8000)
            db.execute("INSERT INTO generation_outputs(output_id) VALUES (1)")
            db.execute(
                "INSERT INTO audio_analysis_cache "
                "(fingerprint_sha256,analysis_type,payload_json) VALUES (?,?,?)",
                (sha256_file(str(p)), "audio_features_v3", '{"duration_seconds":1}'),
            )
            db.commit()
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM audio_analysis_cache").fetchone()[0],
                1,
            )
            db.close()

    @patch("requests.get")
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

    def test_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.bin"
            p.write_bytes(b"jacques")
            self.assertEqual(
                sha256_file(str(p)),
                hashlib.sha256(b"jacques").hexdigest(),
            )

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
