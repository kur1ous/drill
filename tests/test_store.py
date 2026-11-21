import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime, time, timedelta, timezone
from threading import Event

import pytest

from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.store import Store

DAY1 = date(2026, 3, 1)
DAY2 = date(2026, 3, 5)


def version_one_database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE cards (
            id TEXT PRIMARY KEY, deck TEXT NOT NULL, stability REAL NOT NULL,
            difficulty REAL NOT NULL, due TEXT NOT NULL, last_review TEXT NOT NULL,
            introduced TEXT NOT NULL, reps INTEGER NOT NULL, lapses INTEGER NOT NULL
        );
        CREATE INDEX cards_due ON cards (due);
        CREATE TABLE reviews (
            id INTEGER PRIMARY KEY, card_id TEXT NOT NULL, reviewed_at TEXT NOT NULL,
            grade INTEGER NOT NULL, elapsed_days INTEGER NOT NULL, scheduled_days INTEGER NOT NULL
        );
        CREATE INDEX reviews_card ON reviews (card_id, id);
        INSERT INTO cards VALUES ('abc', 'bio', 4, 5, '2026-03-05', '2026-03-01', '2026-03-01', 1, 0);
        INSERT INTO reviews VALUES (1, 'abc', '2026-03-01T12:00:00+00:00', 3, 0, 4);
        PRAGMA user_version = 1;
    """)
    db.close()


def version_two_database(path):
    version_one_database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE cards ADD COLUMN question TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE cards SET question = 'Saved question'")
        db.execute("PRAGMA user_version = 2")


@pytest.mark.parametrize("version", [1, 2])
def test_upgrade_preserves_history_and_runs_once(tmp_path, version):
    path = tmp_path / "s.db"
    (version_one_database if version == 1 else version_two_database)(path)
    db = sqlite3.connect(path)
    before = db.execute("SELECT * FROM cards").fetchone()
    reviews = db.execute("SELECT * FROM reviews").fetchall()
    db.close()
    for _ in range(2):
        with Store(path) as store:
            assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3
            expected = (*before, "", 0) if version == 1 else (*before, 0)
            assert tuple(store.db.execute("SELECT * FROM cards").fetchone()) == expected
            assert [tuple(r) for r in store.db.execute("SELECT * FROM reviews")] == reviews
            indexes = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            assert {"cards_due", "reviews_card"} <= indexes
            assert store.get("abc").due == DAY2


@pytest.mark.parametrize("version", [0, 1, 2])
@pytest.mark.parametrize("fail_after", ["ALTER TABLE", "PRAGMA user_version = 3"])
def test_failed_migration_rolls_back_schema_and_version(tmp_path, monkeypatch, version, fail_after):
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if fail_after in sql:
                raise sqlite3.OperationalError("migration interrupted")
            return result

    path = tmp_path / "s.db"
    if version:
        (version_one_database if version == 1 else version_two_database)(path)
    connection = sqlite3.connect(path, factory=FailingConnection)
    before = list(connection.iterdump())
    with monkeypatch.context() as patch:
        patch.setattr("drill.store.sqlite3.connect", lambda path, **kwargs: connection)
        with pytest.raises(sqlite3.OperationalError, match="migration interrupted"):
            Store(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == version
    assert list(db.iterdump()) == before
    db.close()
    with Store(path) as store:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_question_snapshot_survives_reopen_regrade_and_undo(tmp_path):
    path = tmp_path / "s.db"
    first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    second = apply_grade(first, "abc", "bio", Grade.HARD, DAY2)
    with Store(path) as store:
        store.record(None, first, Grade.GOOD, question="What's β?")
    with Store(path) as store:
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "What's β?"
        store.record(first, second, Grade.HARD, question="What's **β**?")
        assert store.undo()
        assert store.get("abc") == first
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "What's **β**?"
        store.record(first, second, Grade.HARD)
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "What's **β**?"


def test_failed_review_rolls_back_question_snapshot(tmp_path):
    first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    second = apply_grade(first, "abc", "bio", Grade.HARD, DAY2)
    with Store(tmp_path / "s.db") as store:
        store.record(None, first, Grade.GOOD, question="Old?")
        store.db.execute("CREATE TRIGGER reject_review BEFORE INSERT ON reviews "
                         "BEGIN SELECT RAISE(ABORT, 'review failed'); END")
        with pytest.raises(sqlite3.IntegrityError, match="review failed"):
            store.record(first, second, Grade.HARD, question="New?")
        assert store.get("abc") == first
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "Old?"
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


@pytest.mark.parametrize("offsets, expected", [
    ([], 0), ([0, 0], 1), ([0, 1, 2, 4], 3),
    ([1, 2, 3], 3), ([2, 3], 0), ([-1, 0, 1], 2),
])
def test_review_streak_counts_consecutive_local_days(tmp_path, offsets, expected):
    with Store(tmp_path / "s.db") as store:
        for i, offset in enumerate(offsets):
            day = DAY1 - timedelta(days=offset)
            state = apply_grade(None, str(i), "deck", Grade.GOOD, day)
            when = datetime.combine(day, time(0, 30)).astimezone().astimezone(timezone.utc)
            store.record(None, state, Grade.GOOD, when)
        assert store.review_streak(DAY1) == expected


def test_review_streak_recalculates_after_undo(tmp_path):
    with Store(tmp_path / "s.db") as store:
        for offset in [2, 0]:
            day = DAY1 - timedelta(days=offset)
            state = apply_grade(None, str(offset), "deck", Grade.AGAIN, day)
            store.record(None, state, Grade.AGAIN,
                         datetime.combine(day, time(12)).astimezone())
        assert store.review_streak(DAY1) == 1
        assert store.undo()
        assert store.review_streak(DAY1) == 0


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


def test_failed_initialization_closes_connection(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    path.write_bytes(b"not a sqlite database")
    connection = sqlite3.connect(path)
    monkeypatch.setattr("drill.store.sqlite3.connect", lambda path, **kwargs: connection)
    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        Store(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_undo_new_card_restores_daily_allowance(tmp_path):
    path = tmp_path / "s.db"
    with Store(path) as store:
        assert not store.undo()
        new = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
        store.record(None, new, Grade.GOOD)
        assert store.undo()
        assert not store.undo()
        assert store.new_introduced_on(DAY1) == 0
    with Store(path) as store:
        assert store.states() == {}
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_undo_restores_exact_state_and_preserves_earlier_reviews(tmp_path):
    path = tmp_path / "s.db"
    first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    second = apply_grade(first, "abc", "renamed", Grade.AGAIN, DAY2)
    with Store(path) as store:
        store.record(None, first, Grade.GOOD)
        before = [tuple(r) for r in store.db.execute("SELECT * FROM reviews")]
        store.record(first, second, Grade.AGAIN)
        assert store.undo()
        assert not store.undo()
    with Store(path) as store:
        assert store.get("abc") == first
        assert [tuple(r) for r in store.db.execute("SELECT * FROM reviews")] == before
        assert not store.undo()


def test_undo_rolls_back_if_restoring_state_fails(tmp_path):
    with Store(tmp_path / "s.db") as store:
        first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
        second = apply_grade(first, "abc", "bio", Grade.AGAIN, DAY2)
        store.record(None, first, Grade.GOOD)
        store.record(first, second, Grade.AGAIN)
        store.db.execute(
            "CREATE TRIGGER reject_restore BEFORE INSERT ON cards "
            "BEGIN SELECT RAISE(ABORT, 'restore failed'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="restore failed"):
            store.undo()
        assert store.get("abc") == second
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2
        store.db.execute("DROP TRIGGER reject_restore")
        assert store.undo()
        assert store.get("abc") == first


def test_undo_does_not_remove_another_sessions_review(tmp_path):
    path = tmp_path / "s.db"
    with Store(path) as store, Store(path) as other:
        first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
        second = apply_grade(first, "abc", "bio", Grade.HARD, DAY2)
        store.record(None, first, Grade.GOOD)
        other.record(first, second, Grade.HARD)
        assert not store.undo()
        assert store.get("abc") == second
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


@pytest.mark.parametrize("during_setup", [False, True])
def test_busy_store_waits_for_other_connection(tmp_path, during_setup):
    path = tmp_path / "s.db"
    ready, blocked = Event(), Event()
    new = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)

    def writer():
        if during_setup:
            ready.set()
            assert blocked.wait(2)
        with Store(path) as store:
            assert store.db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
            if not during_setup:
                ready.set()
                assert blocked.wait(2)
            store.record(None, new, Grade.GOOD)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert ready.wait(2)
        blocker = sqlite3.connect(path)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            blocked.set()
            with pytest.raises(TimeoutError):
                future.result(timeout=0.05)
        finally:
            blocker.rollback()
            blocker.close()
            blocked.set()
        future.result(timeout=2)
    with Store(path) as store:
        assert store.get("abc") == new
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_interrupted_setup_closes_and_rolls_back(tmp_path, monkeypatch):
    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            result = super().execute(sql, *args)
            if "CREATE TABLE cards" in sql:
                raise KeyboardInterrupt
            return result

    path = tmp_path / "s.db"
    connection = sqlite3.connect(path, factory=InterruptedConnection)
    with monkeypatch.context() as patch:
        patch.setattr("drill.store.sqlite3.connect", lambda path, **kwargs: connection)
        with pytest.raises(KeyboardInterrupt):
            Store(path)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    with Store(path) as store:
        assert store.states() == {}
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_interrupted_grade_rolls_back_and_releases_lock(tmp_path, monkeypatch):
    path = tmp_path / "s.db"
    first = apply_grade(None, "abc", "bio", Grade.GOOD, DAY1)
    second = apply_grade(first, "abc", "bio", Grade.HARD, DAY2)
    with pytest.raises(KeyboardInterrupt):
        with Store(path) as store:
            store.record(None, first, Grade.GOOD)
            save = store._save

            def interrupt(state):
                save(state)
                raise KeyboardInterrupt

            monkeypatch.setattr(store, "_save", interrupt)
            store.record(first, second, Grade.HARD)
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        store.db.execute("SELECT 1")
    with Store(path) as other:
        assert other.get("abc") == first
        assert other.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
        other.record(first, second, Grade.HARD)
        assert other.get("abc") == second
