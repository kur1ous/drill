import sqlite3
from pathlib import Path

import pytest

from drill import __version__
from drill.cli import main
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


@pytest.fixture
def deck_file(tmp_path, monkeypatch):
    path = tmp_path / "notes.md"
    path.write_text("## Question\nAnswer\n", encoding="utf-8")
    keys = iter(" 3")
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
