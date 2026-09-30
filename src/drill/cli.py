import argparse
import sys
from datetime import date
from pathlib import Path

from drill import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drill",
        description="Spaced repetition for markdown notes.",
    )
    parser.add_argument("--version", action="version", version=f"drill {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    review = sub.add_parser("review", help="review the cards that are due")
    review.add_argument("dir", type=Path, help="folder of markdown decks")
    review.add_argument(
        "--new", type=int, default=10, metavar="N", help="new cards per day (default 10)"
    )
    review.add_argument(
        "--db", type=Path, metavar="FILE", help="state file (default: DIR/.drill.db)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    if args.command == "review":
        from drill.session import run_review
        from drill.term import system_terminal

        if not args.dir.is_dir():
            print(f"drill: not a directory: {args.dir}", file=sys.stderr)
            return 2
        return run_review(args.dir, args.db, max(0, args.new), system_terminal(), date.today())
    return 0
