"""Review state in a small SQLite file next to the deck.

The markdown files stay the source of truth for content; this file only holds
what has been learned about each card id, plus a review log for stats and fitting.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from drill.fsrs import Grade
from drill.schedule import CardState

SCHEMA_VERSION = 1


class UnsupportedSchemaError(RuntimeError):
    pass


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
        self._undo: tuple[int, str, CardState | None] | None = None
        self.db = sqlite3.connect(self.path, timeout=5.0)
        try:
            self.db.row_factory = sqlite3.Row
            with self.db:
                # Serialize first-time setup and roll it back if interrupted.
                self.db.execute("BEGIN IMMEDIATE")
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            self.db.execute(statement)
                    self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise UnsupportedSchemaError(
                        f"{self.path} has schema version {version}, this drill expects {SCHEMA_VERSION}"
                    )
        except BaseException:
            self.db.close()
            raise

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

    def retention_counts(self, card_ids: set[str]) -> tuple[int, int]:
        rows = self.db.execute(
            "SELECT card_id, SUM(grade > ?), COUNT(*) FROM reviews"
            " WHERE elapsed_days > 0 GROUP BY card_id", (int(Grade.AGAIN),),
        )
        remembered = total = 0
        for card_id, successes, count in rows:
            if card_id in card_ids:
                remembered += successes
                total += count
        return remembered, total

    def review_counts(self, card_ids: set[str], start: date, end: date) -> dict[date, int]:
        rows = self.db.execute(
            "SELECT card_id, date(reviewed_at, 'localtime') AS day, COUNT(*)"
            " FROM reviews WHERE day BETWEEN ? AND ? GROUP BY card_id, day",
            (start.isoformat(), end.isoformat()),
        )
        counts: dict[date, int] = {}
        for card_id, day, count in rows:
            if card_id in card_ids:
                day = date.fromisoformat(day)
                counts[day] = counts.get(day, 0) + count
        return counts

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
            self._save(new)
            row = self.db.execute(
                "INSERT INTO reviews (card_id, reviewed_at, grade, elapsed_days, scheduled_days)"
                " VALUES (?,?,?,?,?)",
                (
                    new.id, when.isoformat(timespec="seconds"), int(grade),
                    max(0, elapsed), (new.due - new.last_review).days,
                ),
            )
        self._undo = (row.lastrowid, new.id, prev)

    def undo(self) -> bool:
        """Undo the latest grade recorded by this open store, at most once."""
        if self._undo is None:
            return False
        review_id, card_id, prev = self._undo
        with self.db:
            # Another session may have recorded a newer review in the meantime.
            removed = self.db.execute(
                "DELETE FROM reviews WHERE id = ? AND id = (SELECT MAX(id) FROM reviews)",
                (review_id,),
            ).rowcount
            if removed:
                if prev is None:
                    self.db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
                else:
                    self._save(prev)
        self._undo = None
        return bool(removed)

    def _save(self, state: CardState) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?,?)",
            (
                state.id, state.deck, state.stability, state.difficulty,
                state.due.isoformat(), state.last_review.isoformat(),
                state.introduced.isoformat(), state.reps, state.lapses,
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
