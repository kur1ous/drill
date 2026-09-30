import sqlite3
from datetime import date, datetime, timezone

import pytest

from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.store import Store

DAY1 = date(2026, 3, 1)
DAY2 = date(2026, 3, 5)


def test_new_store_has_no_cards(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.get("nope") is None
        assert store.states() == {}
        assert store.new_introduced_on(DAY1) == 0


def test_state_round_trips_through_disk(tmp_path):
    path = tmp_path / "s.db"
    new = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    with Store(path) as store:
        store.record(None, new, Grade.GOOD)
    with Store(path) as store:
        assert store.get("abc") == new
        assert store.states() == {"abc": new}


def test_second_review_replaces_state_and_appends_log(tmp_path):
    first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    second = apply_grade(first, "abc", "bio", Grade.HARD, DAY2)
    with Store(tmp_path / "s.db") as store:
        store.record(None, first, Grade.GOOD)
        store.record(first, second, Grade.HARD)
        assert store.get("abc") == second
        rows = store.db.execute(
            "SELECT grade, elapsed_days, scheduled_days FROM reviews ORDER BY id"
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        (3, 0, 4),
        (2, 4, (second.due - DAY2).days),
    ]


def test_review_log_keeps_timestamp(tmp_path):
    new = apply_grade(None, "abc", "bio", Grade.EASY, DAY1)
    when = datetime(2026, 3, 1, 9, 30, tzinfo=timezone.utc)
    with Store(tmp_path / "s.db") as store:
        store.record(None, new, Grade.EASY, when)
        stamp = store.db.execute("SELECT reviewed_at FROM reviews").fetchone()[0]
    assert stamp == "2026-03-01T09:30:00+00:00"


def test_new_introduced_counts_only_that_day(tmp_path):
    a = apply_grade(None, "a", "d", Grade.GOOD, DAY1)
    b = apply_grade(None, "b", "d", Grade.GOOD, DAY1)
    c = apply_grade(None, "c", "d", Grade.GOOD, DAY2)
    a2 = apply_grade(a, "a", "d", Grade.GOOD, DAY2)
    with Store(tmp_path / "s.db") as store:
        for prev, s in ((None, a), (None, b), (None, c), (a, a2)):
            store.record(prev, s, Grade.GOOD)
        assert store.new_introduced_on(DAY1) == 2
        assert store.new_introduced_on(DAY2) == 1


def test_refuses_a_newer_schema(tmp_path):
    path = tmp_path / "s.db"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 99")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="schema version 99"):
        Store(path)
