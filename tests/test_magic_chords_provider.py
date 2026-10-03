import unittest
from unittest.mock import Mock, patch

from tools.magic_chords_provider import _segments


class MagicChordsProviderTests(unittest.TestCase):
    def test_normalizes_chord_timeline(self):
        result = {
            "chords": [
                {"start": 0, "end": 2.5, "chord": "Cmaj7", "confidence": 0.91},
                {"start": 2.5, "end": 5, "symbol": "Am7"},
                {"start": 5, "end": 5, "chord": "ignored"},
            ]
        }
        rows = _segments(result)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chord"], "Cmaj7")
        self.assertEqual(rows[1]["chord"], "Am7")
        self.assertEqual(rows[0]["start_sec"], 0.0)
        self.assertEqual(rows[1]["end_sec"], 5.0)

    def test_accepts_nested_timeline(self):
        result = {"timeline": {"items": [{"startTime": 1, "endTime": 3, "label": "Dm"}]}}
        rows = _segments(result)
        self.assertEqual(rows[0]["chord"], "Dm")
        self.assertEqual(rows[0]["start_sec"], 1.0)
        self.assertEqual(rows[0]["end_sec"], 3.0)


if __name__ == "__main__":
    unittest.main()
