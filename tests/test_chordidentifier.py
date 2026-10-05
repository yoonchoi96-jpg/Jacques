import unittest

from src.music_db.harmony.chordidentifier import (
    build_harmony_payload,
    parse_chordidentifier_html,
)
from src.music_db.harmony.provider_results import normalize_provider_result

def test_parse_chordidentifier_timeline():
    html = """
    <marker><span style="font-family: monospace">B<br></span></marker>
    <region title="0:07-0:08"></region>
    <marker><span style="font-family: monospace">E<br></span></marker>
    <region title="0:08-0:10"></region>
    <marker><span style="font-family: monospace">C#m<br></span></marker>
    <region title="0:21-0:22"></region>
    """
    segments = parse_chordidentifier_html(html)
    assert [x["chord"] for x in segments] == ["B", "E", "C#m"]
    assert segments[1]["start_sec"] == 8.0
    assert segments[1]["end_sec"] == 10.0


class ChordIdentifierResultTests(unittest.TestCase):
    def test_unparsed_rendered_region_is_reported_as_parse_failure(self):
        payload = build_harmony_payload(
            '<region title="0:07-0:08"></region>',
            source_url="https://chordidentifier.com/result",
            youtube_url="https://youtube.com/watch?v=abcdefghijk",
        )
        _, state = normalize_provider_result(
            "chordidentifier",
            {"status": "success", "harmony_payload": payload},
        )

        self.assertEqual(payload["raw_region_count"], 1)
        self.assertEqual(state["status"], "invalid_result")
        self.assertEqual(state["error_type"], "ChordIdentifierParseError")
