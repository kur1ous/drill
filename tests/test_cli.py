import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from drill import __version__, fsrs
from drill.cli import main
from drill.deck import check_deck, load_deck
from drill.fsrs import Grade
from drill.schedule import apply_grade, preview_intervals
from drill.store import Store
from drill.term import EOF_KEY, Terminal


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: drill" in capsys.readouterr().err


def test_init_creates_valid_example_deck(tmp_path, capsys):
    directory = tmp_path / "nested" / "notes"
    assert main(["init", str(directory)]) == 0
    cards = load_deck(directory)
    assert len(cards) == 2
    assert all(card.question and card.answer for card in cards)
    assert check_deck(directory) == []
    assert list(directory.iterdir()) == [directory / "example.md"]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 3
    assert str(directory / "example.md") in captured.out
    assert "## heading is a question" in captured.out
    assert "answer" in captured.out
    assert "UTF-8 .md" in captured.out


def test_init_preserves_existing_files(tmp_path):
    existing = {"notes.md": b"## My question\nMy answer\n", ".drill.db": b"history"}
    for name, content in existing.items():
        (tmp_path / name).write_bytes(content)
    assert main(["init", str(tmp_path)]) == 0
    for name, content in existing.items():
        assert (tmp_path / name).read_bytes() == content


def test_init_refuses_to_replace_example(tmp_path, capsys):
    example = tmp_path / "example.md"
    example.write_bytes(b"my own deck")
    assert main(["init", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(example) in captured.err
    assert example.read_bytes() == b"my own deck"


@pytest.mark.parametrize("nested", [False, True])
def test_init_reports_file_in_directory_path(tmp_path, capsys, nested):
    existing = tmp_path / "notes"
    existing.write_bytes(b"keep me")
    directory = existing / "child" if nested else existing
    assert main(["init", str(directory)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(existing) in captured.err
    assert "Traceback" not in captured.err
    assert existing.read_bytes() == b"keep me"


@pytest.mark.parametrize("operation", ["mkdir", "open"])
def test_init_reports_permission_errors(tmp_path, capsys, monkeypatch, operation):
    def denied(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, operation, denied)
    assert main(["init", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(tmp_path) in captured.err
    assert "Permission denied" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "example.md").exists()


@pytest.mark.parametrize("limit", ["0", "-1", "1.5", "many"])
def test_review_rejects_invalid_session_limit(tmp_path, capsys, limit):
    with pytest.raises(SystemExit) as exc:
        main(["review", str(tmp_path), "--limit", limit])
    assert exc.value.code == 2
    assert "--limit" in capsys.readouterr().err
    assert not (tmp_path / ".drill.db").exists()


def test_review_cli_caps_session(tmp_path, monkeypatch):
    (tmp_path / "notes.md").write_text("## One\nA\n## Two\nB\n", encoding="utf-8")
    keys = iter("b 3 3")
    output = []
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(output.append, lambda: next(keys, EOF_KEY), ansi=False),
    )
    assert main(["review", str(tmp_path), "--limit", "1"]) == 0
    assert "0 due, 1 new" in "".join(output)
    assert "Done. 1 card reviewed." in "".join(output)
    with Store(tmp_path / ".drill.db") as store:
        assert len(store.states()) == 1


@pytest.mark.parametrize("order, first", [
    (None, "Three"), ("due", "Three"), ("random", "Two"), ("deck", "One"),
])
def test_review_cli_orders_before_session_limit(tmp_path, monkeypatch, order, first):
    (tmp_path / "notes.md").write_text("## One\nA\n## Two\nB\n## Three\nC\n", encoding="utf-8")
    cards = load_deck(tmp_path)
    today = date.today()
    with Store(tmp_path / ".drill.db") as store:
        for i, days in ((0, 0), (2, -1)):
            state = apply_grade(None, cards[i].id, cards[i].deck, Grade.GOOD, today)
            store.record(None, replace(state, due=today + timedelta(days=days)), Grade.GOOD)
    keys = iter("bq")
    output = []
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(output.append, lambda: next(keys, EOF_KEY), ansi=False),
    )
    monkeypatch.setattr("drill.session.random.shuffle", lambda queue: queue.reverse())
    args = ["review", str(tmp_path), "--limit", "1"]
    if order is not None:
        args += ["--order", order]
    assert main(args) == 0
    text = "".join(output)
    assert f"    {first}\n" in text
    for other in {"One", "Two", "Three"} - {first}:
        assert f"    {other}\n" not in text


def test_review_rejects_unknown_order(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["review", str(tmp_path), "--order", "unknown"])
    assert exc.value.code == 2
    assert "--order" in capsys.readouterr().err
    assert not (tmp_path / ".drill.db").exists()


@pytest.mark.parametrize("file_input", [False, True])
@pytest.mark.parametrize("config, flags, expected", [
    (None, [], (10, "due", ".drill.db")),
    ("", [], (10, "due", ".drill.db")),
    ('order = "deck"', [], (10, "deck", ".drill.db")),
    ('new_per_day = 0', [], (0, "due", ".drill.db")),
    ('new_per_day = 3\norder = "random"\ndb = "history.sqlite"',
     [], (3, "random", "history.sqlite")),
    ('new_per_day = 3\norder = "random"\ndb = "history.sqlite"',
     ["--new", "0", "--order", "due", "--db", "override.sqlite"],
     (0, "due", "override.sqlite")),
])
def test_review_config_precedence(tmp_path, monkeypatch, file_input, config, flags, expected):
    folder = tmp_path / "notes"
    folder.mkdir()
    deck = folder / "cards.md"
    deck.write_text("## Q\nA\n", encoding="utf-8")
    if config is not None:
        (folder / ".drill.toml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Discovery is tied to the deck folder, not the working directory.
    (tmp_path / ".drill.toml").write_text("invalid TOML", encoding="utf-8")
    calls = []
    monkeypatch.setattr("drill.session.run_review", lambda *a, **kw: calls.append((a, kw)) or 0)
    monkeypatch.setattr("drill.term.system_terminal", lambda: None)
    target = deck if file_input else folder
    assert main(["review", str(target), *flags]) == 0
    (args, kwargs), = calls
    count, order, db = expected
    assert args[0] == target
    assert args[1] == (Path(db) if "--db" in flags else folder / db)
    assert args[2] == count
    assert kwargs["order"] == order
    assert kwargs["retention"] == 0.9


@pytest.mark.parametrize("config, setting", [
    (b"new_per_day = [", "invalid config"),
    (b"\xff", "cannot read"),
    (b"new_per_day = true", "new_per_day"),
    (b"new_per_day = -1", "new_per_day"),
    (b"new_per_day = 1.5", "new_per_day"),
    (b'new_per_day = "10"', "new_per_day"),
    (b'order = "unknown"', "order"),
    (b"order = []", "order"),
    (b"db = 12", "db"),
    (b'db = " "', "db"),
    (b'db = "\\u0000"', "db"),
    (b"new_perday = 4", "unknown setting"),
    (b"deck = []", "deck"),
    (b"[deck]\nnotes = 3", "deck.'notes'"),
    (b'[deck.""]\nretention = 0.8', "nonempty glob"),
    (b'[deck."unit/*"]\norder = "due"', "unknown setting"),
    *[(f'[deck."unit/*"]\nnew_per_day = {value}'.encode(), "new_per_day")
      for value in ("-1", "true", "1.5", '"2"', "[]")],
    *[(f'[deck."unit/*"]\nretention = {value}'.encode(), "retention")
      for value in ("0", "1", "true", "nan", "inf", '"0.9"', "[]")],
    *[(f"retention = {value}".encode(), "retention") for value in
      ("0", "1", "-0.1", "1.1", "true", '"0.9"', "[]", "nan", "inf", "-inf")],
    *[(f"weights = {value}".encode(), "weights") for value in
      ('"defaults"', "17", "{}", "[]", "[1, 2]", str([1] * 16), str([1] * 18))],
    *[(f"weights = [{value}, {', '.join(map(str, fsrs.DEFAULT_WEIGHTS[1:]))}]".encode(),
       "weights[0]")
      for value in ("true", '"1"', "[]", "nan", "inf", "-inf", "0", "101")],
])
def test_review_reports_invalid_config_before_starting(tmp_path, monkeypatch, capsys, config, setting):
    path = tmp_path / ".drill.toml"
    path.write_bytes(config)
    monkeypatch.setattr("drill.term.system_terminal", lambda: pytest.fail("unexpected terminal"))
    assert main(["review", str(tmp_path)]) == 2
    error = capsys.readouterr().err
    assert str(path) in error
    assert setting in error
    assert "Traceback" not in error
    assert not (tmp_path / ".drill.db").exists()


def test_review_reports_unreadable_config(tmp_path, monkeypatch, capsys):
    path = tmp_path / ".drill.toml"

    def denied(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "read_text", denied)
    assert main(["review", str(tmp_path)]) == 2
    error = capsys.readouterr().err
    assert str(path) in error and "Permission denied" in error
    assert not (tmp_path / ".drill.db").exists()


@pytest.fixture
def deck_file(tmp_path, monkeypatch):
    path = tmp_path / "notes.md"
    path.write_text("## Question\nAnswer\n", encoding="utf-8")
    keys = iter("b 3")
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(lambda text: None, lambda: next(keys, EOF_KEY)),
    )
    return path


@pytest.mark.parametrize("absolute", [False, True])
def test_review_saves_to_configured_database(deck_file, absolute):
    folder = deck_file.parent
    database = folder / "history.sqlite"
    db = database.as_posix() if absolute else database.name
    (folder / ".drill.toml").write_text(f'db = "{db}"\n', encoding="utf-8")
    assert main(["review", str(deck_file)]) == 0
    with Store(database) as store:
        assert len(store.states()) == 1
    assert not (folder / ".drill.db").exists()


@pytest.mark.parametrize("selection, expected", [
    (["--only", "unit/*"], ["unit/biology", "unit/deep/chemistry"]),
    (["--only", "unit/b?ology"], ["unit/biology"]),
    (["--only", "[ab]lgebra"], ["algebra"]),
    (["--only", "*"], ["algebra", "unit/biology", "unit/deep/chemistry"]),
])
def test_review_only_selects_deck_names(tmp_path, monkeypatch, selection, expected):
    for name in ["algebra", "unit/biology", "unit/deep/chemistry"]:
        path = tmp_path / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"## {name}\nAnswer\n", encoding="utf-8")
    keys = iter("b" + " 3" * 3)
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(lambda text: None, lambda: next(keys, EOF_KEY)),
    )
    assert main(["review", str(tmp_path), *selection]) == 0
    with Store(tmp_path / ".drill.db") as store:
        assert sorted(s.deck for s in store.states().values()) == expected


@pytest.mark.parametrize("file_input", [False, True])
@pytest.mark.parametrize("custom_db", [False, True])
def test_selected_duplicate_keeps_folder_identity(deck_file, file_input, custom_db):
    root = deck_file.parent
    (root / "a.md").write_text("## Question\nOther answer\n", encoding="utf-8")
    selected = next(c for c in load_deck(root) if c.deck == "notes")
    args = ["review", str(deck_file if file_input else root)]
    if not file_input:
        args += ["--only", "notes"]
    database = root / ("custom.sqlite" if custom_db else ".drill.db")
    if custom_db:
        args += ["--db", str(database)]
    assert main(args) == 0
    with Store(database) as store:
        assert list(store.states()) == [selected.id]
        assert store.get(selected.id).deck == "notes"
    if custom_db:
        assert not (root / ".drill.db").exists()


@pytest.mark.parametrize("file_input", [False, True])
@pytest.mark.parametrize("pattern", ["missing*", "NOTES", "notes.md"])
def test_review_no_matching_decks_does_not_open_database(
    deck_file, monkeypatch, file_input, pattern,
):
    output = []
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(output.append, lambda: pytest.fail("unexpected input"), ansi=False),
    )
    target = deck_file if file_input else deck_file.parent
    assert main(["review", str(target), "--only", pattern]) == 1
    assert "No cards found matching" in "".join(output)
    assert not (deck_file.parent / ".drill.db").exists()


@pytest.mark.skipif(Path("notes") != Path("NOTES"), reason="Windows path casing")
@pytest.mark.parametrize("pattern", [None, "notes", "NOTES"])
def test_file_review_uses_native_path_casing(deck_file, pattern):
    root = deck_file.parent
    (root / "a.md").write_text("## Question\nOther answer\n", encoding="utf-8")
    selected = next(c for c in load_deck(root) if c.deck == "notes")
    target = root / "NOTES.md"
    assert target.is_file()
    args = ["review", str(target)]
    if pattern is not None:
        args += ["--only", pattern]
    database = root / ".drill.db"
    if pattern == "NOTES":
        assert main(args) == 1
        assert not database.exists()
    else:
        assert main(args) == 0
        with Store(database) as store:
            assert list(store.states()) == [selected.id]
            assert store.get(selected.id).deck == "notes"


@pytest.mark.parametrize("existing", [False, True])
def test_review_rejects_missing_or_non_markdown_file(tmp_path, capsys, existing):
    path = tmp_path / ("notes.txt" if existing else "missing.md")
    if existing:
        path.write_text("## Q\nAnswer\n", encoding="utf-8")
    assert main(["review", str(path)]) == 2
    assert str(path) in capsys.readouterr().err
    assert not (tmp_path / ".drill.db").exists()


def test_file_review_reports_decode_error(deck_file, capsys):
    deck_file.write_bytes(b"\xff")
    assert main(["review", str(deck_file)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error and "UTF-8" in error
    assert not (deck_file.parent / ".drill.db").exists()


def test_filtered_review_shares_daily_new_allowance(deck_file, monkeypatch):
    root = deck_file.parent
    assert main(["review", str(deck_file), "--new", "1"]) == 0
    (root / "other.md").write_text("## Other\nAnswer\n", encoding="utf-8")
    output = []
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(output.append, lambda: pytest.fail("unexpected input"), ansi=False),
    )
    assert main(["review", str(root), "--only", "other", "--new", "1"]) == 0
    assert "Nothing due." in "".join(output)
    with Store(root / ".drill.db") as store:
        assert len(store.states()) == 1


@pytest.mark.parametrize("command", ["review", "check"])
@pytest.mark.parametrize("nested", [False, True])
def test_markdown_directory_is_not_read_as_a_deck(deck_file, capsys, command, nested):
    root = deck_file.parent
    directory = root / "chapter.md"
    directory.mkdir()
    if nested:
        deck_file.rename(directory / deck_file.name)

    assert main([command, str(root)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    if command == "check":
        assert captured.out == "No issues found.\n"
        assert not (root / ".drill.db").exists()
    else:
        with Store(root / ".drill.db") as store:
            states = list(store.states().values())
        assert len(states) == 1
        assert states[0].deck == ("chapter.md/notes" if nested else "notes")


@pytest.mark.parametrize("command", ["review", "check", "stats", "forecast"])
def test_non_utf8_deck_reports_file(deck_file, capsys, command):
    deck_file.write_bytes(b"## Question\n\xff\n")
    assert main([command, str(deck_file.parent)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error
    assert "UTF-8" in error
    assert "Traceback" not in error
    assert not (deck_file.parent / ".drill.db").exists()


@pytest.mark.parametrize("command", ["review", "check", "stats", "forecast"])
def test_unreadable_deck_reports_file(deck_file, monkeypatch, capsys, command):
    read_text = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == deck_file:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert main([command, str(deck_file.parent)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error
    assert "Permission denied" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("contents, status, output", [
    ("## Q\nAnswer\n", 0, "No issues found.\n"),
    ("## Q\n", 1, "notes.md:1: no cards\nnotes.md:1: empty body for 'Q'\n"),
])
def test_check_status_and_output_without_database(tmp_path, capsys, contents, status, output):
    (tmp_path / "notes.md").write_text(contents, encoding="utf-8")
    assert main(["check", str(tmp_path)]) == status
    captured = capsys.readouterr()
    assert captured.out == output
    assert captured.err == ""
    assert not (tmp_path / ".drill.db").exists()


def test_check_does_not_open_existing_database(tmp_path, capsys):
    database = tmp_path / ".drill.db"
    database.write_bytes(b"not a database")
    assert main(["check", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "No issues found.\n"
    assert database.read_bytes() == b"not a database"


@pytest.mark.parametrize("existing_file", [False, True])
def test_check_requires_a_directory(tmp_path, capsys, existing_file):
    path = tmp_path / "notes.md"
    if existing_file:
        path.write_text("## Q\nAnswer\n", encoding="utf-8")
    assert main(["check", str(path)]) == 2
    assert capsys.readouterr().err == f"drill: not a directory: {path}\n"


@pytest.mark.parametrize("custom_db", [False, True])
@pytest.mark.parametrize("command", ["review", "stats", "forecast"])
def test_corrupt_database_reports_file(deck_file, capsys, custom_db, command):
    path = deck_file.parent / ("history.sqlite" if custom_db else ".drill.db")
    path.write_bytes(b"not a sqlite database")
    args = [command, str(deck_file.parent)]
    if custom_db:
        args += ["--db", str(path)]
    assert main(args) == 2
    error = capsys.readouterr().err
    assert str(path) in error
    assert "not a database" in error
    assert "Traceback" not in error
    assert path.read_bytes() == b"not a sqlite database"


@pytest.mark.parametrize("lock", ["EXCLUSIVE", "IMMEDIATE"])
@pytest.mark.parametrize("command", ["review", "stats", "forecast"])
def test_locked_database_reports_file(deck_file, monkeypatch, capsys, lock, command):
    path = deck_file.parent / "history.sqlite"
    Store(path).close()
    connect = sqlite3.connect
    monkeypatch.setattr("drill.store.sqlite3.connect", lambda path, **kwargs: connect(path, timeout=0))
    blocker = connect(path)
    try:
        # Both lock modes block the startup transaction.
        blocker.execute(f"BEGIN {lock}")
        assert main([command, str(deck_file.parent), "--db", str(path)]) == 2
        error = capsys.readouterr().err
        assert str(path) in error
        assert "locked" in error
        assert "Traceback" not in error
    finally:
        blocker.rollback()
        blocker.close()
    with Store(path) as store:
        assert store.states() == {}
        assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


@pytest.mark.parametrize("read_number", [1, 3])
def test_interrupt_during_queue_or_summary_closes_database(
    deck_file, monkeypatch, capsys, read_number,
):
    connections = []
    states = Store.states

    def interrupted_states(store):
        connections.append(store.db)
        if len(connections) == read_number:
            raise KeyboardInterrupt
        return states(store)

    monkeypatch.setattr(Store, "states", interrupted_states)
    keys = iter("q")
    monkeypatch.setattr("drill.term.system_terminal", lambda: Terminal(
        lambda text: None, lambda: next(keys, EOF_KEY), ansi=False,
    ))
    assert main(["review", str(deck_file.parent)]) == 130
    assert capsys.readouterr().err == "drill: stopped.\n"
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize("custom_db", [False, True])
@pytest.mark.parametrize("command", ["review", "stats", "forecast"])
def test_unsupported_database_version_reports_file(deck_file, capsys, custom_db, command):
    path = deck_file.parent / ("history.sqlite" if custom_db else ".drill.db")
    with Store(path) as store:
        store.db.execute("PRAGMA user_version = 99")
    original = path.read_bytes()
    args = [command, str(deck_file.parent)]
    if custom_db:
        args += ["--db", str(path)]

    assert main(args) == 2

    error = capsys.readouterr().err
    assert str(path) in error
    assert "schema version 99" in error
    assert "expects 1" in error
    assert "Traceback" not in error
    assert path.read_bytes() == original


@pytest.mark.parametrize("file_input", [False, True])
def test_config_retention_controls_display_and_saved_due(tmp_path, monkeypatch, file_input):
    deck = tmp_path / "notes.md"
    deck.write_text("## Q\nA\n", encoding="utf-8")
    (tmp_path / ".drill.toml").write_text("retention = 0.8\n", encoding="utf-8")
    card, = load_deck(tmp_path)
    today = date.today()
    intervals = preview_intervals(None, today, card.id, retention=0.8)
    assert 9 <= intervals[Grade.GOOD] <= 11
    keys = iter("b 3")
    output = []
    monkeypatch.setattr(
        "drill.term.system_terminal",
        lambda: Terminal(output.append, lambda: next(keys, EOF_KEY), ansi=False),
    )
    assert main(["review", str(deck if file_input else tmp_path)]) == 0
    for grade, days in intervals.items():
        assert f"{int(grade)} {grade.name.lower()} {days}d" in "".join(output)
    with Store(tmp_path / ".drill.db") as store:
        assert store.get(card.id).due == today + timedelta(days=intervals[Grade.GOOD])


@pytest.mark.parametrize("flags, expected", [([], 2), (["--new", "0"], 0), (["--new", "1"], 1)])
def test_deck_config_overrides_and_cli_new(tmp_path, monkeypatch, flags, expected):
    (tmp_path / "notes.md").write_text("## One\nA\n## Two\nB\n", encoding="utf-8")
    (tmp_path / ".drill.toml").write_text(
        'new_per_day = 0\nretention = 0.95\n'
        '[deck."*"]\nnew_per_day = 2\nretention = 0.85\n'
        '[deck."NOTES"]\nnew_per_day = 0\n'
        '[deck."notes"]\nretention = 0.8\n', encoding="utf-8",
    )
    keys = iter("b 3 3")
    output = []
    monkeypatch.setattr("drill.term.system_terminal", lambda: Terminal(
        output.append, lambda: next(keys, EOF_KEY), ansi=False,
    ))
    assert main(["review", str(tmp_path), *flags]) == 0
    with Store(tmp_path / ".drill.db") as store:
        states = store.states()
    assert len(states) == expected
    for state in states.values():
        intervals = preview_intervals(None, date.today(), state.id, retention=0.8)
        assert state.due == date.today() + timedelta(days=intervals[Grade.GOOD])
        assert f"3 good {intervals[Grade.GOOD]}d" in "".join(output)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("grade", list(Grade))
def test_custom_weights_control_previews_and_saved_memory(tmp_path, monkeypatch, existing, grade):
    deck = tmp_path / "notes.md"
    deck.write_text("## Q\nA\n", encoding="utf-8")
    weights = (1, 2, 12, 25, 6, 1, 1, 0.2, 0.5, 0.1, 1, 0.5, 0.1, 0.3, 1, 0.5, 2)
    (tmp_path / ".drill.toml").write_text(
        f'weights = {list(weights)}\n[deck."notes"]\nretention = 0.8\n', encoding="utf-8",
    )
    card, = load_deck(tmp_path)
    today = date.today()
    before = None
    if existing:
        before = apply_grade(None, card.id, card.deck, Grade.GOOD, today - timedelta(days=30))
        before = replace(before, stability=30, difficulty=5, due=today)
        with Store(tmp_path / ".drill.db") as store:
            store.record(None, before, Grade.GOOD)
        expected_memory = fsrs.next_memory(fsrs.Memory(30, 5), grade, 30, weights)
    else:
        expected_memory = fsrs.initial(grade, weights)
    intervals = preview_intervals(before, today, card.id, retention=0.8, weights=weights)
    keys = iter(f"b {int(grade)}q")
    output = []
    monkeypatch.setattr("drill.term.system_terminal", lambda: Terminal(
        output.append, lambda: next(keys, EOF_KEY), ansi=False,
    ))
    assert main(["review", str(deck if existing else tmp_path)]) == 0
    for offered_grade, days in intervals.items():
        assert f"{int(offered_grade)} {offered_grade.name.lower()} {days}d" in "".join(output)
    with Store(tmp_path / ".drill.db") as store:
        saved = store.get(card.id)
    assert fsrs.Memory(saved.stability, saved.difficulty) == expected_memory
    assert saved.due == today + timedelta(days=intervals[grade])
    default = apply_grade(before, card.id, card.deck, grade, today, retention=0.8)
    assert saved.stability != default.stability
