"""The review loop: pick what is due, show a card, take one keypress per grade."""

from __future__ import annotations

import random
import re
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path
from time import monotonic

from drill.config import deck_settings
from drill.deck import Card, load_deck
from drill.fsrs import DEFAULT_WEIGHTS, Grade
from drill.schedule import CardState, apply_grade, preview_intervals
from drill.store import Store
from drill.term import EOF_KEY, Terminal

MARGIN = "    "
_LEECH_LAPSES = 8
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
    elapsed_seconds: float = 0.0


def build_queue(
    cards: list[Card],
    states: dict[str, CardState],
    today: date,
    new_limit: int,
    new_today: int = 0,
    *, order: str = "due", deck_limits: dict[str, int] | None = None,
) -> list[Card]:
    """Order due cards and the new cards admitted by today's allowance."""
    if order not in {"due", "random", "deck"}:
        raise ValueError(f"unknown review order: {order}")
    due = [c for c in cards if c.id in states and states[c.id].due <= today]
    due.sort(key=lambda c: (states[c.id].due, c.deck, c.line))
    fresh = [c for c in cards if c.id not in states]
    deck_limits = deck_limits or {}
    introduced = Counter(s.deck for s in states.values()
                         if s.introduced == today and s.deck in deck_limits)
    remaining = {deck: max(0, count - introduced[deck])
                 for deck, count in deck_limits.items()}
    shared_today = max(0, new_today - sum(introduced.values()))
    remaining[None] = max(0, new_limit - shared_today)
    queue = list(due)
    for card in fresh:
        budget = card.deck if card.deck in deck_limits else None
        if remaining[budget] > 0:
            queue.append(card)
            remaining[budget] -= 1
    if order == "random":
        random.shuffle(queue)
    elif order == "deck":
        queue.sort(key=lambda c: (c.deck, c.line))
    return queue


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


def _layout(term: Terminal) -> tuple[str, int]:
    margin = MARGIN if term.width >= 40 else ""
    return margin, max(1, min(term.width - len(margin) * 2, 72))


def _write_text(term: Terminal, text: str, *, dim: bool = False) -> None:
    margin, width = _layout(term)
    for line in textwrap.wrap(text, width):
        term.write(margin + (term.dim(line) if dim else line) + "\n")


def render_answer(term: Terminal, answer: str) -> list[str]:
    """Light markdown with hanging lists and whitespace-preserving code wraps."""
    margin, width = _layout(term)
    out: list[str] = []
    para: list[str] = []
    fence = False

    def flush() -> None:
        if para:
            for line in textwrap.wrap(" ".join(para), width):
                out.append(margin + _inline(term, line))
            para.clear()

    def literal(line: str, style) -> None:
        line = line.expandtabs()
        for start in range(0, max(1, len(line)), width):
            out.append(margin + style(line[start : start + width]))

    for line in answer.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            flush()
            fence = not fence
            literal(stripped, term.dim)
        elif fence:
            literal(line, term.accent)
        elif not stripped:
            flush()
            out.append("")
        elif m := _LIST.match(line):
            flush()
            hang = " " * min(len(m.group(0)), width - 1)
            for wrapped in textwrap.wrap(line, width, subsequent_indent=hang):
                out.append(margin + _inline(term, wrapped))
        elif line.startswith((" ", "\t", ">", "|")):
            flush()
            literal(line.rstrip(), lambda text: _inline(term, text))
        else:
            para.append(stripped)
    flush()
    return out


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _days(n: int) -> str:
    return "1d" if n == 1 else f"{n}d"


def _screen(term: Terminal, card: Card, position: int, total: int) -> None:
    term.refresh_width()
    term.clear()
    margin, width = _layout(term)
    count = f"{position} of {total}"
    term.write("\n")
    if len(card.deck) + len(count) + 2 <= width:
        gap = width - len(card.deck) - len(count)
        term.write(f"{margin}{term.dim(card.deck)}{' ' * gap}{term.dim(count)}\n")
    else:
        _write_text(term, card.deck, dim=True)
        _write_text(term, count, dim=True)
    term.write("\n\n")
    for line in textwrap.wrap(card.question, width):
        term.write(margin + term.bold(line) + "\n")
    term.write("\n")


def _ask(term: Terminal, wanted: set[str]) -> str | None:
    """Wait for one of `wanted`; returns None if the user quits."""
    while True:
        key = term.read_key()
        if key in _QUIT:
            return None
        if key.lower() in wanted:
            return key.lower()


def review(
    queue: list[Card], store: Store, term: Terminal, today: date, retention: float = 0.9,
    *, deck_retentions: dict[str, float] | None = None,
    weights: tuple[float, ...] = DEFAULT_WEIGHTS,
) -> Summary:
    summary = Summary()
    queue = list(queue)
    scheduled_count = len(queue)
    started = None
    try:
        states = store.states()
        due = sum(card.id in states for card in queue)
        term.refresh_width()
        term.clear()
        term.write("\n")
        _write_text(term, f"{due} due, {len(queue) - due} new")
        term.write("\n")
        _write_text(term, "Press any key to begin   q to stop", dim=True)
        if term.read_key() in _QUIT:
            summary.finished = False
            return summary
        started = monotonic()
        position = 0
        last_grade = None
        while position < len(queue):
            card = queue[position]
            card_retention = (deck_retentions or {}).get(card.deck, retention)
            practice = position >= scheduled_count
            state = store.get(card.id)
            _screen(term, card, position + 1, len(queue))
            term.write("\n")
            if practice:
                _write_text(term, "Practice only - schedule unchanged", dim=True)
            _write_text(term, "space to show the answer   q to stop", dim=True)
            undo_keys = {"u"} if last_grade is not None else set()
            if undo_keys:
                _write_text(term, "u to undo the last grade", dim=True)
            key = _ask(term, _REVEAL | undo_keys)
            if key is None:
                summary.finished = False
                break
            if key == "u":
                if store.undo():
                    summary.reviewed -= 1
                    summary.counts[last_grade] -= 1
                    if summary.counts[last_grade] == 0:
                        del summary.counts[last_grade]
                    if last_grade == Grade.AGAIN:
                        queue.pop()
                    position -= 1
                last_grade = None
                continue

            _screen(term, card, position + 1, len(queue))
            margin, width = _layout(term)
            _write_text(term, "-" * min(12, width), dim=True)
            term.write("\n")
            for line in render_answer(term, card.answer):
                term.write(line + "\n")
            if state is not None and state.lapses >= _LEECH_LAPSES:
                term.write("\n")
                _write_text(term, f"Leech: {state.lapses} lapses; consider rewriting this card.", dim=True)
            if practice:
                term.write("\n")
                _write_text(term, "Practice only - schedule unchanged", dim=True)
                _write_text(term, "space to continue   q to stop", dim=True)
                if _ask(term, _REVEAL) is None:
                    summary.finished = False
                    break
                last_grade = None
                position += 1
                continue
            days = preview_intervals(state, today, card.id, card_retention, weights)
            labels = [
                f"{term.accent(str(int(g)))} {g.name.lower()} {term.dim(_days(days[g]))}"
                for g in Grade
            ]
            plain = [f"{int(g)} {g.name.lower()} {_days(days[g])}" for g in Grade]
            term.write("\n\n")
            if len("   ".join(plain)) <= width:
                term.write(margin + "   ".join(labels) + "\n")
            else:
                for label, text in zip(labels, plain):
                    if len(text) <= width:
                        term.write(margin + label + "\n")
                    else:
                        _write_text(term, text)
            key = _ask(term, set(_GRADE_KEYS))
            if key is None:
                summary.finished = False
                break

            grade = _GRADE_KEYS[key]
            store.record(state, apply_grade(
                state, card.id, card.deck, grade, today, card_retention, weights,
            ), grade, question=card.question)
            summary.reviewed += 1
            summary.counts[grade] = summary.counts.get(grade, 0) + 1
            if grade == Grade.AGAIN:
                queue.append(card)
            last_grade = grade
            position += 1
    except KeyboardInterrupt:
        summary.finished = False
    finally:
        if started is not None:
            summary.elapsed_seconds = monotonic() - started
    return summary


def _next_line(upcoming: tuple[date, int] | None) -> str:
    if not upcoming:
        return ""
    when, n = upcoming
    return f"Next: {_plural(n, 'card')} on {when.isoformat()}."


def run_review(
    root: Path, db_path: Path | None, new_limit: int, term: Terminal, today: date,
    *, limit: int | None = None, only: str | None = None, order: str = "due",
    retention: float = 0.9,
    weights: tuple[float, ...] = DEFAULT_WEIGHTS,
    deck_rules: dict | None = None,
) -> int:
    folder = root.parent if root.is_file() else root
    # Assign duplicate ids across the whole folder before selecting a subset.
    cards = load_deck(folder)
    if folder != root:
        cards = [c for c in cards if Path(c.deck) == Path(root.stem)]
    if only is not None:
        cards = [c for c in cards if fnmatchcase(c.deck, only)]
    if not cards:
        term.refresh_width()
        selection = f" matching {only!r}" if only is not None else ""
        _write_text(term, f"No cards found{selection} in {root}.")
        _write_text(term, "A card is a '## question' heading followed by its answer.")
        return 1
    live = {c.id for c in cards}
    with Store(db_path or folder / ".drill.db") as store:
        states = store.states()
        decks = {c.deck for c in cards} | {s.deck for s in states.values()}
        settings = {deck: deck_settings(deck, deck_rules or {}) for deck in decks}
        queue = build_queue(
            cards, states, today, new_limit, store.new_introduced_on(today), order=order,
            deck_limits={deck: values["new_per_day"] for deck, values in settings.items()
                         if "new_per_day" in values},
        )
        if limit is not None:
            queue = queue[:limit]
        if not queue:
            term.refresh_width()
            term.write("\n")
            _write_text(term, "Nothing due.")
            _write_text(term, _next_line(next_due(states, live, today)))
            term.write("\n")
            return 0
        summary = review(queue, store, term, today, retention, weights=weights,
                         deck_retentions={deck: values["retention"]
                                          for deck, values in settings.items()
                                          if "retention" in values})
        term.refresh_width()
        term.clear()
        verb = "Done" if summary.finished else "Stopped"
        term.write("\n")
        _write_text(term, f"{verb}. {_plural(summary.reviewed, 'card')} reviewed.")
        if summary.counts:
            parts = [f"{summary.counts[g]} {g.name.lower()}" for g in Grade if g in summary.counts]
            _write_text(term, ", ".join(parts), dim=True)
        minutes, seconds = divmod(int(summary.elapsed_seconds), 60)
        again_rate = (f"{summary.counts.get(Grade.AGAIN, 0) / summary.reviewed:.1%}"
                      if summary.reviewed else "n/a")
        _write_text(term, f"Time: {minutes}m {seconds}s", dim=True)
        _write_text(term, f"Again-rate: {again_rate}", dim=True)
        _write_text(term, f"Streak: {_plural(store.review_streak(today), 'day')}", dim=True)
        states = store.states()
        # Retained grades cover the queue prefix; practice repeats add no grades.
        leeches = {c.id for c in queue[:summary.reviewed]
                   if c.id in states and states[c.id].lapses >= _LEECH_LAPSES}
        if leeches:
            label = "leech" if len(leeches) == 1 else "leeches"
            _write_text(term, f"{len(leeches)} {label} reviewed (8+ lapses); consider rewriting.", dim=True)
        upcoming = next_due(states, live, today)
        if upcoming:
            term.write("\n")
            _write_text(term, _next_line(upcoming))
        term.write("\n")
    return 0
