from src.music_db.harmony.pipeline import SOURCE_PRIORITY


def test_streaming_harmony_sources_have_explicit_priority():
    assert SOURCE_PRIORITY["chordidentifier"] < SOURCE_PRIORITY["magic_chords"]
    assert SOURCE_PRIORITY["magic_chords"] < SOURCE_PRIORITY["essentia"]
