from datetime import date, timedelta

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
    term = Fake(" 3 4")
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
    term = Fake(" 3")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Question 1" in term.text and "Answer 1" in term.text
    assert "1 again 1d   2 hard 1d   3 good 4d   4 easy 11d" in term.text


def test_review_displays_question_with_attached_hash(tmp_path):
    (tmp_path / "languages.md").write_text("## C#\nA language.\n", encoding="utf-8")
    term = Fake("q")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "    C#\n" in term.text


def test_quitting_keeps_earlier_grades_only(tmp_path):
    make_deck(tmp_path)
    term = Fake(" 3 q")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Stopped. 1 card reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert len(store.states()) == 1


def test_closed_stdin_stops_cleanly(tmp_path):
    make_deck(tmp_path)
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Stopped. 0 cards reviewed." in term.text


def test_other_keys_are_ignored(tmp_path):
    make_deck(tmp_path, 1)
    term = Fake("x 9 z 5 2")  # 5 and 9 are not grades; the space reveals, 2 grades
    run_review(tmp_path, None, 10, term, TODAY)
    with Store(tmp_path / ".drill.db") as store:
        (state,) = store.states().values()
    assert state.reps == 1
    assert (state.due - TODAY).days == 1  # hard on a new card


def test_second_session_same_day_has_nothing_due(tmp_path):
    make_deck(tmp_path)
    run_review(tmp_path, None, 10, Fake(" 3 3"), TODAY)
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Nothing due." in term.text
    assert "Next: 2 cards on 2026-03-05." in term.text


def test_new_card_limit_applies_per_day(tmp_path):
    make_deck(tmp_path, 3)
    run_review(tmp_path, None, 1, Fake(" 3"), TODAY)
    term = Fake("")
    run_review(tmp_path, None, 1, term, TODAY)
    assert "Nothing due." in term.text
    later = Fake(" 3")
    run_review(tmp_path, None, 1, later, TODAY + timedelta(days=1))
    assert "1 card reviewed" in later.text


def test_card_reviewed_again_when_due(tmp_path):
    make_deck(tmp_path, 1)
    run_review(tmp_path, None, 10, Fake(" 3"), TODAY)
    day = TODAY + timedelta(days=4)
    term = Fake(" 3")
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
    run_review(tmp_path, None, 10, Fake(" 3"), TODAY)
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
