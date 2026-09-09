from __future__ import annotations

import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO


def open_private_log(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Preserve private creation permissions on every rollover, not just startup."""

    def _open(self) -> TextIO:
        return open_private_log(Path(self.baseFilename))
