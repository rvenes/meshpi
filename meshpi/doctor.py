from __future__ import annotations

import gc
import platform
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from meshpi import __version__
from meshpi.config import Settings
from meshpi.connections import ConnectionProfile, ConnectionStore
from meshpi.database import Database
from meshpi.i18n import tr


def offline_checks(settings: Settings) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = [
        (
            "Python",
            sys.version_info >= (3, 11),
            f"{platform.python_version()} ({sys.executable})",
        ),
        ("MeshPi", True, __version__),
        (
            tr("doctor.configuration"),
            settings.background_mode in {"always", "session"},
            tr("doctor.background", mode=settings.background_mode),
        ),
    ]
    try:
        with TemporaryDirectory(prefix="meshpi-doctor-") as directory:
            root = Path(directory)
            database = Database(root / "doctor.db")
            database.initialize()
            store = ConnectionStore(
                root / "connections.json",
                ConnectionProfile.tcp("127.0.0.1", 4403),
            )
            store.active_profile()
            del database, store
            gc.collect()
        checks.append((tr("doctor.storage"), True, tr("doctor.storage_ok")))
    except Exception as exc:
        checks.append((tr("doctor.storage"), False, str(exc)))
    return checks
