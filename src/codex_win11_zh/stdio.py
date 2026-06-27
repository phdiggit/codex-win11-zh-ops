from __future__ import annotations

import sys
from typing import TextIO


def configure_utf8_stdio() -> None:
    """Keep CLI and hook IO UTF-8 even on Windows cp936 consoles."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        _reconfigure_utf8(stream)


def _reconfigure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return
