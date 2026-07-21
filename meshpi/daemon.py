from __future__ import annotations

import logging
import os
import signal
import threading
from contextlib import suppress

from meshpi.config import Settings
from meshpi.connections import ConnectionProfile, ConnectionStore
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.service import MeshtasticService

LOG = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s  %(levelname)s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if level != "DEBUG":
        logging.getLogger("meshtastic").setLevel(logging.WARNING)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        ctypes.windll.kernel32.CloseHandle(process)
        return True
    with suppress(OSError):
        os.kill(pid, 0)
        return True
    return False


def run_daemon(settings: Settings, parent_pid: int | None = None) -> None:
    configure_logging(settings.log_level)
    database = Database(settings.database_path)
    database.initialize()
    default_profile = (
        ConnectionProfile.tcp(settings.meshtastic_host, settings.meshtastic_port)
        if settings.meshtastic_host
        else None
    )
    connections = ConnectionStore(settings.connections_path, default_profile)
    events = EventHub()
    service = MeshtasticService(settings, database, events, connections=connections)
    stopping = threading.Event()

    def request_stop() -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    app = IPCApplication(
        settings,
        database,
        service,
        events,
        shutdown_callback=request_stop,
    )
    server = IPCServer(settings, app)

    def stop_handler(signum: int, frame: object) -> None:
        del signum, frame
        request_stop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop_handler)

    if parent_pid is not None:
        def watch_parent() -> None:
            while not stopping.wait(2):
                if not _pid_exists(parent_pid):
                    LOG.info("Foreldreprosessen er borte; stoppar session-daemonen")
                    request_stop()
                    return

        threading.Thread(target=watch_parent, name="parent-watch", daemon=True).start()

    service.start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.shutdown()
