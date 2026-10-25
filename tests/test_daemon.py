from __future__ import annotations

import logging
import os

from meshpi.daemon import configure_logging


def test_configure_logging_rotates_private_file(tmp_path):
    log_file = tmp_path / "logs" / "meshpi.log"
    try:
        configure_logging(
            "INFO",
            log_file=log_file,
            log_max_bytes=1024,
            log_backup_count=2,
        )
        logger = logging.getLogger("meshpi.rotation-test")
        for index in range(100):
            logger.info("logglinje %s %s", index, "x" * 80)
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_file.is_file()
        assert log_file.with_name("meshpi.log.1").is_file()
        assert len(list(log_file.parent.glob("meshpi.log*"))) <= 3
        if os.name == "posix":
            assert log_file.stat().st_mode & 0o777 == 0o600
    finally:
        logging.shutdown()
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)


def test_duplicate_start_reserves_listener_before_touching_database(tmp_path, monkeypatch):
    import socket

    import pytest

    import meshpi.daemon as daemon
    from meshpi.config import Settings

    monkeypatch.setattr(daemon, "configure_logging", lambda *args, **kwargs: None)
    calls = []
    monkeypatch.setattr(daemon, "GatewayDatabase", lambda *args: calls.append("database"))
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        settings = Settings(ipc_transport="tcp", ipc_port=occupied.getsockname()[1],
                            ipc_token="r" * 40, database_path=tmp_path / "untouched.db")
        with pytest.raises(OSError):
            daemon.run_daemon(settings)
    assert calls == []
    assert not settings.database_path.exists()
    assert not settings.database_path.with_name("untouched-gateways").exists()


def test_initialization_failure_releases_unstarted_listener(tmp_path, monkeypatch):
    import pytest

    import meshpi.daemon as daemon
    from meshpi.config import Settings
    from meshpi.ipc import IPCServer

    monkeypatch.setattr(daemon, "configure_logging", lambda *args, **kwargs: None)
    servers = []

    def reserve(settings):
        result = IPCServer(settings)
        servers.append(result)
        # Calling socketserver.shutdown here would wait forever; close must be used.
        def forbidden_shutdown():
            raise AssertionError("Unstarted server must not wait for serve_forever")
        monkeypatch.setattr(result._server, "shutdown", forbidden_shutdown)
        return result

    class BrokenDatabase:
        def __init__(self, path):
            assert servers and servers[0]._server.socket.fileno() >= 0

        def initialize(self):
            raise RuntimeError("migration failed")

    monkeypatch.setattr(daemon, "IPCServer", reserve)
    monkeypatch.setattr(daemon, "GatewayDatabase", BrokenDatabase)
    settings = Settings(ipc_transport="tcp", ipc_port=0, ipc_token="r" * 40,
                        database_path=tmp_path / "test.db")
    with pytest.raises(RuntimeError, match="migration failed"):
        daemon.run_daemon(settings)
    assert servers[0]._server.socket.fileno() == -1
    host, port = servers[0].address
    replacement = IPCServer(Settings(ipc_transport="tcp", ipc_host=host,
                                     ipc_port=port, ipc_token="r" * 40))
    replacement.close()


def test_unstarted_ipc_requires_application_and_shutdown_never_waits(monkeypatch):
    import pytest

    from meshpi.config import Settings
    from meshpi.ipc import IPCServer

    server = IPCServer(Settings(ipc_transport="tcp", ipc_port=0, ipc_token="r" * 40))
    try:
        with pytest.raises(RuntimeError, match="ikkje klar"):
            server.serve_forever()
        monkeypatch.setattr(server._server, "shutdown", lambda: pytest.fail("must not wait"))
        server.shutdown()
        assert server._server.socket.fileno() == -1
    finally:
        server.close()


def test_busy_unix_listener_is_not_removed_as_stale(tmp_path, monkeypatch):
    import errno
    import socket

    import pytest

    from meshpi.ipc import IPCServer

    if os.name == "nt":
        pytest.skip("Unix socket test")
    path = tmp_path / "ipc.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen()
        original = path.stat()

        class BusyProbe:
            def settimeout(self, value):
                pass

            def connect(self, value):
                raise BlockingIOError(errno.EAGAIN, "listen queue full")

            def close(self):
                pass

        monkeypatch.setattr("meshpi.ipc.socket.socket", lambda *args: BusyProbe())
        with pytest.raises(BlockingIOError):
            IPCServer._prepare_unix_path(path)
        assert path.stat().st_ino == original.st_ino


def test_stop_accepting_can_keep_ownership_until_service_stops():
    import socket
    import threading

    import pytest

    from meshpi.config import Settings
    from meshpi.ipc import IPCApplication, IPCServer

    server = IPCServer(Settings(ipc_transport="tcp", ipc_port=0, ipc_token="r" * 40))
    server.set_application(IPCApplication(server.settings, None, None, None))
    entered = threading.Event()
    original_serve = server._server.serve_forever

    def serve(**kwargs):
        entered.set()
        original_serve(**kwargs)

    server._server.serve_forever = serve
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assert entered.wait(timeout=2)
    # An actual queued connection establishes that the reserved socket is live.
    with socket.create_connection(server.address, timeout=1):
        pass
    server.shutdown(close=False)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert server._server.socket.fileno() >= 0
    host, port = server.address
    with socket.socket() as duplicate, pytest.raises(OSError):
        duplicate.bind((host, port))
    server.close()
    assert server._server.socket.fileno() == -1
