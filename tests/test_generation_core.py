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
