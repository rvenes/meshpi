from __future__ import annotations

import logging
import os
import signal
import threading
from contextlib import suppress
from pathlib import Path

from meshpi.config import Settings
from meshpi.connections import ConnectionProfile, ConnectionStore
from meshpi.events import EventHub
from meshpi.gateway_database import GatewayDatabase
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.private_logging import PrivateRotatingFileHandler
from meshpi.service import MeshtasticService

LOG = logging.getLogger(__name__)


def configure_logging(
    level: str,
    *,
    log_file: Path | None = None,
    log_max_bytes: int = 5 * 1024 * 1024,
    log_backup_count: int = 3,
) -> None:
    handlers: list[logging.Handler] | None = None
    if log_file is not None:
        handlers = [
            PrivateRotatingFileHandler(
                log_file,
                maxBytes=log_max_bytes,
                backupCount=log_backup_count,
                encoding="utf-8",
            )
        ]
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s  %(levelname)s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
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
    configure_logging(
        settings.log_level,
        log_file=settings.log_file,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )
    # Reserve the single-owner endpoint before any migration or database writes.
    server = IPCServer(settings)
    stopping = threading.Event()
    service: MeshtasticService | None = None
    maintenance: threading.Thread | None = None

    def request_stop() -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=lambda: server.shutdown(close=False), daemon=True).start()

    try:
        database = GatewayDatabase(settings.database_path)
        database.initialize()
        default_profile = (
            ConnectionProfile.tcp(settings.meshtastic_host, settings.meshtastic_port)
            if settings.meshtastic_host
            else None
        )
        connections = ConnectionStore(settings.connections_path, default_profile)
        events = EventHub()
        service = MeshtasticService(settings, database, events, connections=connections)
        app = IPCApplication(
            settings, database, service, events, shutdown_callback=request_stop,
        )
        server.set_application(app)

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

        def maintain_history() -> None:
            while not stopping.is_set():
                try:
                    results = database.maintain_history()
                    if any(any(counts.values()) for counts in results.values()):
                        events.publish({"type": "history_changed"})
                except Exception:
                    LOG.exception("Automatisk historikkopprydding feila; prøver igjen neste time")
                stopping.wait(3600)

        maintenance = threading.Thread(
            target=maintain_history, name="history-maintenance", daemon=True,
        )
        maintenance.start()
        if not stopping.is_set():
            server.serve_forever()
    finally:
        stopping.set()
        if maintenance is not None:
            maintenance.join(timeout=15)
        try:
            if service is not None:
                service.stop()
        finally:
            server.close()
