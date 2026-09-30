from datetime import date

import pytest

from drill import fsrs
from drill.fsrs import Grade, Memory
from drill.schedule import apply_grade, preview_intervals


def test_retrievability_is_ninety_percent_at_one_stability():
    for s in (0.5, 1, 4.1386, 30, 400):
        assert fsrs.retrievability(s, s) == pytest.approx(0.9, abs=1e-12)


def test_retrievability_starts_at_one_and_falls():
    assert fsrs.retrievability(0, 5) == 1.0
    assert fsrs.retrievability(10, 5) < fsrs.retrievability(5, 5)
    # (1 + 19/81 * 4/4.1386) ** -0.5, worked by hand
    assert fsrs.retrievability(4, 4.1386) == pytest.approx(0.9029, abs=1e-4)


def test_interval_at_default_retention_is_stability():
    assert fsrs.interval(4.1386) == 4
    assert fsrs.interval(10.9355) == 11
    assert fsrs.interval(0.2) == 1  # never less than a day
    assert fsrs.interval(1e9) == fsrs.MAX_INTERVAL


def test_lower_retention_means_longer_interval():
    assert fsrs.interval(10, 0.8) > fsrs.interval(10, 0.9) > fsrs.interval(10, 0.95)
    # 10 / (19/81) * (0.8 ** -2 - 1) = 10 * 81/19 * 0.5625
    assert fsrs.interval(10, 0.8) == 24


def test_first_review_uses_w0_to_w3():
    assert [fsrs.initial(g).stability for g in Grade] == [0.5701, 1.4436, 4.1386, 10.9355]


def test_initial_difficulty():
    assert fsrs.initial(Grade.GOOD).difficulty == pytest.approx(5.1443)
    assert fsrs.initial(Grade.AGAIN).difficulty == pytest.approx(5.1443 + 2 * 1.2006)
    assert fsrs.initial(Grade.EASY).difficulty == pytest.approx(5.1443 - 1.2006)


def test_difficulty_stays_within_bounds():
    d = 5.0
    for _ in range(60):
        d = fsrs.next_difficulty(d, Grade.AGAIN)
    assert 1 <= d <= 10
    d = 5.0
    for _ in range(60):
        d = fsrs.next_difficulty(d, Grade.EASY)
    assert 1 <= d <= 10


def test_difficulty_after_lapse_worked_by_hand():
    # step: 5.1443 + 0.8627 * 2 = 6.8697
    # revert: 0.0362 * 5.1443 + 0.9638 * 6.8697 = 6.8072
    assert fsrs.next_difficulty(5.1443, Grade.AGAIN) == pytest.approx(6.8072, abs=1e-4)


def test_good_review_leaves_default_difficulty_alone():
    assert fsrs.next_difficulty(5.1443, Grade.GOOD) == pytest.approx(5.1443)


def test_stability_after_good_recall_worked_by_hand():
    # S=4.1386 D=5.1443 after 4 days: R=0.9029
    # growth = e^1.629 * 5.8557 * 4.1386^-0.1342 * (e^(0.0971*1.0166) - 1) = 2.5608
    # S' = 4.1386 * 3.5608 = 14.736
    m = fsrs.next_memory(Memory(4.1386, 5.1443), Grade.GOOD, 4)
    assert m.stability == pytest.approx(14.736, abs=5e-3)
    assert m.difficulty == pytest.approx(5.1443)


def test_stability_after_lapse_worked_by_hand():
    # S=10 D=6 at R=0.8 (t = 23.98 days):
    # 2.1174 * 6^-0.0839 * (11^0.3204 - 1) * e^(0.2 * 1.4676) = 2.826
    t = (0.8 ** -2 - 1) * 10 / fsrs.FACTOR
    m = fsrs.next_memory(Memory(10, 6), Grade.AGAIN, t)
    assert m.stability == pytest.approx(2.826, abs=2e-3)


def test_grade_ordering_for_stability():
    base = Memory(4.1386, 5.1443)
    s = {g: fsrs.next_memory(base, g, 4).stability for g in Grade}
    assert s[Grade.AGAIN] < s[Grade.HARD] < s[Grade.GOOD] < s[Grade.EASY]


def test_lapse_never_raises_stability():
    for s in (0.3, 2, 30, 300):
        for t in (0, 1, 50):
            assert fsrs.next_memory(Memory(s, 5), Grade.AGAIN, t).stability <= s


def test_waiting_longer_before_a_successful_recall_helps_more():
    base = Memory(10, 5)
    early = fsrs.next_memory(base, Grade.GOOD, 2).stability
    late = fsrs.next_memory(base, Grade.GOOD, 20).stability
    assert late > early > base.stability


def test_harder_cards_gain_less():
    easy = fsrs.next_memory(Memory(10, 2), Grade.GOOD, 10).stability
    hard = fsrs.next_memory(Memory(10, 9), Grade.GOOD, 10).stability
    assert easy > hard


TODAY = date(2026, 3, 1)


def test_first_good_review_schedules_four_days_out():
    s = apply_grade(None, "abc", "bio", Grade.GOOD, TODAY)
    assert (s.due - TODAY).days == 4
    assert s.reps == 1 and s.lapses == 0
    assert s.introduced == TODAY and s.last_review == TODAY


def test_second_review_uses_elapsed_calendar_days():
    first = apply_grade(None, "abc", "bio", Grade.GOOD, TODAY)
    second = apply_grade(first, "abc", "bio", Grade.GOOD, date(2026, 3, 5))
    assert second.stability == pytest.approx(14.736, abs=5e-3)
    assert (second.due - date(2026, 3, 5)).days == 15
    assert second.reps == 2
    assert second.introduced == TODAY


def test_reviewing_early_on_the_same_day_does_not_go_negative():
    first = apply_grade(None, "abc", "bio", Grade.GOOD, TODAY)
    again = apply_grade(first, "abc", "bio", Grade.GOOD, TODAY)
    assert again.stability >= first.stability


def test_again_counts_a_lapse_only_after_the_first_review():
    first = apply_grade(None, "abc", "bio", Grade.AGAIN, TODAY)
    assert first.lapses == 0
    second = apply_grade(first, "abc", "bio", Grade.AGAIN, date(2026, 3, 3))
    assert second.lapses == 1


def test_preview_intervals_for_new_card():
    assert preview_intervals(None, TODAY) == {
        Grade.AGAIN: 1, Grade.HARD: 1, Grade.GOOD: 4, Grade.EASY: 11,
    }
