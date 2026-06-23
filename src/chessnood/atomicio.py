"""Crash-safe file writes.

The appliance has no clean shutdown -- the player just switches the power off,
sometimes mid-write. A plain ``write_text`` can then leave a truncated file, so
the saved game (or status) is lost on the next boot. Writing to a temp file in
the same directory and ``os.replace``-ing it into place is atomic on POSIX: a
reader ever sees either the old, complete file or the new, complete file -- never
a half-written one.
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically. Raises OSError on failure.

    The temp file is created beside the target (same filesystem, so ``replace``
    is a rename, not a copy) and cleaned up if the write fails partway.
    """
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp")
    try:
        with tmp.open("w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())   # get the bytes onto the SD card before the rename
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
