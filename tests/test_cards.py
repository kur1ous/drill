from dataclasses import replace
from datetime import date, timedelta
import re

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.store import Store


@pytest.mark.parametrize("due_only", [False, True])
def test_cards_empty_deck(tmp_path, capsys, due_only):
    args = ["cards", str(tmp_path)] + (["--due"] if due_only else [])
    assert main(args) == 0
    assert capsys.readouterr().out == (
        "No due cards.\n" if due_only else "No cards found.\n"
    )


def test_cards_new_cards_and_due_filter(tmp_path, capsys):
    (tmp_path / "notes.md").write_text("## Unreviewed question\nAnswer\n", encoding="utf-8")
    assert main(["cards", str(tmp_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert re.split(r" {2,}", lines[1]) == ["notes", "new", "-", "0", "Unreviewed question"]
    assert main(["cards", str(tmp_path), "--due"]) == 0
    assert capsys.readouterr().out == "No due cards.\n"


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("due_only", [False, True])
def test_cards_schedules_alignment_and_current_content(tmp_path, capsys, monkeypatch, override, due_only):
    today = date(2026, 12, 31)
    class Clock:
        @staticmethod
        def today():
            return today
    monkeypatch.setattr("drill.cli.date", Clock)
    (tmp_path / "a.md").write_text(
        "## Overdue question\nAnswer\n## Due today\nAnswer\n", encoding="utf-8",
    )
    (tmp_path / "long deck name.md").write_text(
        "## Future question\nAnswer\n## New question\nAnswer\n", encoding="utf-8",
    )
    cards = load_deck(tmp_path)
    path = tmp_path / "history.sqlite"
    (tmp_path / ".drill.toml").write_text(
        f'db = "{"unused.sqlite" if override else "history.sqlite"}"\n', encoding="utf-8",
    )
    with Store(path) as store:
        for card, offset, stability, lapses in zip(cards, [-1, 0, 1], [1.25, 1234.5, 3.75], [0, 12, 2]):
            state = apply_grade(None, card.id, "old deck name", Grade.GOOD, today)
            state = replace(state, due=today + timedelta(days=offset), stability=stability, lapses=lapses)
            store.record(None, state, Grade.GOOD)
        orphan = apply_grade(None, "removed", "removed deck", Grade.GOOD, today)
        store.record(None, replace(orphan, due=today), Grade.GOOD)
    original = path.read_bytes()
    args = ["cards", str(tmp_path)]
    if override:
        args += ["--db", str(path)]
    if due_only:
        args += ["--due"]
    assert main(args) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    expected = [
        ["a", "2026-12-30", "1.25", "0", "Overdue question"],
        ["a", "2026-12-31", "1234.50", "12", "Due today"],
        ["long deck name", "2027-01-01", "3.75", "2", "Future question"],
        ["long deck name", "new", "-", "0", "New question"],
    ][:2 if due_only else 4]
    assert re.split(r" {2,}", lines[0]) == ["Deck", "Due", "Stability (days)", "Lapses", "Question"]
    assert [re.split(r" {2,}", row) for row in lines[1:]] == expected
    for line, values in zip(lines[1:], expected):
        assert line.index(values[1]) == lines[0].index("Due")
        assert line.index(values[2]) + len(values[2]) == lines[0].index("Stability (days)") + len("Stability (days)")
        assert line.rindex(values[3], 0, line.index(values[4])) + len(values[3]) == lines[0].index("Lapses") + len("Lapses")
        assert line.index(values[4]) == lines[0].index("Question")
    assert "\x1b" not in output
    assert path.read_bytes() == original
    assert not (tmp_path / ".drill.db").exists()
    assert not (tmp_path / "unused.sqlite").exists()
