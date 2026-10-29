import sqlite3
from pathlib import Path

import pytest

from drill import __version__
from drill.cli import main
from drill.deck import check_deck, load_deck
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


@pytest.mark.parametrize("command", ["review", "check"])
def test_non_utf8_deck_reports_file(deck_file, capsys, command):
    deck_file.write_bytes(b"## Question\n\xff\n")
    assert main([command, str(deck_file.parent)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error
    assert "UTF-8" in error
    assert "Traceback" not in error
    assert not (deck_file.parent / ".drill.db").exists()


@pytest.mark.parametrize("command", ["review", "check"])
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
def test_corrupt_database_reports_file(deck_file, capsys, custom_db):
    path = deck_file.parent / ("history.sqlite" if custom_db else ".drill.db")
    path.write_bytes(b"not a sqlite database")
    args = ["review", str(deck_file.parent)]
    if custom_db:
        args += ["--db", str(path)]
    assert main(args) == 2
    error = capsys.readouterr().err
    assert str(path) in error
    assert "not a database" in error
    assert "Traceback" not in error
    assert path.read_bytes() == b"not a sqlite database"


@pytest.mark.parametrize("lock", ["EXCLUSIVE", "IMMEDIATE"])
def test_locked_database_reports_file(deck_file, monkeypatch, capsys, lock):
    path = deck_file.parent / "history.sqlite"
    Store(path).close()
    connect = sqlite3.connect
    monkeypatch.setattr("drill.store.sqlite3.connect", lambda path: connect(path, timeout=0))
    blocker = connect(path)
    try:
        # EXCLUSIVE blocks startup; IMMEDIATE allows reads but blocks saving a grade.
        blocker.execute(f"BEGIN {lock}")
        assert main(["review", str(deck_file.parent), "--db", str(path)]) == 2
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


@pytest.mark.parametrize("custom_db", [False, True])
def test_unsupported_database_version_reports_file(deck_file, capsys, custom_db):
    path = deck_file.parent / ("history.sqlite" if custom_db else ".drill.db")
    with Store(path) as store:
        store.db.execute("PRAGMA user_version = 99")
    original = path.read_bytes()
    args = ["review", str(deck_file.parent)]
    if custom_db:
        args += ["--db", str(path)]

    assert main(args) == 2

    error = capsys.readouterr().err
    assert str(path) in error
    assert "schema version 99" in error
    assert "expects 1" in error
    assert "Traceback" not in error
    assert path.read_bytes() == original
