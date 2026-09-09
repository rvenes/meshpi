from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from typing import Any, Protocol

from meshpi.config import Settings
from meshpi.i18n import get_language, tr
from meshpi.ipc_identity import verify_windows_peer

MAX_RESPONSE_BYTES = 2_000_000


class WatchStream(Protocol):
    def __iter__(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class _SocketLineStream:
    """Linjedelt lesing direkte frå ein sokkel som kan avbrytast på Windows."""

    def __init__(self, sock: socket.socket):
        self._socket = sock
        self._buffer = bytearray()

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        line = self.readline(MAX_RESPONSE_BYTES + 1)
        if not line:
            raise StopIteration
        if len(line) > MAX_RESPONSE_BYTES:
            raise CLIError(tr("client.response_too_large"))
        return line

    def readline(self, maximum: int) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                end = newline + 1
                line = bytes(self._buffer[:end])
                del self._buffer[:end]
                return line
            if len(self._buffer) >= maximum:
                line = bytes(self._buffer[:maximum])
                del self._buffer[:maximum]
                return line
            chunk = self._socket.recv(min(64 * 1024, maximum - len(self._buffer)))
            if not chunk:
                line = bytes(self._buffer)
                self._buffer.clear()
                return line
            self._buffer.extend(chunk)

    def close(self) -> None:
        # Sokkelen er eigd og lukka av kallaren.
        pass


class CLIError(RuntimeError):
    pass


class CLIUnavailableError(CLIError):
    """IPC-tenesta lyttar ikkje, så ho kan trygt startast."""


def _connect(settings: Settings, timeout: float) -> socket.socket:
    if settings.ipc_uses_unix:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(settings.ipc_socket_path))
        except OSError:
            sock.close()
            raise
        return sock
    sock = socket.create_connection(
        (settings.ipc_host, settings.ipc_port),
        timeout=timeout,
    )
    if os.name == "nt":
        try:
            verify_windows_peer(sock)
        except OSError as exc:
            sock.close()
            raise CLIError(tr("client.untrusted_peer")) from exc
    return sock


def request(
    settings: Settings, payload: dict[str, Any], timeout: float = 10
) -> dict[str, Any]:
    try:
        sock = _connect(settings, timeout)
    except OSError as exc:
        raise CLIUnavailableError(
            tr("client.unavailable")
        ) from exc

    try:
        with sock, sock.makefile("rwb") as stream:
            authenticated = payload | {
                "token": settings.ipc_token,
                "language": get_language(),
            }
            stream.write(
                json.dumps(authenticated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            raw = stream.readline(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise CLIError(
            tr("client.connection_broken")
        ) from exc
    if not raw:
        raise CLIError(tr("client.closed_without_response"))
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CLIError(tr("client.response_too_large"))
    response = json.loads(raw)
    if not response.get("ok"):
        raise CLIError(str(response.get("error", tr("common.unknown_error"))))
    return response


def open_watch(
    settings: Settings, conversation: str = "all"
) -> tuple[socket.socket, WatchStream]:
    sock: socket.socket | None = None
    stream: WatchStream | None = None
    try:
        sock = _connect(settings, 10)
        sock.settimeout(None)
        payload = (
            json.dumps(
                {
                    "command": "watch",
                    "conversation": conversation,
                    "token": settings.ipc_token,
                    "language": get_language(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        if os.name == "nt":
            windows_stream = _SocketLineStream(sock)
            stream = windows_stream
            sock.sendall(payload)
            raw = windows_stream.readline(MAX_RESPONSE_BYTES + 1)
        else:
            posix_stream = sock.makefile("rwb")
            stream = posix_stream
            posix_stream.write(payload)
            posix_stream.flush()
            raw = posix_stream.readline(MAX_RESPONSE_BYTES + 1)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise CLIError(tr("client.invalid_response"))
        response = json.loads(raw)
        if not response.get("ok"):
            raise CLIError(str(response.get("error", tr("client.watch_failed"))))
        return sock, stream
    except Exception as exc:
        if stream is not None:
            stream.close()
        if sock is not None:
            sock.close()
        if isinstance(exc, CLIError):
            raise
        if isinstance(exc, OSError):
            raise CLIError(tr("client.cannot_connect")) from exc
        raise


def open_export(settings: Settings) -> tuple[socket.socket, WatchStream]:
    """Opne ein autentisert straum med JSON-linjer frå databasen."""
    sock: socket.socket | None = None
    stream: WatchStream | None = None
    try:
        sock = _connect(settings, 10)
        line_stream = _SocketLineStream(sock)
        stream = line_stream
        payload = (
            json.dumps(
                {
                    "command": "export",
                    "token": settings.ipc_token,
                    "language": get_language(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        sock.sendall(payload)
        raw = line_stream.readline(MAX_RESPONSE_BYTES + 1)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise CLIError(tr("client.invalid_response"))
        response = json.loads(raw)
        if not response.get("ok"):
            raise CLIError(
                str(response.get("error", tr("client.export_failed")))
            )
        sock.settimeout(None)
        return sock, stream
    except Exception as exc:
        if stream is not None:
            stream.close()
        if sock is not None:
            sock.close()
        if isinstance(exc, CLIError):
            raise
        if isinstance(exc, OSError):
            raise CLIError(tr("client.cannot_connect")) from exc
        raise
