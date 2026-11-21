from dataclasses import replace
from datetime import date, datetime, time, timedelta
import re

import pytest

from drill.cli import main
from drill.deck import load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade, preview_intervals
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


@pytest.mark.parametrize("keys", ["bs", "b s"])
@pytest.mark.parametrize("deck_rules", [None, {"deck": {"new_per_day": 1}}])
def test_suspend_new_card_persists_without_review_or_allowance(tmp_path, keys, deck_rules):
    make_deck(tmp_path)
    term = Fake(keys)
    run_review(tmp_path, None, 1, term, TODAY, deck_rules=deck_rules)
    assert "Done. 0 cards reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        state = store.get(load_deck(tmp_path)[0].id)
        assert state.suspended and state.reps == 0
        assert store.new_introduced_on(TODAY) == 0
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "Question 1"
    term = Fake("b 3")
    run_review(tmp_path, None, 1, term, TODAY, deck_rules=deck_rules)
    assert "0 due, 1 new" in term.text and "Question 1" not in term.text
    assert "Done. 1 card reviewed." in term.text


@pytest.mark.parametrize("keys", ["bs", "b s", "b 1s", "b 1 s"])
def test_suspend_due_or_practice_card_preserves_schedule(tmp_path, keys):
    make_deck(tmp_path, 1)
    card = load_deck(tmp_path)[0]
    old = apply_grade(None, card.id, card.deck, Grade.GOOD, TODAY - timedelta(days=20))
    with Store(tmp_path / ".drill.db") as store:
        store.record(None, old, Grade.GOOD)
    term = Fake(keys)
    run_review(tmp_path, None, 10, term, TODAY)
    expected = apply_grade(old, card.id, card.deck, Grade.AGAIN, TODAY) if "1" in keys else old
    with Store(tmp_path / ".drill.db") as store:
        assert store.get(card.id) == replace(expected, suspended=True)
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == (2 if "1" in keys else 1)
    term = Fake("")
    run_review(tmp_path, None, 10, term, TODAY + timedelta(days=30))
    assert "Nothing due." in term.text and "Next:" not in term.text
    # A suspended future schedule must not appear in the next-due hint either.
    term = Fake("")
    run_review(tmp_path, None, 10, term, TODAY - timedelta(days=30))
    assert "Next:" not in term.text


def test_suspension_ends_undo_and_leech_summary_counts_graded_cards(tmp_path):
    make_deck(tmp_path, 3)
    cards = load_deck(tmp_path)
    with Store(tmp_path / ".drill.db") as store:
        for card in cards:
            state = apply_grade(None, card.id, card.deck, Grade.GOOD, TODAY - timedelta(days=20))
            store.record(None, replace(state, lapses=8, due=TODAY), Grade.GOOD)
    term = Fake("b 3su 3")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Done. 2 cards reviewed." in term.text
    assert "2 leeches reviewed" in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert [store.get(c.id).reps for c in cards] == [2, 1, 2]
        assert store.get(cards[1].id).suspended


@pytest.mark.parametrize("keys, expected_rate, elapsed", [
    ("q", "n/a", "0m 0s"),
    ("b 1 3  ", "50.0%", "1m 5s"),
    ("b 1u 3 3", "0.0%", "1m 5s"),
    ("b 1uq", "n/a", "1m 5s"),
    ("b 1q", "100.0%", "1m 5s"),
])
def test_summary_time_and_again_rate(tmp_path, monkeypatch, keys, expected_rate, elapsed):
    make_deck(tmp_path)
    clock = iter([50.0, 115.9])
    monkeypatch.setattr("drill.session.monotonic", lambda: next(clock))
    term = Fake(keys)
    run_review(tmp_path, None, 10, term, TODAY)
    assert f"Time: {elapsed}" in term.text
    assert f"Again-rate: {expected_rate}" in term.text
    if keys == "q":
        assert next(clock) == 50.0


def test_interrupted_summary_keeps_time_and_grades(tmp_path, monkeypatch):
    make_deck(tmp_path)
    clock = iter([10.0, 70.0])
    monkeypatch.setattr("drill.session.monotonic", lambda: next(clock))
    term = Fake("b 1")

    def read_key():
        key = term._next_key()
        if key == EOF_KEY:
            raise KeyboardInterrupt
        return key

    term.read_key = read_key
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Stopped. 1 card reviewed." in term.text
    assert "Time: 1m 0s" in term.text
    assert "Again-rate: 100.0%" in term.text


def test_summary_streak_uses_configured_database_and_other_decks(tmp_path, monkeypatch):
    make_deck(tmp_path, 1)
    path = tmp_path / "shared.sqlite"
    with Store(path) as store:
        day = TODAY - timedelta(days=1)
        state = apply_grade(None, "removed", "other", Grade.GOOD, day)
        store.record(None, state, Grade.GOOD, datetime.combine(day, time(12)).astimezone())
    original_record = Store.record

    def record_today(self, prev, new, grade, **kwargs):
        original_record(self, prev, new, grade,
                        datetime.combine(TODAY, time(12)).astimezone(), **kwargs)

    monkeypatch.setattr(Store, "record", record_today)
    term = Fake("b 3")
    run_review(tmp_path, path, 10, term, TODAY)
    assert "Streak: 2 days" in term.text
    assert not (tmp_path / ".drill.db").exists()


def test_queue_puts_due_first_then_limited_new(tmp_path):
    make_deck(tmp_path, 4)
    cards = load_deck(tmp_path)
    older = apply_grade(None, cards[2].id, "deck", Grade.GOOD, TODAY - timedelta(days=12))
    old = apply_grade(None, cards[1].id, "deck", Grade.GOOD, TODAY - timedelta(days=8))
    later = apply_grade(None, cards[3].id, "deck", Grade.GOOD, TODAY)  # due in the future
    states = {s.id: s for s in (older, old, later)}
    queue = build_queue(cards, states, TODAY, new_limit=5)
    assert [c.question for c in queue] == ["Question 3", "Question 2", "Question 1"]
    assert build_queue(cards, {}, TODAY, new_limit=2, new_today=1) == cards[:1]
    assert build_queue(cards, {}, TODAY, new_limit=2, new_today=5) == []


@pytest.mark.parametrize("order, expected", [
    ("due", [3, 1, 0, 2]),
    ("random", [2, 0, 1, 3]),
    ("deck", [0, 1, 2, 3]),
])
def test_queue_orders_only_eligible_cards(tmp_path, monkeypatch, order, expected):
    for name in ("alpha", "beta"):
        (tmp_path / f"{name}.md").write_text(
            "".join(f"## {name} {i}\nAnswer\n" for i in range(3)), encoding="utf-8"
        )
    cards = load_deck(tmp_path)
    original = list(cards)
    states = {}
    for i, days in ((0, 0), (1, -1), (3, -2), (4, 1)):
        state = apply_grade(None, cards[i].id, cards[i].deck, Grade.GOOD, TODAY)
        states[state.id] = replace(state, due=TODAY + timedelta(days=days))

    shuffled = []

    def reverse(queue):
        shuffled.append(list(queue))
        queue.reverse()

    monkeypatch.setattr("drill.session.random.shuffle", reverse)
    queue = build_queue(cards, states, TODAY, new_limit=2, new_today=1, order=order)
    assert queue == [cards[i] for i in expected]
    assert cards == original
    assert shuffled == ([[cards[i] for i in (3, 1, 0, 2)]] if order == "random" else [])
    assert build_queue([], states, TODAY, new_limit=2, order=order) == []
    exhausted = build_queue(cards, states, TODAY, new_limit=1, new_today=2, order=order)
    assert {card.id for card in exhausted} == {cards[i].id for i in (0, 1, 3)}


def test_queue_rejects_unknown_order():
    with pytest.raises(ValueError, match="unknown review order"):
        build_queue([], {}, TODAY, new_limit=1, order="unknown")


@pytest.mark.parametrize("order", ["due", "deck", "random"])
def test_per_deck_allowances_survive_filtering_and_session_limits(tmp_path, order):
    for name in ("unit/a", "unit/b", "unit/c", "other", "extra"):
        path = tmp_path / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"## {name} {i}\nAnswer\n" for i in range(3)), encoding="utf-8")
    rules = {"unit/*": {"new_per_day": 2}, "unit/b": {"new_per_day": 0}}

    def run(only=None, limit=None, keys="b" + " 3" * 10, day=TODAY):
        term = Fake(keys)
        assert run_review(tmp_path, None, 1, term, day, only=only, limit=limit,
                          order=order, deck_rules=rules) == 0
        return term.text

    assert "Done. 1 card reviewed." in run("unit/a", limit=1)
    assert "0 due, 1 new" in run("unit/a")
    assert "Nothing due." in run("unit/a")
    assert "Nothing due." in run("unit/b")
    assert "0 due, 2 new" in run("unit/c")
    assert "Done. 1 card reviewed." in run("other")
    assert "Nothing due." in run("extra")
    assert "Nothing due." in run()
    with Store(tmp_path / ".drill.db") as store:
        states = list(store.states().values())
        assert sorted(s.deck for s in states) == ["other", "unit/a", "unit/a", "unit/c", "unit/c"]
        queue = build_queue(load_deck(tmp_path), store.states(), TODAY, 1,
                            deck_limits={"unit/a": 2, "unit/c": 2})
        assert len(queue) == 1
        card = next(c for c in load_deck(tmp_path) if c.deck == "unit/b")
        previous = apply_grade(None, card.id, card.deck, Grade.GOOD, TODAY - timedelta(days=9))
        store.record(None, replace(previous, due=TODAY), Grade.GOOD)
    assert "1 due, 0 new" in run("unit/b")
    assert "0 due, 1 new" in run("unit/a", day=TODAY + timedelta(days=1))


def test_deck_retention_survives_undo_between_decks(tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.md").write_text(f"## {name}\nAnswer\n", encoding="utf-8")
    term = Fake("b 1u 3 3")
    run_review(tmp_path, None, 10, term, TODAY, retention=0.95,
               deck_rules={"a": {"retention": 0.8}})
    with Store(tmp_path / ".drill.db") as store:
        for card in load_deck(tmp_path):
            expected = apply_grade(None, card.id, card.deck, Grade.GOOD, TODAY,
                                   retention=0.8 if card.deck == "a" else 0.95)
            assert store.get(card.id) == expected
            assert f"3 good {(expected.due - TODAY).days}d" in term.text
        assert store.new_introduced_on(TODAY) == 2
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_review_records_each_grade(tmp_path):
    make_deck(tmp_path)
    term = Fake("b 3 4")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Done. 2 cards reviewed." in term.text
    assert "1 good, 1 easy" in term.text
    with Store(tmp_path / ".drill.db") as store:
        states = store.states()
        assert len(states) == 2
        good_days, easy_days = sorted((s.due - TODAY).days for s in states.values())
        assert 3 <= good_days <= 5
        assert 10 <= easy_days <= 12
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_answer_screen_offers_previews_and_keys(tmp_path):
    make_deck(tmp_path, 1)
    term = Fake("b 3")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Question 1" in term.text and "Answer 1" in term.text
    card = load_deck(tmp_path)[0]
    intervals = preview_intervals(None, TODAY, card.id)
    for grade in Grade:
        assert f"{int(grade)} {grade.name.lower()} {intervals[grade]}d" in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert (store.get(card.id).due - TODAY).days == intervals[Grade.GOOD]


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


@pytest.mark.parametrize("existing", [False, True])
def test_again_cards_return_once_after_the_scheduled_queue(tmp_path, existing):
    make_deck(tmp_path, 3)
    cards = load_deck(tmp_path)
    previous = {}
    with Store(tmp_path / ".drill.db") as store:
        if existing:
            for card in cards:
                state = apply_grade(None, card.id, card.deck, Grade.GOOD,
                                    TODAY - timedelta(days=9))
                state = replace(state, due=TODAY)
                store.record(None, state, Grade.GOOD)
                previous[card.id] = state
    term = Fake("b 1 3 1    ")
    run_review(tmp_path, None, 10, term, TODAY, limit=3)
    questions = re.findall(r"Question \d", term.text)
    assert questions == [f"Question {n}" for n in (1, 1, 2, 2, 3, 3, 1, 1, 3, 3)]
    assert "Practice only - schedule unchanged" in term.text
    assert "Done. 3 cards reviewed." in term.text
    assert "2 again, 1 good" in term.text
    with Store(tmp_path / ".drill.db") as store:
        for card, grade in zip(cards, (Grade.AGAIN, Grade.GOOD, Grade.AGAIN)):
            assert store.get(card.id) == apply_grade(
                previous.get(card.id), card.id, card.deck, grade, TODAY
            )
        rows = store.db.execute("SELECT card_id, grade FROM reviews ORDER BY id").fetchall()
        assert len(rows) == (6 if existing else 3)
        assert [tuple(row) for row in rows[-3:]] == [
            (cards[0].id, 1), (cards[1].id, 3), (cards[2].id, 1),
        ]


@pytest.mark.parametrize("keys", ["q", " q", "", " "])
def test_stopping_during_practice_keeps_original_grade(tmp_path, keys):
    make_deck(tmp_path, 1)
    card = load_deck(tmp_path)[0]
    term = Fake("b 1" + keys)
    run_review(tmp_path, None, 10, term, TODAY, limit=1)
    assert "Stopped. 1 card reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert store.get(card.id) == apply_grade(None, card.id, card.deck, Grade.AGAIN, TODAY)
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


@pytest.mark.parametrize("replacement", ["1  ", "3"])
def test_undo_on_first_practice_card_replaces_last_scheduled_grade(tmp_path, replacement):
    make_deck(tmp_path, 1)
    term = Fake("b 1u " + replacement)
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Done. 1 card reviewed." in term.text
    assert term.text.count("Question 1") == (7 if replacement[0] == "1" else 5)
    with Store(tmp_path / ".drill.db") as store:
        rows = store.db.execute("SELECT grade FROM reviews").fetchall()
        assert [row[0] for row in rows] == [int(replacement[0])]


def test_undo_again_removes_only_its_own_pending_practice(tmp_path):
    make_deck(tmp_path, 3)
    term = Fake("b 1 1u 4 3  ")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Done. 3 cards reviewed." in term.text
    assert "1 again, 1 good, 1 easy" in term.text
    questions = re.findall(r"Question \d", term.text)
    assert questions[-2:] == ["Question 1", "Question 1"]
    assert questions.count("Question 2") == 4


def test_practice_does_not_offer_undo_after_a_practice_card(tmp_path):
    make_deck(tmp_path)
    term = Fake("b 1 1  u  ")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Done. 2 cards reviewed." in term.text
    last_question = term.text.rsplit("Question 2", 2)[1]
    assert "u to undo" not in last_question
    with Store(tmp_path / ".drill.db") as store:
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_closed_stdin_stops_cleanly(tmp_path):
    make_deck(tmp_path)
    term = Fake("")
    assert run_review(tmp_path, None, 10, term, TODAY) == 0
    assert "Stopped. 0 cards reviewed." in term.text


def seed_lapses(tmp_path, lapses):
    make_deck(tmp_path, len(lapses))
    cards = load_deck(tmp_path)
    with Store(tmp_path / ".drill.db") as store:
        for card, count in zip(cards, lapses):
            state = apply_grade(None, card.id, card.deck, Grade.GOOD,
                                TODAY - timedelta(days=9))
            store.record(None, replace(state, due=TODAY, lapses=count), Grade.GOOD)
    return cards


@pytest.mark.parametrize("lapses", [7, 8, 9])
@pytest.mark.parametrize("width", [30, 120])
def test_leech_note_starts_at_eight_lapses_on_answer_and_summary(tmp_path, lapses, width):
    seed_lapses(tmp_path, [lapses])
    term = Fake("b 3", width=width)
    screens = []
    term.clear = lambda: screens.append([])
    term.write = lambda text: screens[-1].append(text)
    run_review(tmp_path, None, 10, term, TODAY)
    start, front, back, summary = ["".join(chunks) for chunks in screens]
    assert "Leech" not in start + front
    assert (f"Leech: {lapses} lapses" in back) == (lapses >= 8)
    assert ("1 leech reviewed" in summary) == (lapses >= 8)
    assert all(len(line) <= width for chunks in screens for line in "".join(chunks).splitlines())


def test_eighth_lapse_shows_in_practice_and_counts_once_in_summary(tmp_path):
    cards = seed_lapses(tmp_path, [7, 8])
    term = Fake("b 1 3  ")
    run_review(tmp_path, None, 10, term, TODAY)
    assert term.text.count("Leech: 8 lapses") == 2
    assert "2 leeches reviewed (8+ lapses)" in term.text
    assert "Done. 2 cards reviewed." in term.text
    with Store(tmp_path / ".drill.db") as store:
        assert [store.get(c.id).lapses for c in cards] == [8, 8]
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 4


@pytest.mark.parametrize("ending", ["q", "u 3", "uq"])
def test_leech_summary_respects_undo_and_stopping_before_practice(tmp_path, ending):
    card = seed_lapses(tmp_path, [7])[0]
    term = Fake("b 1" + ending)
    run_review(tmp_path, None, 10, term, TODAY)
    assert ("1 leech reviewed" in term.text) == (ending == "q")
    with Store(tmp_path / ".drill.db") as store:
        assert store.get(card.id).lapses == (8 if ending == "q" else 7)


@pytest.mark.parametrize("keys, limit", [("q", None), ("b q", None), ("b 3q", None), ("b 3", 1)])
def test_summary_excludes_ungraded_leeches(tmp_path, keys, limit):
    seed_lapses(tmp_path, [0, 8])
    term = Fake(keys)
    run_review(tmp_path, None, 10, term, TODAY, limit=limit)
    assert "leech" not in term.text.lower()


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
        for card, day in [(cards[2], TODAY - timedelta(days=12)),
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
    assert "Next: 1 card on 2026-03-04." in term.text


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
    with Store(tmp_path / ".drill.db") as store:
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "Question 1"
    (tmp_path / "deck.md").write_text("## question 1?\nA better, longer answer.\n", encoding="utf-8")
    term = Fake("")
    run_review(tmp_path, None, 10, term, TODAY)
    assert "Nothing due." in term.text  # still remembered, not treated as a new card
    run_review(tmp_path, None, 10, Fake("b 3"), TODAY + timedelta(days=30))
    with Store(tmp_path / ".drill.db") as store:
        assert store.db.execute("SELECT question FROM cards").fetchone()[0] == "question 1?"
        assert next(iter(store.states().values())).reps == 2


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
    term = Fake("b 1 2 3 4  ", width=width)
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
