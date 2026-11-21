from dataclasses import replace
from datetime import date, timedelta

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.store import Store


@pytest.mark.parametrize("questions", [[], ["One", "Two"]])
def test_stats_without_reviews(tmp_path, capsys, questions):
    (tmp_path / "notes.md").write_text(
        "".join(f"## {question}\nAnswer\n" for question in questions), encoding="utf-8",
    )
    assert main(["stats", str(tmp_path)]) == 0
    assert capsys.readouterr().out == (
        f"Cards: {len(questions)}\n  new: {len(questions)}\n"
        "  learning: 0\n  young: 0\n  mature: 0\n"
        "Due today (including overdue): 0\nRetention: n/a (0 reviews)\n"
    )


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
    assert capsys.readouterr().out == (
        "Cards: 5\n  new: 1\n  learning: 1\n  young: 2\n  mature: 1\n"
        "Due today (including overdue): 3\nRetention: 75.0% (3/4)\n"
    )
    assert path.read_bytes() == original
    assert not (tmp_path / ".drill.db").exists()
    assert not (tmp_path / "unused.sqlite").exists()


@pytest.mark.parametrize("file_exists", [False, True])
def test_stats_requires_directory(tmp_path, capsys, file_exists):
    path = tmp_path / "notes.md"
    if file_exists:
        path.write_text("## Q\nA\n", encoding="utf-8")
    assert main(["stats", str(path)]) == 2
    assert capsys.readouterr().err == f"drill: not a directory: {path}\n"


def test_stats_reports_invalid_config(tmp_path, capsys):
    config = tmp_path / ".drill.toml"
    config.write_text("db = 42\n", encoding="utf-8")
    assert main(["stats", str(tmp_path)]) == 2
    assert str(config) in capsys.readouterr().err
    assert not (tmp_path / ".drill.db").exists()
