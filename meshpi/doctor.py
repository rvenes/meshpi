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


def offline_checks(settings: Settings) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = [
        (
            "Python",
            sys.version_info >= (3, 11),
            f"{platform.python_version()} ({sys.executable})",
        ),
        ("MeshPi", True, __version__),
        (
            "Konfigurasjon",
            settings.background_mode in {"always", "session"},
            f"bakgrunn={settings.background_mode}",
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
        checks.append(("Lokal lagring", True, "SQLite og profilar fungerer"))
    except Exception as exc:
        checks.append(("Lokal lagring", False, str(exc)))
    return checks
