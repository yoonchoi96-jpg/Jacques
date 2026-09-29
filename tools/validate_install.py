from __future__ import annotations

import importlib
import sys

MODULES = [  # core import smoke test
    "music_db.database",
    "music_db.enrichment.engine",
    "music_db.enrichment.live_dispatcher",
    "music_db.external.billboard",
    "music_db.external.pitchfork",
    "music_db.external.apple_music",
    "music_db.external.discogs",
    "music_db.external.acoustid",
    "music_db.generation.base",
    "music_db.generation.outputs",
    "music_db.generation.audio_analysis",
    "music_db.generation.compare",
    "music_db.generation.prompt_builder",
    "music_db.generation.mureka",
    "music_db.generation.ace_step",
]


def main():
    failures = []
    for name in MODULES:
        try:
            importlib.import_module(name)
            print(f"OK   {name}")
        except Exception as exc:
            failures.append((name, exc))
            print(f"FAIL {name}: {exc}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
