"""The review loop: pick what is due, show a card, take one keypress per grade."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from drill.deck import Card, load_deck
from drill.fsrs import Grade
from drill.schedule import CardState, apply_grade, preview_intervals
from drill.store import Store
from drill.term import EOF_KEY, Terminal

MARGIN = "    "
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_GRADE_KEYS = {"1": Grade.AGAIN, "2": Grade.HARD, "3": Grade.GOOD, "4": Grade.EASY}
_REVEAL = {" ", "\r", "\n"}
_QUIT = {"q", "\x03", EOF_KEY}  # q, Ctrl-C, and a closed stdin


@dataclass
class Summary:
    reviewed: int = 0
    counts: dict[Grade, int] = field(default_factory=dict)
    finished: bool = True


def build_queue(
    cards: list[Card],
    states: dict[str, CardState],
    today: date,
    new_limit: int,
    new_today: int = 0,
) -> list[Card]:
    """Due cards oldest first, then as many new cards as today's limit allows."""
    due = [c for c in cards if c.id in states and states[c.id].due <= today]
    due.sort(key=lambda c: (states[c.id].due, c.deck, c.line))
    fresh = [c for c in cards if c.id not in states]
    return due + fresh[: max(0, new_limit - new_today)]


def next_due(
    states: dict[str, CardState], live_ids: set[str], today: date
) -> tuple[date, int] | None:
    """Earliest future due date among cards still in the deck, and how many fall on it."""
    future = sorted(s.due for i, s in states.items() if i in live_ids and s.due > today)
    return (future[0], future.count(future[0])) if future else None


def _inline(term: Terminal, text: str) -> str:
    if not term.ansi:
        return text
    text = _BOLD.sub(lambda m: term.bold(m.group(1)), text)
    return _CODE.sub(lambda m: term.accent(m.group(1)), text)


def render_answer(term: Terminal, answer: str) -> list[str]:
    """Light markdown: wrap prose and list items, leave code and tables alone."""
    width = max(30, min(term.width - len(MARGIN) * 2, 72))
    out: list[str] = []
    para: list[str] = []
    fence = False

    def flush() -> None:
        if para:
            for line in textwrap.wrap(" ".join(para), width):
                out.append(MARGIN + _inline(term, line))
            para.clear()

    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            flush()
            fence = not fence
            out.append(MARGIN + term.dim(stripped))
        elif fence:
            out.append(MARGIN + term.accent(line))
        elif not stripped:
            flush()
            out.append("")
        elif m := _LIST.match(line):
            flush()
            hang = " " * len(m.group(0))
            for wrapped in textwrap.wrap(line, width, subsequent_indent=hang):
                out.append(MARGIN + _inline(term, wrapped))
        elif line.startswith((" ", "\t", ">", "|")):
            flush()
            out.append(MARGIN + _inline(term, line.rstrip()))
        else:
            para.append(stripped)
    flush()
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _days(n: int) -> str:
    return "1d" if n == 1 else f"{n}d"


def _screen(term: Terminal, card: Card, position: int, total: int) -> None:
    term.clear()
    count = f"{position} of {total}"
    gap = max(2, min(term.width, 76) - len(MARGIN) - len(card.deck) - len(count))
    term.write(f"\n{MARGIN}{term.dim(card.deck)}{' ' * gap}{term.dim(count)}\n\n\n")
    for line in textwrap.wrap(card.question, max(30, min(term.width - 8, 72))):
        term.write(MARGIN + term.bold(line) + "\n")
    term.write("\n")


def _ask(term: Terminal, wanted: set[str]) -> str | None:
    """Wait for one of `wanted`; returns None if the user quits."""
    while True:
        key = term.read_key()
        if key in _QUIT:
            return None
        if key.lower() in wanted:
            return key.lower()


def review(queue: list[Card], store: Store, term: Terminal, today: date) -> Summary:
    summary = Summary()
    try:
        for position, card in enumerate(queue, start=1):
            state = store.get(card.id)
            _screen(term, card, position, len(queue))
            term.write(f"\n{MARGIN}{term.dim('space to show the answer   q to stop')}\n")
            if _ask(term, _REVEAL) is None:
                summary.finished = False
                break

            _screen(term, card, position, len(queue))
            term.write(MARGIN + term.dim("-" * 12) + "\n\n")
            for line in render_answer(term, card.answer):
                term.write(line + "\n")
            days = preview_intervals(state, today)
            labels = "   ".join(
                f"{term.accent(str(int(g)))} {g.name.lower()} {term.dim(_days(days[g]))}"
                for g in Grade
            )
            term.write(f"\n\n{MARGIN}{labels}\n")
            key = _ask(term, set(_GRADE_KEYS))
            if key is None:
                summary.finished = False
                break

            grade = _GRADE_KEYS[key]
            store.record(state, apply_grade(state, card.id, card.deck, grade, today), grade)
            summary.reviewed += 1
            summary.counts[grade] = summary.counts.get(grade, 0) + 1
    except KeyboardInterrupt:
        summary.finished = False
    return summary


def _next_line(upcoming: tuple[date, int] | None) -> str:
    if not upcoming:
        return ""
    when, n = upcoming
    return f"{MARGIN}Next: {_plural(n, 'card')} on {when.isoformat()}.\n"


def run_review(
    root: Path, db_path: Path | None, new_limit: int, term: Terminal, today: date
) -> int:
    cards = load_deck(root)
    if not cards:
        term.write(
            f"No cards found under {root}.\n"
            "A card is a '## question' heading followed by its answer.\n"
        )
        return 1
    live = {c.id for c in cards}
    with Store(db_path or Path(root) / ".drill.db") as store:
        states = store.states()
        queue = build_queue(cards, states, today, new_limit, store.new_introduced_on(today))
        if not queue:
            term.write(f"\n{MARGIN}Nothing due.\n")
            term.write(_next_line(next_due(states, live, today)) + "\n")
            return 0
        summary = review(queue, store, term, today)
        term.clear()
        verb = "Done" if summary.finished else "Stopped"
        term.write(f"\n{MARGIN}{verb}. {_plural(summary.reviewed, 'card')} reviewed.\n")
        if summary.counts:
            parts = [f"{summary.counts[g]} {g.name.lower()}" for g in Grade if g in summary.counts]
            term.write(f"{MARGIN}{term.dim(', '.join(parts))}\n")
        upcoming = next_due(store.states(), live, today)
        if upcoming:
            term.write("\n" + _next_line(upcoming))
        term.write("\n")
    return 0
