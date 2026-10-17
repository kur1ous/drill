from datetime import date, timedelta
import re

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade
from drill.session import build_queue, render_answer, run_review
from drill.store import Store
from drill.term import EOF_KEY, Terminal

TODAY = date(2026, 3, 1)


class Fake(Terminal):
    def __init__(self, keys: str, width: int = 80):
        self.out: list[str] = []
        self._keys = iter(keys)
        super().__init__(self.out.append, self._next_key, ansi=False, width=width)

    def _next_key(self) -> str:
        return next(self._keys, EOF_KEY)

    @property
    def text(self) -> str:
        return "".join(self.out)


def make_deck(tmp_path, n=2):
    body = "".join(f"## Question {i}\nAnswer {i}\n\n" for i in range(1, n + 1))
    (tmp_path / "deck.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_queue_puts_due_first_then_limited_new(tmp_path):
    make_deck(tmp_path, 4)
    cards = load_deck(tmp_path)
    older = apply_grade(None, cards[2].id, "deck", Grade.GOOD, TODAY - timedelta(days=9))
    old = apply_grade(None, cards[1].id, "deck", Grade.GOOD, TODAY - timedelta(days=8))
    later = apply_grade(None, cards[3].id, "deck", Grade.GOOD, TODAY)  # due in 4 days
    states = {s.id: s for s in (older, old, later)}
    queue = build_queue(cards, states, TODAY, new_limit=5)
    assert [c.question for c in queue] == ["Question 3", "Question 2", "Question 1"]
    assert build_queue(cards, {}, TODAY, new_limit=2, new_today=1) == cards[:1]
    assert build_queue(cards, {}, TODAY, new_limit=2, new_today=5) == []


def test_review_records_each_grade(tmp_path):
    make_deck(tmp_path)
    term = Fake("b 3 4")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Done. 2 cards reviewed." in term.text
    assert "1 good, 1 easy" in term.text
    with Store(tmp_path / ".drill.db") as store:
        states = store.states()
        assert len(states) == 2
        assert sorted((s.due - TODAY).days for s in states.values()) == [4, 11]
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_answer_screen_offers_previews_and_keys(tmp_path):
    make_deck(tmp_path, 1)
    term = Fake("b 3")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Question 1" in term.text and "Answer 1" in term.text
    assert "1 again 1d   2 hard 1d   3 good 4d   4 easy 11d" in term.text


def test_review_displays_question_with_attached_hash(tmp_path):
    (tmp_path / "languages.md").write_text("## C#\nA language.\n", encoding="utf-8")
    term = Fake("bq")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "    C#\n" in term.text


def test_quitting_keeps_earlier_grades_only(tmp_path):
    make_deck(tmp_path)
    term = Fake("b 3 q")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Stopped. 1 card reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert len(store.states()) == 1


def test_undo_returns_to_previous_card_and_replaces_grade(tmp_path):
    make_deck(tmp_path, 3)
    cards = load_deck(tmp_path)
    term = Fake("bu 3 1Uu 4 2")
    run_review(tmp_path, None, 10, term, TODAY, limit=3)
    assert "u to undo the last grade" in term.text
    assert "Done. 3 cards reviewed." in term.text
    assert "1 hard, 1 good, 1 easy" in term.text
    assert term.text.count("Question 2") == 4
    with Store(tmp_path / ".drill.db") as store:
        rows = store.db.execute("SELECT card_id, grade FROM reviews ORDER BY id")
        assert [tuple(r) for r in rows] == [
            (cards[0].id, 3), (cards[1].id, 4), (cards[2].id, 2),
        ]
        assert store.get(cards[1].id) == apply_grade(
            None, cards[1].id, cards[1].deck, Grade.EASY, TODAY
        )


@pytest.mark.parametrize("existing", [False, True])
def test_quitting_after_undo_leaves_previous_state(tmp_path, existing):
    make_deck(tmp_path)
    card = load_deck(tmp_path)[0]
    path = tmp_path / ".drill.db"
    previous = None
    if existing:
        previous = apply_grade(None, card.id, card.deck, Grade.GOOD, TODAY - timedelta(days=9))
        with Store(path) as store:
            store.record(None, previous, Grade.GOOD)
    term = Fake("b 1uq")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Stopped. 0 cards reviewed." in term.text
    assert "0 again" not in term.text
    with Store(path) as store:
        assert store.get(card.id) == previous
        assert store.new_introduced_on(TODAY) == 0
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == int(existing)


def test_closed_stdin_stops_cleanly(tmp_path):
    make_deck(tmp_path)
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Stopped. 0 cards reviewed." in term.text


@pytest.mark.parametrize("key", ["q", "\x03", ""])
def test_quitting_start_screen_leaves_history_untouched(tmp_path, key):
    make_deck(tmp_path)
    term = Fake(key)
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "0 due, 2 new" in term.text
    assert "Press any key to begin" in term.text
    assert "Question 1" not in term.text
    assert "Stopped. 0 cards reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert store.states() == {}
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_interrupt_at_start_stops_cleanly(tmp_path):
    make_deck(tmp_path)
    term = Fake("")

    def interrupt():
        raise KeyboardInterrupt

    term.read_key = interrupt
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Stopped. 0 cards reviewed." in term.text


@pytest.mark.parametrize("limit, due_count, new_count", [
    (1, 1, 0), (2, 2, 0), (3, 2, 1), (20, 2, 2), (None, 2, 2),
])
def test_session_limit_keeps_due_first_and_counts_selected_cards(
    tmp_path, limit, due_count, new_count
):
    make_deck(tmp_path, 5)
    cards = load_deck(tmp_path)
    with Store(tmp_path / ".drill.db") as store:
        for card, day in [(cards[2], TODAY - timedelta(days=9)),
                          (cards[1], TODAY - timedelta(days=8)),
                          (cards[4], TODAY)]:
            state = apply_grade(None, card.id, card.deck, Grade.GOOD, day)
            store.record(None, state, Grade.GOOD)
    count = due_count + new_count
    term = Fake("b" + " 3" * 5)
    assert run_review(tmp_path, None, 10, term, TODAY, limit=limit) == 0
    assert f"{due_count} due, {new_count} new" in term.text
    assert f"{count} of {count}" in term.text
    expected = [cards[2], cards[1], cards[0], cards[3]][:count]
    with Store(tmp_path / ".drill.db") as store:
        rows = store.db.execute("SELECT card_id FROM reviews ORDER BY rowid").fetchall()
        assert [row[0] for row in rows[3:]] == [card.id for card in expected]
    if limit == 1:
        assert "Question 2" not in term.text


def test_session_limit_does_not_consume_unreviewed_new_allowance(tmp_path):
    make_deck(tmp_path, 5)
    run_review(tmp_path, None, 3, Fake("b 3"), TODAY, limit=1)
    term = Fake("b 3 3")
    run_review(tmp_path, None, 3, term, TODAY)
    assert "0 due, 2 new" in term.text
    assert "Done. 2 cards reviewed." in term.text
    term = Fake("")
    run_review(tmp_path, None, 3, term, TODAY)
    assert "Nothing due." in term.text
    assert "Press any key" not in term.text


def test_other_keys_are_ignored(tmp_path):
    make_deck(tmp_path, 1)
    term = Fake("bx 9 z 5 2")  # 5 and 9 are not grades; the space reveals, 2 grades
    run_review(tmp_path, None, 10, term, TODAY)
    with Store(tmp_path / ".drill.db") as store:
        (state,) = store.states().values()
    assert state.reps == 1
    assert (state.due - TODAY).days == 1  # hard on a new card


def test_second_session_same_day_has_nothing_due(tmp_path):
    make_deck(tmp_path)
    run_review(tmp_path, None, 10, Fake("b 3 3"), TODAY)
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Nothing due." in term.text
    assert "Next: 2 cards on 2026-03-05." in term.text


def test_new_card_limit_applies_per_day(tmp_path):
    make_deck(tmp_path, 3)
    run_review(tmp_path, None, 1, Fake("b 3"), TODAY)
    term = Fake("")
    run_review(tmp_path, None, 1, term, TODAY)
    assert "Nothing due." in term.text
    later = Fake("b 3")
    run_review(tmp_path, None, 1, later, TODAY + timedelta(days=1))
    assert "1 card reviewed" in later.text


def test_card_reviewed_again_when_due(tmp_path):
    make_deck(tmp_path, 1)
    run_review(tmp_path, None, 10, Fake("b 3"), TODAY)
    day = TODAY + timedelta(days=4)
    term = Fake("b 3")
    run_review(tmp_path, None, 10, term, day)
    assert "3 good 15d" in term.text  # preview at the moment of the second review
    with Store(tmp_path / ".drill.db") as store:
        (state,) = store.states().values()
    assert state.reps == 2 and (state.due - day).days == 15


def test_empty_folder_is_reported(tmp_path):
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 1
    assert "No cards found" in term.text


def test_review_state_follows_a_reworded_question(tmp_path):
    make_deck(tmp_path, 1)
    run_review(tmp_path, None, 10, Fake("b 3"), TODAY)
    (tmp_path / "deck.md").write_text("## question 1?\nA better, longer answer.\n", encoding="utf-8")
    term = Fake("")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Nothing due." in term.text  # still remembered, not treated as a new card


def test_render_answer_wraps_prose_and_keeps_code():
    term = Fake("", width=50)
    answer = "one two three four five six seven eight nine ten eleven twelve\nthirteen.\n\n- a list item that is long enough to need wrapping across lines\n\n```py\nx  =  1\n```"
    lines = render_answer(term, answer)
    assert all(len(line) <= 50 for line in lines)
    assert "    x  =  1" in lines
    assert "    thirteen." not in lines  # joined into the paragraph above
    item = [i for i, l in enumerate(lines) if "- a list item" in l][0]
    assert lines[item + 1].startswith("      ")  # continuation hangs under the text


def test_cli_rejects_missing_directory(tmp_path, capsys):
    assert main(["review", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("width", [30, 40, 50, 120])
@pytest.mark.parametrize("ansi", [False, True])
def test_review_fits_terminal_width(tmp_path, width, ansi):
    name = "a-long-deck-name-" * 5
    answer = "A long answer with enough words to wrap across several lines.\n\n"
    answer += "- A list item that also needs wrapping in a narrow terminal.\n\n"
    answer += " " * 35 + "- A deeply nested list item that must still fit.\n\n"
    answer += "```python\n    result  =  " + "x" * 90 + "\n```\n"
    (tmp_path / f"{name}.md").write_text(
        "".join(f"## Question {i}: " + "long " * 20 + "\n" + answer for i in range(4)),
        encoding="utf-8",
    )
    term = Fake("b 1 2 3 4", width=width)
    term.ansi = ansi
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", term.text)
    assert all(len(line) <= width for line in text.splitlines())
    for label in ("1 again 1d", "2 hard 1d", "3 good 4d", "4 easy 11d"):
        assert label in text
    assert "Done. 4 cards reviewed." in text
    assert "1 again, 1 hard, 1 good, 1 easy" in " ".join(text.split())
    assert "x" * 90 in "".join(text.split())
    if width == 120:
        assert "1 again 1d   2 hard 1d   3 good 4d   4 easy 11d" in text


@pytest.mark.parametrize("widths", [(30, 120, 30, 120), (120, 30, 120, 30)])
def test_review_refreshes_width_for_start_front_back_and_summary(tmp_path, widths):
    make_deck(tmp_path, 1)
    term = Fake("b 3")
    sizes = iter(widths)
    term.get_width = lambda: next(sizes)
    screens = []
    term.clear = lambda: screens.append((term.width, []))
    term.write = lambda text: screens[-1][1].append(text)
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert [width for width, _ in screens] == list(widths)
    for width, chunks in screens:
        assert all(len(line) <= width for line in "".join(chunks).splitlines())
    assert "Answer 1" not in "".join(screens[0][1])
    assert "Answer 1" not in "".join(screens[1][1])
    assert "Answer 1" in "".join(screens[2][1])
    assert "Done. 1 card reviewed." in "".join(screens[3][1])


@pytest.mark.parametrize("empty", [True, False])
def test_empty_screens_refresh_width(tmp_path, empty):
    if not empty:
        make_deck(tmp_path, 1)
        run_review(tmp_path, None, 10, Fake("b 3"), TODAY)
    term = Fake("")
    term.get_width = lambda: 30
    assert run_review(tmp_path, None, 10, term, TODAY) == int(empty)
    assert term.width == 30
    assert all(len(line) <= 30 for line in term.text.splitlines())
    if not empty:
        assert "Next: 1 card on 2026-03-05." in term.text
