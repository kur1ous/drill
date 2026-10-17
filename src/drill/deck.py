"""Markdown decks: one `## question` heading per card, the body is the answer."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_QUESTION = re.compile(r"^##[ \t]+(\S.*?)(?:[ \t]+#+)?[ \t]*$")
_EMPHASIS = re.compile(r"[*_`]+")
_SPACE = re.compile(r"\s+")


class DeckReadError(Exception):
    pass


@dataclass(frozen=True)
class Card:
    id: str
    deck: str  # path relative to the deck root, without the .md suffix
    question: str
    answer: str
    line: int  # 1-based line of the heading, for error messages and editors


def normalize(question: str) -> str:
    """Reduce a question to what identifies it, ignoring cosmetic edits.

    Case, spacing, markdown emphasis and a trailing ?/./!/: do not change the
    result. Other punctuation stays: in notes full of math, `a^2` and `a_2`
    (and `\\frac{a}{b}` vs `\\frac{b}{a}`) must not collapse into one card.
    """
    text = unicodedata.normalize("NFKC", question).casefold()
    text = _EMPHASIS.sub("", text)
    text = _SPACE.sub(" ", text).strip()
    return text.rstrip("?.!:").rstrip()


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]


def parse_markdown(text: str, deck: str = "", *, include_empty: bool = False) -> list[Card]:
    """Parse one file's text. Ids here are provisional; see `load_deck`."""
    cards: list[Card] = []
    question: str | None = None
    start = 0
    body: list[str] = []
    fence: str | None = None

    def flush() -> None:
        if question is None:
            return
        answer = "\n".join(body).strip("\n").rstrip()
        # A bare heading is a section title, not a card.
        if answer.strip() or include_empty:
            cards.append(Card(_digest(normalize(question)), deck, question, answer, start))

    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = raw.rstrip("\r")
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)
            if fence is None:
                fence = marker
            elif (
                marker[0] == fence[0]
                and len(marker) >= len(fence)
                and not line[m.end():].strip()
            ):
                fence = None
        elif fence is None:
            if line.startswith("# ") or line == "#":
                # A top-level title ends the previous card.
                flush()
                question = None
                body = []
                continue
            q = _QUESTION.match(line)
            if q:
                flush()
                question, start, body = q.group(1), number, []
                continue
        if question is not None:
            body.append(line)
    flush()
    return cards


def _read_decks(root: Path | str) -> Iterator[tuple[Path, str]]:
    root = Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    for path in sorted(root.rglob("*.md"), key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DeckReadError(f"cannot read deck {path}: expected UTF-8 text") from exc
        except OSError as exc:
            raise DeckReadError(f"cannot read deck {path}: {exc.strerror or exc}") from exc
        yield rel, text


def check_deck(root: Path | str) -> list[str]:
    issues: list[str] = []
    seen: dict[str, str] = {}
    for rel, text in _read_decks(root):
        cards = parse_markdown(text, include_empty=True)
        if not any(card.answer.strip() for card in cards):
            issues.append(f"{rel.as_posix()}:1: no cards")
        for card in cards:
            location = f"{rel.as_posix()}:{card.line}"
            if not card.answer.strip():
                issues.append(f"{location}: empty body for {card.question!r}")
            if card.question in seen:
                issues.append(
                    f"{location}: duplicate question {card.question!r} "
                    f"(first at {seen[card.question]})"
                )
            else:
                seen[card.question] = location
    return issues


def load_deck(root: Path | str) -> list[Card]:
    """Load every .md file under `root`, in path order.

    A card's id is a hash of its normalized question alone, so rewriting the
    answer, moving the file, or fixing case and punctuation in the question
    keeps its review history. If two cards share a question (same file or not)
    the first keeps the plain id and later ones are salted with their file and
    position, so each still gets its own schedule.
    """
    cards: list[Card] = []
    seen: dict[str, int] = {}
    for rel, text in _read_decks(root):
        deck = rel.with_suffix("").as_posix()
        for card in parse_markdown(text, deck):
            n = seen.get(card.id, 0)
            seen[card.id] = n + 1
            if n:
                card = Card(
                    _digest(normalize(card.question), deck, str(n)),
                    card.deck, card.question, card.answer, card.line,
                )
            cards.append(card)
    return cards
