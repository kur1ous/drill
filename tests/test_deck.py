import pytest

from drill.deck import check_deck, load_deck, normalize, parse_markdown

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


@pytest.mark.parametrize("marker", ["```", "~~~"])
@pytest.mark.parametrize("suffix", ["literal", " text", "\ttext"])
def test_fence_with_trailing_content_does_not_close(marker, suffix):
    answer = f"{marker}text\n{marker}{suffix}\n## still code\nexample\n{marker}"
    cards = parse_markdown(f"## Show a fence\n{answer}\n\n## Real card\nreal answer\n")
    assert [(c.question, c.answer) for c in cards] == [
        ("Show a fence", answer),
        ("Real card", "real answer"),
    ]


@pytest.mark.parametrize("marker", ["```", "~~~"])
@pytest.mark.parametrize("ending", ["", " \t", "  "])
def test_fence_closes_with_only_trailing_whitespace(marker, ending):
    answer = f"{marker}text\n## still code\nexample\n  {marker}{marker[0]}{ending}"
    cards = parse_markdown(f"## Show a fence\n{answer}\n\n## Real card\nreal answer\n")
    assert [c.question for c in cards] == ["Show a fence", "Real card"]
    assert cards[0].answer == answer.rstrip()


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


def test_check_reports_empty_files_and_heading_locations(tmp_path):
    (tmp_path / "empty.md").write_text("# Notes\nIntro only.\n", encoding="utf-8")
    (tmp_path / "headings.md").write_text(
        "## First\n \t\n## Second\n# Chapter\n## Last\n", encoding="utf-8"
    )
    assert check_deck(tmp_path) == [
        "empty.md:1: no cards",
        "headings.md:1: no cards",
        "headings.md:1: empty body for 'First'",
        "headings.md:3: empty body for 'Second'",
        "headings.md:5: empty body for 'Last'",
    ]


def test_check_reports_exact_duplicates_across_and_within_files(tmp_path):
    (tmp_path / "a.md").write_text(
        "## Same?\none\n## Same? ##\ntwo\n## same?\nthree\n"
        "## *Same*?\nfour\n## Same\nfive\n", encoding="utf-8"
    )
    nested = tmp_path / "unit"
    nested.mkdir()
    (nested / "b.md").write_text("## Same?\n", encoding="utf-8")
    assert check_deck(tmp_path) == [
        "a.md:3: duplicate question 'Same?' (first at a.md:1)",
        "unit/b.md:1: no cards",
        "unit/b.md:1: empty body for 'Same?'",
        "unit/b.md:1: duplicate question 'Same?' (first at a.md:1)",
    ]


@pytest.mark.parametrize("marker", ["```", "~~~"])
def test_check_uses_parser_fences_and_ignores_hidden_paths(tmp_path, marker):
    (tmp_path / "a.md").write_text(
        f"\ufeff## Q\r\n{marker}\r\n## Q\r\n{marker}\r\n", encoding="utf-8"
    )
    (tmp_path / ".hidden.md").write_bytes(b"\xff")
    (tmp_path / "notes.txt").write_text("No cards", encoding="utf-8")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "b.md").write_bytes(b"\xff")
    assert check_deck(tmp_path) == []


@pytest.mark.parametrize("ending", [" <=>", " <=> ###", " <=> \t"])
def test_reversed_card_swaps_sides_and_keeps_source_location(ending):
    cards = parse_markdown(f"# Notes\n## Term{ending}\nDefinition\n\nMore detail.\n", "bio")
    assert [(c.question, c.answer) for c in cards] == [
        ("Term", "Definition\n\nMore detail."),
        ("Definition\n\nMore detail.", "Term"),
    ]
    assert [(c.deck, c.line) for c in cards] == [("bio", 2), ("bio", 2)]
    assert cards[0].id != cards[1].id


def test_reverse_marker_must_be_a_separate_heading_suffix():
    cards = parse_markdown(
        "## A<=>\none\n## A <=> B\ntwo\n## Plain\nbody <=>\n"
        "```\n## Inside <=>\n```\n"
    )
    assert [c.question for c in cards] == ["A<=>", "A <=> B", "Plain"]


def test_reversed_ids_survive_body_edits_and_cosmetic_heading_edits():
    plain = parse_markdown("## Term?\nOld definition\n")[0]
    before = parse_markdown("## Term? <=>\nOld definition\n")
    after = parse_markdown("## *term* <=>\nNew definition\n")
    assert before[0].id == plain.id
    assert [c.id for c in before] == [c.id for c in after]
    different = parse_markdown("## Another term <=>\nOld definition\n")
    assert set(c.id for c in before).isdisjoint(c.id for c in different)


def test_empty_reversed_heading_is_reported_once(tmp_path):
    text = "## Empty <=>\n \t\n"
    assert parse_markdown(text) == []
    assert [(c.question, c.answer) for c in parse_markdown(text, include_empty=True)] == [
        ("Empty", ""),
    ]
    (tmp_path / "empty.md").write_text(text, encoding="utf-8")
    assert check_deck(tmp_path) == [
        "empty.md:1: no cards",
        "empty.md:1: empty body for 'Empty'",
    ]


def test_reversed_duplicates_keep_distinct_stable_ids(tmp_path):
    path = tmp_path / "a.md"
    plain = "## Same\nSame\n## Same\nSame\n"
    path.write_text(plain, encoding="utf-8")
    original_ids = [c.id for c in load_deck(tmp_path)]
    path.write_text(plain.replace("## Same", "## Same <=>"), encoding="utf-8")
    (tmp_path / "b.md").write_text("## Same <=>\nSame\n", encoding="utf-8")
    cards = load_deck(tmp_path)
    ids = [c.id for c in cards]
    assert ids[0:4:2] == original_ids
    assert len(set(ids)) == 6
    path.write_text("## Same <=>\nEdited\n## Same <=>\nAlso edited\n", encoding="utf-8")
    assert [c.id for c in load_deck(tmp_path)] == ids


def test_moving_reversed_pair_keeps_both_ids(tmp_path):
    path = tmp_path / "old.md"
    path.write_text("## Term <=>\nDefinition\n", encoding="utf-8")
    before = [c.id for c in load_deck(tmp_path)]
    path.rename(tmp_path / "new.md")
    assert [c.id for c in load_deck(tmp_path)] == before


def test_check_compares_source_headings_not_generated_reverse_questions(tmp_path):
    (tmp_path / "a.md").write_text(
        "## First <=>\nShared answer\n## Second <=>\nShared answer\n"
        "## First\nOther answer\n", encoding="utf-8",
    )
    assert check_deck(tmp_path) == [
        "a.md:5: duplicate question 'First' (first at a.md:1)",
    ]


def test_cloze_numbers_make_distinct_cards_in_numeric_order():
    body = "{{c10::ten}} and {{c2::two}}\nAgain: {{c2::second}}."
    cards = parse_markdown(f"# Notes\n## Numbers\n{body}\n", "math")
    assert [c.cloze for c in cards] == [2, 10]
    assert len({c.id for c in cards}) == 2
    assert [(c.question, c.answer, c.deck, c.line, c.reverse) for c in cards] == [
        ("Numbers", body, "math", 2, False),
        ("Numbers", body, "math", 2, False),
    ]


def test_cloze_ids_follow_heading_and_number_not_body_or_position():
    before = parse_markdown("## Facts?\n{{c1::one}} {{c3::three}}\n")
    after = parse_markdown("## *facts*\n{{c3::edited}} {{c2::new}} {{c1::changed}}\n")
    ids = {c.cloze: c.id for c in after}
    assert all(c.id == ids[c.cloze] for c in before)
    plain_and_reverse = parse_markdown("## Facts <=>\none\n")
    other_heading = parse_markdown("## Other facts\n{{c1::one}}\n")
    assert set(ids.values()).isdisjoint(c.id for c in plain_and_reverse + other_heading)


@pytest.mark.parametrize("body", [
    "{{c0::zero}}", "{{c-1::negative}}", "{{c01::leading zero}}",
    "{{c1::}}", "{{c1:: \t}}", "{{c1::unclosed}", "{c1::text}}",
    "{{C1::uppercase}}", "{{cX::letter}}",
])
def test_invalid_cloze_syntax_stays_an_ordinary_answer(body):
    cards = parse_markdown(f"## Note\n{body}\n")
    assert len(cards) == 1
    assert cards[0].cloze is None
    assert cards[0].answer == body


def test_clozes_are_only_read_from_the_body_and_can_span_lines():
    card = parse_markdown("## Literal {{c1::heading}}\nPlain body\n")[0]
    assert card.cloze is None
    cards = parse_markdown("## Note\n{{c2::two\nlines}}\n")
    assert [c.cloze for c in cards] == [2]


def test_clozes_take_precedence_over_reverse_marker():
    cards = parse_markdown("## Note <=>\n{{c1::one}} {{c2::two}}\n")
    assert [c.cloze for c in cards] == [1, 2]
    assert all(c.question == "Note" and not c.reverse for c in cards)
    assert [c.id for c in cards] == [
        c.id for c in parse_markdown("## Note\n{{c1::one}} {{c2::two}}\n")
    ]


def test_duplicate_cloze_notes_keep_distinct_ids_and_metadata(tmp_path):
    path = tmp_path / "a.md"
    note = "## Note\n{{c1::one}} {{c2::two}}\n"
    path.write_text(note * 2, encoding="utf-8")
    (tmp_path / "b.md").write_text(note, encoding="utf-8")
    cards = load_deck(tmp_path)
    ids = [c.id for c in cards]
    assert len(set(ids)) == 6
    assert [c.cloze for c in cards] == [1, 2] * 3
    path.write_text(note.replace("one", "edited") * 2, encoding="utf-8")
    assert [c.id for c in load_deck(tmp_path)] == ids
    assert check_deck(tmp_path) == [
        "a.md:3: duplicate question 'Note' (first at a.md:1)",
        "b.md:1: duplicate question 'Note' (first at a.md:1)",
    ]


def test_moving_a_cloze_note_keeps_all_ids(tmp_path):
    path = tmp_path / "old.md"
    path.write_text("## Note\n{{c1::one}} {{c2::two}}\n", encoding="utf-8")
    before = [c.id for c in load_deck(tmp_path)]
    assert check_deck(tmp_path) == []
    path.rename(tmp_path / "new.md")
    assert [c.id for c in load_deck(tmp_path)] == before
