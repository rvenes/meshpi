from __future__ import annotations

import json
import socket
from typing import Any, BinaryIO

from meshpi.config import Settings

MAX_RESPONSE_BYTES = 2_000_000


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
    return socket.create_connection(
        (settings.ipc_host, settings.ipc_port),
        timeout=timeout,
    )


def request(
    settings: Settings, payload: dict[str, Any], timeout: float = 10
) -> dict[str, Any]:
    try:
        sock = _connect(settings, timeout)
    except OSError as exc:
        raise CLIUnavailableError(
            "Får ikkje kontakt med meshpi-tenesta. "
            "Kontroller med «meshpi service status»."
        ) from exc

    try:
        with sock, sock.makefile("rwb") as stream:
            authenticated = payload | {"token": settings.ipc_token}
            stream.write(
                json.dumps(authenticated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            raw = stream.readline(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise CLIError(
            "Sambandet med meshpi-tenesta blei brote før ho svarte. "
            "Tenesta blir ikkje starta på nytt automatisk."
        ) from exc
    if not raw:
        raise CLIError("Meshpi-tenesta lukka sambandet utan svar")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise CLIError("Svaret frå meshpi-tenesta er for stort")
    response = json.loads(raw)
    if not response.get("ok"):
        raise CLIError(str(response.get("error", "Ukjend feil")))
    return response


def open_watch(
    settings: Settings, conversation: str = "all"
) -> tuple[socket.socket, BinaryIO]:
    sock: socket.socket | None = None
    stream: BinaryIO | None = None
    try:
        sock = _connect(settings, 10)
        sock.settimeout(None)
        stream = sock.makefile("rwb")
        stream.write(
            json.dumps(
                {
                    "command": "watch",
                    "conversation": conversation,
                    "token": settings.ipc_token,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        raw = stream.readline(MAX_RESPONSE_BYTES + 1)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise CLIError("Ugyldig svar frå meshpi-tenesta")
        response = json.loads(raw)
        if not response.get("ok"):
            raise CLIError(str(response.get("error", "Klarte ikkje starte overvaking")))
        return sock, stream
    except Exception as exc:
        if stream is not None:
            stream.close()
        if sock is not None:
            sock.close()
        if isinstance(exc, CLIError):
            raise
        if isinstance(exc, OSError):
            raise CLIError("Får ikkje kontakt med meshpi-tenesta") from exc
        raise
