from dataclasses import replace
from datetime import date, timedelta
import re

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.stats import forecast_bars
from drill.store import Store
from drill.term import Terminal


@pytest.mark.parametrize("questions", [[], ["One", "Two"]])
def test_forecast_without_reviews(tmp_path, capsys, questions):
    (tmp_path / "notes.md").write_text(
        "".join(f"## {question}\nAnswer\n" for question in questions), encoding="utf-8",
    )
    assert main(["forecast", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    lines = output.splitlines()
    assert lines[:2] == ["Overdue: 0", "Scheduled cards (14 days)"]
    assert len(lines[3:]) == 14
    assert all(row.split()[1:] == ["0"] for row in lines[3:])
    assert "\x1b" not in output


@pytest.mark.parametrize("override", [False, True])
def test_forecast_window_current_cards_and_config(tmp_path, capsys, monkeypatch, override):
    today = date(2026, 12, 25)
    class Clock:
        @staticmethod
        def today():
            return today
    monkeypatch.setattr("drill.cli.date", Clock)
    (tmp_path / "notes.md").write_text(
        "".join(f"## Question {i}\nAnswer\n" for i in range(8)), encoding="utf-8",
    )
    cards = load_deck(tmp_path)
    path = tmp_path / "history.sqlite"
    (tmp_path / ".drill.toml").write_text(
        f'db = "{"unused.sqlite" if override else "history.sqlite"}"\n', encoding="utf-8",
    )
    with Store(path) as store:
        for card, offset in zip(cards, [-30, -1, 0, 0, 1, 13, 14]):
            state = apply_grade(None, card.id, card.deck, Grade.GOOD, today)
            store.record(None, replace(state, due=today + timedelta(days=offset)), Grade.GOOD)
        for offset in (-1, 0):
            state = apply_grade(None, f"removed{offset}", "notes", Grade.GOOD, today)
            store.record(None, replace(state, due=today + timedelta(days=offset)), Grade.GOOD)
    original = path.read_bytes()
    args = ["forecast", str(tmp_path)] + (["--db", str(path)] if override else [])
    assert main(args) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Overdue: 2"
    assert lines[2] == "2026-12-25 to 2027-01-07"
    assert len(lines[3:]) == 14
    assert {row.split()[0]: int(row.split()[1]) for row in lines[3:]} == {
        (today + timedelta(days=i)).isoformat(): {0: 2, 1: 1, 13: 1}.get(i, 0)
        for i in range(14)
    }
    assert path.read_bytes() == original
    assert not (tmp_path / ".drill.db").exists()
    assert not (tmp_path / "unused.sqlite").exists()


@pytest.mark.parametrize("width", [15, 30, 120])
@pytest.mark.parametrize("ansi", [False, True])
def test_forecast_bars_scale_and_refresh_width(width, ansi):
    today = date(2026, 11, 21)
    counts = {today + timedelta(days=i): count for i, count in enumerate((0, 1, 5000, 10000))}
    terminal = Terminal(lambda text: None, lambda: "", ansi=ansi, get_width=lambda: width)
    output = forecast_bars(counts, terminal)
    lines = re.sub(r"\x1b\[[0-9;]*m", "", output).splitlines()
    available = max(0, min(40, width - 19))
    assert terminal.width == width
    assert [row.split()[1] for row in lines] == ["0", "1", "5000", "10000"]
    assert [row.count("█") for row in lines] == [
        0, 1 if available else 0, round(available / 2), available,
    ]
    assert all(len(row) <= max(17, width) for row in lines)
    assert ("\x1b[2m" in output) == (ansi and bool(available))
