"""Review state in a small SQLite file next to the deck.

The markdown files stay the source of truth for content; this file only holds
what has been learned about each card id, plus an append-only review log that
later features (undo, stats, weight fitting) read.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from drill.fsrs import Grade
from drill.schedule import CardState

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE cards (
    id          TEXT PRIMARY KEY,
    deck        TEXT NOT NULL,
    stability   REAL NOT NULL,
    difficulty  REAL NOT NULL,
    due         TEXT NOT NULL,
    last_review TEXT NOT NULL,
    introduced  TEXT NOT NULL,
    reps        INTEGER NOT NULL,
    lapses      INTEGER NOT NULL
);
CREATE INDEX cards_due ON cards (due);
CREATE TABLE reviews (
    id              INTEGER PRIMARY KEY,
    card_id         TEXT NOT NULL,
    reviewed_at     TEXT NOT NULL,
    grade           INTEGER NOT NULL,
    elapsed_days    INTEGER NOT NULL,
    scheduled_days  INTEGER NOT NULL
);
CREATE INDEX reviews_card ON reviews (card_id, id);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            with self.db:
                self.db.executescript(_SCHEMA)
                self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            self.db.close()
            raise RuntimeError(
                f"{self.path} has schema version {version}, this drill expects {SCHEMA_VERSION}"
            )

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def get(self, card_id: str) -> CardState | None:
        row = self.db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return _state(row) if row else None

    def states(self) -> dict[str, CardState]:
        rows = self.db.execute("SELECT * FROM cards").fetchall()
        return {row["id"]: _state(row) for row in rows}

    def new_introduced_on(self, day: date) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) FROM cards WHERE introduced = ?", (day.isoformat(),)
        ).fetchone()
        return row[0]

    def record(
        self,
        prev: CardState | None,
        new: CardState,
        grade: Grade,
        when: datetime | None = None,
    ) -> None:
        """Save the new state and log the review in one transaction."""
        when = when or datetime.now(timezone.utc)
        elapsed = (new.last_review - prev.last_review).days if prev else 0
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new.id, new.deck, new.stability, new.difficulty,
                    new.due.isoformat(), new.last_review.isoformat(),
                    new.introduced.isoformat(), new.reps, new.lapses,
                ),
            )
            self.db.execute(
                "INSERT INTO reviews (card_id, reviewed_at, grade, elapsed_days, scheduled_days)"
                " VALUES (?,?,?,?,?)",
                (
                    new.id, when.isoformat(timespec="seconds"), int(grade),
                    max(0, elapsed), (new.due - new.last_review).days,
                ),
            )


def _state(row: sqlite3.Row) -> CardState:
    return CardState(
        id=row["id"],
        deck=row["deck"],
        stability=row["stability"],
        difficulty=row["difficulty"],
        due=date.fromisoformat(row["due"]),
        last_review=date.fromisoformat(row["last_review"]),
        introduced=date.fromisoformat(row["introduced"]),
        reps=row["reps"],
        lapses=row["lapses"],
    )
