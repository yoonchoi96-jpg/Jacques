from src.music_db.harmony.chordidentifier import parse_chordidentifier_html

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
