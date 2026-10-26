import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from drill import __version__
from drill.deck import DeckReadError, check_deck


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drill",
        description="Spaced repetition for markdown notes.",
    )
    parser.add_argument("--version", action="version", version=f"drill {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    check = sub.add_parser("check", help="check decks for empty cards and duplicates")
    check.add_argument("dir", type=Path, help="folder of markdown decks")

    review = sub.add_parser("review", help="review the cards that are due")
    review.add_argument("dir", type=Path, help="folder of markdown decks")
    review.add_argument(
        "--new", type=int, default=10, metavar="N", help="new cards per day (default 10)"
    )
    review.add_argument(
        "--limit", type=int, metavar="N", help="maximum cards per session (positive integer)"
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
    if args.command == "check":
        if not args.dir.is_dir():
            print(f"drill: not a directory: {args.dir}", file=sys.stderr)
            return 2
        try:
            issues = check_deck(args.dir)
        except DeckReadError as exc:
            print(f"drill: {exc}", file=sys.stderr)
            return 2
        for issue in issues:
            print(issue)
        if not issues:
            print("No issues found.")
        return int(bool(issues))
    if args.command == "review":
        from drill.session import run_review
        from drill.store import UnsupportedSchemaError
        from drill.term import system_terminal

        if args.limit is not None and args.limit <= 0:
            parser.error("--limit must be a positive integer")
        if not args.dir.is_dir():
            print(f"drill: not a directory: {args.dir}", file=sys.stderr)
            return 2
        db_path = args.db or args.dir / ".drill.db"
        try:
            return run_review(
                args.dir, db_path, max(0, args.new), system_terminal(), date.today(),
                limit=args.limit,
            )
        except (DeckReadError, UnsupportedSchemaError) as exc:
            print(f"drill: {exc}", file=sys.stderr)
        except sqlite3.Error as exc:
            print(f"drill: cannot use database {db_path}: {exc}", file=sys.stderr)
        return 2
    return 0
