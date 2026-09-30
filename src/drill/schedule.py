"""Turn a grade into an updated card state, working in whole calendar days.

Due dates are dates, not timestamps: a card graded at 9pm and due in four days
shows up on the morning of day four, not the evening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

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


def apply_grade(
    state: CardState | None, card_id: str, deck: str, grade: Grade, today: date
) -> CardState:
    """New state after grading. `state` is None for a card never reviewed."""
    if state is None:
        mem = fsrs.initial(grade)
        lapses = 0
        reps = 1
        introduced = today
    else:
        elapsed = max(0, (today - state.last_review).days)
        mem = fsrs.next_memory(fsrs.Memory(state.stability, state.difficulty), grade, elapsed)
        lapses = state.lapses + (grade == Grade.AGAIN)
        reps = state.reps + 1
        introduced = state.introduced
    days = fsrs.interval(mem.stability)
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


def preview_intervals(state: CardState | None, today: date) -> dict[Grade, int]:
    """Days until next review for each possible grade, for the answer screen."""
    out = {}
    for grade in Grade:
        after = apply_grade(state, state.id if state else "", "", grade, today)
        out[grade] = (after.due - today).days
    return out

