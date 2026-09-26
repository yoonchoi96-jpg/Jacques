import argparse

from .search import (
    search_tracks,
    search_by_artist,
    recent_tracks,
    saved_tracks,
    top_tracks,
    advanced_search,
)


def print_rows(rows):
    if not rows:
        print("\nNo results.")
        return

    print()
    print("=" * 100)

    for i, row in enumerate(rows, 1):
        title = row["title"] or "(untitled)"
        artists = row["artists"] or "(unknown artist)"
        album = row["album"] or "(unknown album)"
        release = row["release_date"] or ""

        print(f"{i:3}. {title}")
        print(f"     Artist : {artists}")
        print(f"     Album  : {album}")
        print(f"     Date   : {release}")

        if "tempo" in row.keys() and row["tempo"] is not None:
            print(f"     BPM    : {row['tempo']:.1f}")

        if "energy" in row.keys() and row["energy"] is not None:
            print(f"     Energy : {row['energy']:.2f}")

        if "danceability" in row.keys() and row["danceability"] is not None:
            print(f"     Dance  : {row['danceability']:.2f}")

        if "valence" in row.keys() and row["valence"] is not None:
            print(f"     Valence: {row['valence']:.2f}")

        if "last_played" in row.keys() and row["last_played"]:
            print(f"     Played : {row['last_played']}")

        if "saved" in row.keys() and row["saved"]:
            print("     Saved  : YES")

        if "saved_at" in row.keys() and row["saved_at"]:
            print(f"     Saved at : {row['saved_at']}")

        print("-" * 100)


def main():
    parser = argparse.ArgumentParser(
        description="Music Database CLI"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # --------------------------------------------------------
    # search
    # --------------------------------------------------------

    search_parser = subparsers.add_parser("search")

    search_parser.add_argument(
        "keyword",
        nargs="?",
        help="Search title, album, or artist",
    )

    search_parser.add_argument(
        "--artist",
        help="Filter by artist",
    )

    search_parser.add_argument(
        "--min-bpm",
        type=float,
    )

    search_parser.add_argument(
        "--max-bpm",
        type=float,
    )

    search_parser.add_argument(
        "--min-energy",
        type=float,
    )

    search_parser.add_argument(
        "--max-energy",
        type=float,
    )

    search_parser.add_argument(
        "--min-dance",
        type=float,
    )

    search_parser.add_argument(
        "--max-dance",
        type=float,
    )

    search_parser.add_argument(
        "--genre",
    )

    search_parser.add_argument(
        "--tag",
    )

    # --------------------------------------------------------
    # artist
    # --------------------------------------------------------

    artist_parser = subparsers.add_parser("artist")

    artist_parser.add_argument(
        "name",
    )

    # --------------------------------------------------------
    # recent
    # --------------------------------------------------------

    recent_parser = subparsers.add_parser("recent")

    recent_parser.add_argument(
        "--limit",
        type=int,
        default=20,
    )

    # --------------------------------------------------------
    # saved
    # --------------------------------------------------------

    subparsers.add_parser("saved")

    # --------------------------------------------------------
    # top
    # --------------------------------------------------------

    top_parser = subparsers.add_parser("top")

    top_parser.add_argument(
        "--period",
        choices=["short", "medium", "long"],
        default="long",
    )

    args = parser.parse_args()

    if args.command == "search":
        rows = advanced_search(
            keyword=args.keyword,
            artist=args.artist,
            min_bpm=args.min_bpm,
            max_bpm=args.max_bpm,
            min_energy=args.min_energy,
            max_energy=args.max_energy,
            min_danceability=args.min_dance,
            max_danceability=args.max_dance,
            genre=args.genre,
            tag=args.tag,
        )

    elif args.command == "artist":
        rows = search_by_artist(args.name)

    elif args.command == "recent":
        rows = recent_tracks(args.limit)

    elif args.command == "saved":
        rows = saved_tracks()

    elif args.command == "top":
        rows = top_tracks(args.period)

    else:
        rows = []

    print_rows(rows)


if __name__ == "__main__":
    main()
