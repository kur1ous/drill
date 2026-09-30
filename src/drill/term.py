"""Plain ANSI output and single-key input. No curses, no dependencies."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from typing import Callable

# One accent (a muted teal) and dim for everything secondary. Bold for the question.
_ACCENT = "\x1b[38;5;109m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"

EOF_KEY = "\x04"  # what read_key reports when stdin is closed
IGNORED_KEY = "\x00"  # arrow keys and the like


@dataclass
class Terminal:
    write: Callable[[str], None]
    read_key: Callable[[], str]
    ansi: bool = False
    width: int = 80

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self.ansi else text

    def dim(self, text: str) -> str:
        return self._wrap(_DIM, text)

    def bold(self, text: str) -> str:
        return self._wrap(_BOLD, text)

    def accent(self, text: str) -> str:
        return self._wrap(_ACCENT, text)

    def clear(self) -> None:
        if self.ansi:
            self.write("\x1b[2J\x1b[H")
        else:
            self.write("\n")


def _read_key_windows() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow and function keys arrive as a two-part sequence
        msvcrt.getwch()
        return IGNORED_KEY
    return ch


def _read_key_posix() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)  # keeps Ctrl-C working, unlike setraw
        return sys.stdin.read(1) or EOF_KEY
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _read_key_piped() -> str:
    return sys.stdin.read(1) or EOF_KEY


def system_terminal() -> Terminal:
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    ansi = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
    # Notes are UTF-8; the Windows console default (cp1252) would raise on the first accented letter.
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if ansi and os.name == "nt":
        os.system("")  # switches the Windows console into VT mode
    if not interactive:
        read_key = _read_key_piped
    elif os.name == "nt":
        read_key = _read_key_windows
    else:
        read_key = _read_key_posix

    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    return Terminal(write, read_key, ansi, shutil.get_terminal_size((80, 24)).columns)
