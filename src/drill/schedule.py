"""Turn a grade into an updated card state, working in whole calendar days.

Due dates are dates, not timestamps: a card graded at 9pm and due in four days
shows up on the morning of day four, not the evening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from random import Random

from drill import fsrs
from drill.fsrs import Grade


@dataclass(frozen=True)
class CardState:
    id: str
    deck: str
    stability: float
    difficulty: float
    due: date
    last_review: date
    introduced: date  # day of the first review; counts against the daily new-card limit
    reps: int = 1
    lapses: int = 0


def _next_memory(
    state: CardState | None, grade: Grade, today: date, weights: tuple[float, ...],
) -> fsrs.Memory:
    if state is None:
        return fsrs.initial(grade, weights)
    elapsed = max(0, (today - state.last_review).days)
    return fsrs.next_memory(fsrs.Memory(state.stability, state.difficulty), grade, elapsed, weights)


def apply_grade(
    state: CardState | None, card_id: str, deck: str, grade: Grade, today: date,
    retention: float = 0.9,
    weights: tuple[float, ...] = fsrs.DEFAULT_WEIGHTS,
) -> CardState:
    """New state after grading. `state` is None for a card never reviewed."""
    mem = _next_memory(state, grade, today, weights)
    if state is None:
        lapses = 0
        reps = 1
        introduced = today
    else:
        lapses = state.lapses + (grade == Grade.AGAIN)
        reps = state.reps + 1
        introduced = state.introduced
    days = preview_intervals(state, today, card_id, retention, weights)[grade]
    return CardState(
        id=card_id,
        deck=deck,
        stability=mem.stability,
        difficulty=mem.difficulty,
        due=today + timedelta(days=days),
        last_review=today,
        introduced=introduced,
        reps=reps,
        lapses=lapses,
    )


def preview_intervals(
    state: CardState | None, today: date, card_id: str, retention: float = 0.9,
    weights: tuple[float, ...] = fsrs.DEFAULT_WEIGHTS,
) -> dict[Grade, int]:
    """Days until next review for each possible grade, for the answer screen."""
    out = {grade: fsrs.interval(_next_memory(state, grade, today, weights).stability, retention)
           for grade in Grade}
    # A local, string-seeded generator keeps previews and undo/regrade reproducible.
    rng = Random(f"{card_id}:{state.reps + 1 if state else 1}")
    for grade, days in out.items():
        if days > 2:
            spread = max(1, days // 20)
            out[grade] = rng.randint(max(3, days - spread), min(fsrs.MAX_INTERVAL, days + spread))
    # Reserve room below the cap so hard, good and easy stay a day apart.
    out[Grade.HARD] = min(out[Grade.HARD], fsrs.MAX_INTERVAL - 2)
    out[Grade.GOOD] = min(max(out[Grade.GOOD], out[Grade.HARD] + 1), fsrs.MAX_INTERVAL - 1)
    out[Grade.EASY] = max(out[Grade.EASY], out[Grade.GOOD] + 1)
    out[Grade.AGAIN] = min(out[Grade.AGAIN], out[Grade.HARD])
    return out

