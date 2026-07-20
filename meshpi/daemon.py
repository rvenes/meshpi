from __future__ import annotations

import logging
import signal
import threading

from meshpi.config import Settings
from meshpi.database import Database
from meshpi.events import EventHub
from meshpi.ipc import IPCApplication, IPCServer
from meshpi.service import MeshtasticService


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s  %(levelname)s  %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if level != "DEBUG":
        logging.getLogger("meshtastic").setLevel(logging.WARNING)


def run_daemon(settings: Settings) -> None:
    configure_logging(settings.log_level)
    database = Database(settings.database_path)
    database.initialize()
    events = EventHub()
    service = MeshtasticService(settings, database, events)
    app = IPCApplication(settings, database, service, events)
    server = IPCServer(settings, app)
    stopping = threading.Event()

    def stop_handler(signum: int, frame: object) -> None:
        del signum, frame
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop_handler)

    service.start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.shutdown()

