from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from music_db.database import initialize_database, get_connection
from music_db.generation.outputs import link_outputs


RELATIONS = (
    "reference_of",
    "generated_from",
    "edited_from",
    "variant_of",
    "finalized_from",
)


def main():
    p = argparse.ArgumentParser(description="Link Jacques generation outputs")
    p.add_argument("from_output", type=int)
    p.add_argument("to_output", type=int)
    p.add_argument("--relation", choices=RELATIONS, required=True)
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--note", default=None)
    args = p.parse_args()

    initialize_database()
    with get_connection() as conn:
        link_outputs(
            conn, args.from_output, args.to_output, args.relation,
            confidence=args.confidence, note=args.note,
        )
    print(
        f"linked {args.from_output} -> {args.to_output} "
        f"relation={args.relation}"
    )


if __name__ == "__main__":
    main()
