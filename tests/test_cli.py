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


def test_non_utf8_deck_reports_file(deck_file, capsys):
    deck_file.write_bytes(b"## Question\n\xff\n")
    assert main(["review", str(deck_file.parent)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error
    assert "UTF-8" in error
    assert "Traceback" not in error
    assert not (deck_file.parent / ".drill.db").exists()


def test_unreadable_deck_reports_file(deck_file, monkeypatch, capsys):
    read_text = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == deck_file:
            raise PermissionError(13, "Permission denied", str(path))
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert main(["review", str(deck_file.parent)]) == 2
    error = capsys.readouterr().err
    assert str(deck_file) in error
    assert "Permission denied" in error
    assert "Traceback" not in error


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
