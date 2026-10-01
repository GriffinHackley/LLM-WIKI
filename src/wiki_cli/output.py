"""Text output for people: wrapping to the terminal, aligned columns, and a little color.

Color is used only on a terminal (never when output is piped to an agent or a file) and
never with ``NO_COLOR`` set, so text output stays plain where it is parsed. JSON output
does not come through here.
"""

from __future__ import annotations

import os
import shutil
import sys
import textwrap

MAX_WIDTH = 100
_CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
_enabled: dict[int, bool] = {}


def _color(stream) -> bool:
    key = id(stream)
    if key not in _enabled:
        on = (hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ
              and os.environ.get("TERM") != "dumb")
        if on and sys.platform == "win32":
            on = _windows_ansi(stream)
        _enabled[key] = on
    return _enabled[key]


def _windows_ansi(stream) -> bool:
    """Turn on ANSI escapes in a Windows console; False where that is not possible."""
    try:
        import ctypes
        import msvcrt

        kernel = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError, ValueError, ImportError):
        return False


def style(text: str, *names: str, stream=None) -> str:
    if not text or not _color(stream or sys.stdout):
        return text
    return f"\033[{';'.join(_CODES[name] for name in names)}m{text}\033[0m"


def bold(text: str) -> str:
    return style(text, "bold")


def dim(text: str) -> str:
    return style(text, "dim")


def width(stream=None) -> int:
    """The width to wrap to: the terminal's, up to MAX_WIDTH. Output piped to an agent or
    a file is not wrapped (unless COLUMNS asks for it): a sentence stays on one line."""
    stream = stream or sys.stdout
    if "COLUMNS" not in os.environ and not (hasattr(stream, "isatty") and stream.isatty()):
        return 100_000
    return max(min(shutil.get_terminal_size((MAX_WIDTH, 24)).columns, MAX_WIDTH) - 1, 60)


def wrap(text: str, first: str = "", rest: str | None = None, *, stream=None) -> str:
    """``text`` wrapped to the terminal, after ``first`` on the first line and ``rest``
    (default: spaces as wide as ``first``) on the others. Indents may hold color codes."""
    rest = " " * _visible(first) if rest is None else rest
    lines = textwrap.wrap(text, width=width(stream) - _visible(first), break_on_hyphens=False,
                          break_long_words=False) or [""]
    # Later lines may use the width the first one's indent did not.
    if len(lines) > 1 and _visible(rest) != _visible(first):
        tail = textwrap.wrap(" ".join(lines[1:]), width=width(stream) - _visible(rest), break_on_hyphens=False,
                             break_long_words=False)
        lines = lines[:1] + tail
    return "\n".join([first + lines[0], *(rest + line for line in lines[1:])])


def _visible(text: str) -> int:
    length, escape = 0, False
    for char in text:
        if char == "\033":
            escape = True
        elif escape:
            escape = char != "m"
        else:
            length += 1
    return length


def heading(text: str, count: int | None = None) -> str:
    """A section title: ``Linked pages (3)``."""
    return bold(text) + (f" {dim(f'({count})')}" if count is not None else "")


def rows(items: list[tuple[str, ...]], *, indent: str = "  ", max_key: int = 32,
         styles: tuple[tuple[str, ...], ...] = ()) -> list[str]:
    """Aligned columns, the last one wrapped under itself. Each item is a tuple of cells;
    ``styles`` gives each column's style names (``("bold",)``, ``("dim",)``). A first
    cell wider than ``max_key`` gets a line of its own."""
    if not items:
        return []
    columns = len(items[0])
    widths = [max(len(item[col]) for item in items) for col in range(columns - 1)]
    widths[:1] = [min(widths[0], max_key)] if widths else []

    def styled(cell: str, col: int) -> str:
        names = styles[col] if col < len(styles) else ()
        return style(cell, *names) if names else cell

    out = []
    for item in items:
        overflow = columns > 1 and len(item[0]) > widths[0]
        if overflow:
            out.append(indent + styled(item[0], 0))
        cells = []
        for col in range(columns - 1):
            cell = "" if overflow and col == 0 else item[col]
            cells.append(styled(cell, col) + " " * max(widths[col] - len(cell), 0))
        lead = indent + "".join(cell + "  " for cell in cells)
        if not item[-1]:
            out.append(lead.rstrip())
            continue
        lines = textwrap.wrap(item[-1], width=max(width() - _visible(lead), 30), break_on_hyphens=False,
                              break_long_words=False) or [""]
        out.append(lead + styled(lines[0], columns - 1))
        out += [" " * _visible(lead) + styled(line, columns - 1) for line in lines[1:]]
    return out


def note(message: str) -> None:
    print(style("note:", "dim", stream=sys.stderr) + " " + message, file=sys.stderr)


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word if count == 1 else (many or word + 's')}"
