from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import re

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.store import Store
from drill.stats import review_heatmap
from drill.term import Terminal


@pytest.mark.parametrize("questions", [[], ["One", "Two"]])
def test_stats_without_reviews(tmp_path, capsys, questions):
    (tmp_path / "notes.md").write_text(
        "".join(f"## {question}\nAnswer\n" for question in questions), encoding="utf-8",
    )
    assert main(["stats", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert output.split("\n\n")[0] + "\n" == (
        f"Cards: {len(questions)}\n  new: {len(questions)}\n"
        "  learning: 0\n  young: 0\n  mature: 0\n"
        "Due today (including overdue): 0\nRetention: n/a (0 reviews)\n"
    )
    assert "Reviews per day (12 weeks, local dates)" in output
    rows = output.splitlines()[-8:-1]
    assert len(rows) == 7
    assert all(set(row[4:]) <= {".", " "} for row in rows)
    assert "\x1b" not in output


@pytest.mark.parametrize("override", [False, True])
def test_stats_boundaries_and_retention_use_current_cards(tmp_path, capsys, override):
    (tmp_path / "notes.md").write_text(
        "".join(f"## Question {i}\nAnswer\n" for i in range(5)), encoding="utf-8",
    )
    cards = load_deck(tmp_path)
    today = date.today()
    path = tmp_path / "history.sqlite"
    (tmp_path / ".drill.toml").write_text(
        f'db = "{"unused.sqlite" if override else "history.sqlite"}"\n', encoding="utf-8",
    )
    with Store(path) as store:
        for card, interval, offset, grade in zip(
            cards, [1, 2, 20, 21], [-1, 0, 1, 0], Grade,
        ):
            due = today + timedelta(days=offset)
            reviewed = due - timedelta(days=interval)
            prev = apply_grade(None, card.id, card.deck, Grade.EASY, reviewed - timedelta(days=1))
            store.record(None, prev, Grade.EASY)
            state = replace(prev, due=due, last_review=reviewed, stability=0.1, reps=2)
            store.record(prev, state, grade)
        # Same-day grades and history for removed cards must not skew retention.
        store.record(state, state, Grade.AGAIN)
        orphan = apply_grade(None, "removed", "notes", Grade.GOOD, today - timedelta(days=2))
        store.record(None, orphan, Grade.GOOD)
        store.record(orphan, replace(orphan, last_review=today), Grade.AGAIN)
    original = path.read_bytes()
    args = ["stats", str(tmp_path)] + (["--db", str(path)] if override else [])
    assert main(args) == 0
    assert capsys.readouterr().out.split("\n\n")[0] + "\n" == (
        "Cards: 5\n  new: 1\n  learning: 1\n  young: 2\n  mature: 1\n"
        "Due today (including overdue): 3\nRetention: 75.0% (3/4)\n"
    )
    assert path.read_bytes() == original
    assert not (tmp_path / ".drill.db").exists()
    assert not (tmp_path / "unused.sqlite").exists()


@pytest.mark.parametrize("command", ["stats", "forecast", "cards"])
@pytest.mark.parametrize("file_exists", [False, True])
def test_stats_requires_directory(tmp_path, capsys, file_exists, command):
    path = tmp_path / "notes.md"
    if file_exists:
        path.write_text("## Q\nA\n", encoding="utf-8")
    assert main([command, str(path)]) == 2
    assert capsys.readouterr().err == f"drill: not a directory: {path}\n"


@pytest.mark.parametrize("command", ["stats", "forecast", "cards"])
def test_stats_reports_invalid_config(tmp_path, capsys, command):
    config = tmp_path / ".drill.toml"
    config.write_text("db = 42\n", encoding="utf-8")
    assert main([command, str(tmp_path)]) == 2
    assert str(config) in capsys.readouterr().err
    assert not (tmp_path / ".drill.db").exists()


def test_review_counts_include_all_grades_and_respect_window_and_undo(tmp_path):
    today = date(2026, 1, 1)
    start = date(2025, 10, 13)
    with Store(tmp_path / "history.sqlite") as store:
        for card_id, day, amount in (
            ("one", start - timedelta(days=1), 1),
            ("one", start, 1),
            ("one", today, 5),
            ("two", today, 5),
            ("removed", today, 3),
            ("one", today + timedelta(days=1), 1),
        ):
            # Store UTC timestamps, with reviews just after local midnight.
            when = datetime.combine(day, time(0, 5)).astimezone(timezone.utc)
            for index in range(amount):
                prev = store.get(card_id)
                grade = Grade(index % 4 + 1)
                state = apply_grade(prev, card_id, "notes", grade, day)
                store.record(prev, state, grade, when=when)
        prev = store.get("two")
        state = apply_grade(prev, "two", "notes", Grade.AGAIN, today)
        store.record(prev, state, Grade.AGAIN, when=datetime.combine(today, time(12)).astimezone())
        assert store.undo()
        assert store.review_counts({"one", "two"}, start, today) == {start: 1, today: 10}
        assert store.review_counts(set(), start, today) == {}


@pytest.mark.parametrize("today", [date(2026, 1, 5), date(2026, 1, 4), date(2024, 2, 29)])
def test_heatmap_week_alignment_and_future_blanks(today):
    start = today - timedelta(days=today.weekday() + 77)
    terminal = Terminal(lambda text: None, lambda: "")
    counts = {start: 1, start - timedelta(days=1): 10, today: 10,
              today + timedelta(days=1): 10}
    lines = review_heatmap(counts, today, terminal).splitlines()
    assert lines[1] == f"{start} to {today}"
    assert len(lines) == 10
    for weekday, row in enumerate(lines[2:9]):
        cells = row[4:].split()
        assert len(cells) == (12 if weekday <= today.weekday() else 11)
        for week, cell in enumerate(cells):
            day = start + timedelta(days=week * 7 + weekday)
            assert cell == ("█" if day == today else "░" if day == start else ".")


def test_heatmap_shading_and_colour_have_the_same_meaning():
    today = date(2026, 1, 11)
    monday = today - timedelta(days=6)
    counts = {monday + timedelta(days=i): count for i, count in enumerate((0, 1, 2, 3, 5, 6, 10))}
    plain = review_heatmap(counts, today, Terminal(lambda text: None, lambda: ""))
    coloured = review_heatmap(counts, today, Terminal(lambda text: None, lambda: "", ansi=True))
    assert [row[-1] for row in plain.splitlines()[2:9]] == list(".░░▒▒▓█")
    assert plain.splitlines()[-1] == ". 0  ░ 1-2  ▒ 3-5  ▓ 6-9  █ 10+"
    assert "\x1b[38;5;109m▓\x1b[0m" in coloured
    assert re.sub(r"\x1b\[[0-9;]*m", "", coloured) == plain


def test_stats_heatmap_uses_logged_reviews(tmp_path, capsys):
    (tmp_path / "notes.md").write_text("## Q\nA\n", encoding="utf-8")
    card, = load_deck(tmp_path)
    today = date.today()
    with Store(tmp_path / ".drill.db") as store:
        state = apply_grade(None, card.id, card.deck, Grade.GOOD, today)
        when = datetime.combine(today, time(12)).astimezone()
        store.record(None, state, Grade.GOOD, when=when)
    assert main(["stats", str(tmp_path)]) == 0
    rows = capsys.readouterr().out.splitlines()[-8:-1]
    assert rows[today.weekday()].endswith("░")
    assert sum(row.count("░") for row in rows) == 1
