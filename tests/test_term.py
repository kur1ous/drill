import io
import os

from drill import term


def test_system_terminal_reads_current_width(monkeypatch):
    monkeypatch.setattr(term.sys, "stdin", io.StringIO())
    monkeypatch.setattr(term.sys, "stdout", io.StringIO())
    widths = iter((30, 120))
    monkeypatch.setattr(
        term.shutil, "get_terminal_size", lambda fallback: os.terminal_size((next(widths), 24))
    )
    terminal = term.system_terminal()
    terminal.refresh_width()
    assert terminal.width == 30
    terminal.refresh_width()
    assert terminal.width == 120
