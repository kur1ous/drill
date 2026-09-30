"""FSRS-4.5 memory model.

Formulas and default weights are from the published algorithm description:
  https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm
  (FSRS-4.5; the power-law forgetting curve replaced the exponential of v4)
and the papers cited there: Ye, Su, Cao, "A Stochastic Shortest Path Algorithm
for Optimizing Spaced Repetition Scheduling", KDD 2022.

Each card has two numbers: stability S (days for retrievability to fall to
90%) and difficulty D (1 easiest to 10 hardest).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum


class Grade(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.5701, 1.4436, 4.1386, 10.9355,  # w0-w3: initial stability per grade
    5.1443, 1.2006,                   # w4, w5: initial difficulty
    0.8627, 0.0362,                   # w6, w7: difficulty update, mean reversion
    1.629, 0.1342, 1.0166,            # w8-w10: stability after a recall
    2.1174, 0.0839, 0.3204, 1.4676,   # w11-w14: stability after a lapse
    0.219, 2.8237,                    # w15, w16: hard penalty, easy bonus
)

DECAY = -0.5
FACTOR = 19 / 81  # chosen so that R(S, S) is exactly 0.9

S_MIN = 0.01
MAX_INTERVAL = 36500


@dataclass(frozen=True)
class Memory:
    stability: float
    difficulty: float


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after `elapsed_days`: (1 + FACTOR * t / S) ** DECAY."""
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def interval(stability: float, retention: float = 0.9) -> int:
    """Whole days until retrievability drops to `retention` (at least 1)."""
    days = stability / FACTOR * (retention ** (1 / DECAY) - 1)
    return min(MAX_INTERVAL, max(1, math.floor(days + 0.5)))


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def initial_difficulty(grade: Grade, w: tuple[float, ...] = DEFAULT_WEIGHTS) -> float:
    return _clamp_d(w[4] - (grade - 3) * w[5])


def initial(grade: Grade, w: tuple[float, ...] = DEFAULT_WEIGHTS) -> Memory:
    """Memory state after the very first review of a card."""
    return Memory(max(S_MIN, w[grade - 1]), initial_difficulty(grade, w))


def next_difficulty(d: float, grade: Grade, w: tuple[float, ...] = DEFAULT_WEIGHTS) -> float:
    stepped = d - w[6] * (grade - 3)
    # Pull back toward the difficulty of a "good" first review so D can't lock at 1 or 10.
    return _clamp_d(w[7] * initial_difficulty(Grade.GOOD, w) + (1 - w[7]) * stepped)


def next_memory(
    mem: Memory, grade: Grade, elapsed_days: float, w: tuple[float, ...] = DEFAULT_WEIGHTS
) -> Memory:
    """Memory state after reviewing a card last seen `elapsed_days` ago."""
    s, d = mem.stability, mem.difficulty
    r = retrievability(elapsed_days, s)
    if grade == Grade.AGAIN:
        new_s = w[11] * d ** -w[12] * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])
        # A lapse must not raise stability (clamp as in FSRS-5).
        new_s = min(new_s, s)
    else:
        hard = w[15] if grade == Grade.HARD else 1.0
        easy = w[16] if grade == Grade.EASY else 1.0
        growth = (
            math.exp(w[8]) * (11 - d) * s ** -w[9] * math.expm1((1 - r) * w[10]) * hard * easy
        )
        new_s = s * (1 + growth)
    return Memory(max(S_MIN, new_s), next_difficulty(d, grade, w))
