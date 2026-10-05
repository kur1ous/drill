import pytest

from drill.deck import load_deck, normalize, parse_markdown

SAMPLE = """\
# Cell biology

Some intro that is not a card.

## What does the mitochondrion do?

Makes most of the cell's ATP.

Also has its own DNA.

## Why is the membrane *fluid*?
Phospholipids move sideways.

### A subheading stays in the answer

More detail.
"""


def test_parses_questions_and_answers():
    cards = parse_markdown(SAMPLE, "bio")
    assert [c.question for c in cards] == [
        "What does the mitochondrion do?",
        "Why is the membrane *fluid*?",
    ]
    assert cards[0].answer == "Makes most of the cell's ATP.\n\nAlso has its own DNA."
    assert "### A subheading stays in the answer" in cards[1].answer
    assert cards[0].line == 5
    assert cards[0].deck == "bio"


def test_heading_without_body_is_not_a_card():
    cards = parse_markdown("## Section\n\n## Real\nanswer\n")
    assert [c.question for c in cards] == ["Real"]


def test_hash_inside_code_fence_is_not_a_heading():
    text = "## How to comment?\n```python\n## not a card\nx = 1\n```\ndone\n"
    cards = parse_markdown(text)
    assert len(cards) == 1
    assert "## not a card" in cards[0].answer
    assert cards[0].answer.endswith("done")


def test_longer_fence_needs_matching_close():
    text = "## Q\n````\n```\n## still inside\n````\nafter\n"
    cards = parse_markdown(text)
    assert len(cards) == 1 and "## still inside" in cards[0].answer


def test_top_level_heading_ends_a_card():
    cards = parse_markdown("## A\nans\n# Next chapter\nnot part of A\n")
    assert cards[0].answer == "ans"


def test_crlf_and_bom():
    text = "﻿## Q one\r\nfirst\r\n\r\n## Q two\r\nsecond\r\n"
    cards = parse_markdown(text)
    assert [(c.question, c.answer) for c in cards] == [("Q one", "first"), ("Q two", "second")]


def test_closing_hashes_stripped_and_answer_indent_kept():
    cards = parse_markdown("## Q ##\n    code block\n")
    assert cards[0].question == "Q"
    assert cards[0].answer == "    code block"


@pytest.mark.parametrize("heading, question", [
    ("## C#", "C#"),
    ("## F# \t", "F#"),
    ("## C# ##", "C#"),
    ("## C#\t### \t", "C#"),
    ("## Literal ###hash###", "Literal ###hash###"),
])
def test_attached_hashes_stay_in_question(heading, question):
    card = parse_markdown(f"{heading}\nAnswer\n")[0]
    assert card.question == question


def test_attached_hash_changes_card_identity():
    plain = parse_markdown("## C\nA language.\n")[0]
    sharp = parse_markdown("## C#\nAnother language.\n")[0]
    decorated = parse_markdown("## C# ##\nAnother language.\n")[0]
    assert sharp.id != plain.id
    assert sharp.id == decorated.id


def test_normalize_ignores_cosmetic_edits():
    base = normalize("What does the Mitochondrion do?")
    assert normalize("what does the  *mitochondrion*   do") == base
    assert normalize("What does the `mitochondrion` do??") == base


def test_normalize_keeps_math_distinct():
    assert normalize("What is $a^2$?") != normalize("What is $a_2$?")
    assert normalize(r"$\frac{a}{b}$") != normalize(r"$\frac{b}{a}$")


def test_id_survives_answer_edit_and_light_question_edit():
    a = parse_markdown("## Define entropy?\nDisorder.\n")[0]
    b = parse_markdown("## define *entropy*\nA measure of the number of microstates.\n")[0]
    assert a.id == b.id


def test_id_changes_when_question_meaning_changes():
    a = parse_markdown("## Define entropy?\nx\n")[0]
    b = parse_markdown("## Define enthalpy?\nx\n")[0]
    assert a.id != b.id


def test_load_deck_walks_folder_in_order(tmp_path):
    (tmp_path / "b.md").write_text("## Q b\nans b\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("## Q a\nans a\n", encoding="utf-8")
    sub = tmp_path / "unit1"
    sub.mkdir()
    (sub / "c.md").write_text("## Q c\nans c\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("## ignored\nx\n", encoding="utf-8")
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "x.md").write_text("## hidden\nx\n", encoding="utf-8")
    cards = load_deck(tmp_path)
    assert [(c.deck, c.question) for c in cards] == [
        ("a", "Q a"), ("b", "Q b"), ("unit1/c", "Q c"),
    ]


def test_duplicate_questions_get_distinct_stable_ids(tmp_path):
    (tmp_path / "a.md").write_text("## Same?\none\n\n## Same?\ntwo\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("## Same?\nthree\n", encoding="utf-8")
    ids = [c.id for c in load_deck(tmp_path)]
    assert len(set(ids)) == 3
    assert ids == [c.id for c in load_deck(tmp_path)]


def test_moving_a_card_between_files_keeps_its_id(tmp_path):
    (tmp_path / "old.md").write_text("## Where am I?\nhere\n", encoding="utf-8")
    before = load_deck(tmp_path)[0].id
    (tmp_path / "old.md").rename(tmp_path / "new.md")
    assert load_deck(tmp_path)[0].id == before
