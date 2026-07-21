from __future__ import annotations

import json
import socket
from typing import Any, BinaryIO

from meshpi.config import Settings

MAX_RESPONSE_BYTES = 2_000_000


class CLIError(RuntimeError):
    pass


def request(
    settings: Settings, payload: dict[str, Any], timeout: float = 10
) -> dict[str, Any]:
    try:
        with socket.create_connection(
            (settings.ipc_host, settings.ipc_port), timeout=timeout
        ) as sock:
            stream = sock.makefile("rwb")
            authenticated = payload | {"token": settings.ipc_token}
            stream.write(
                json.dumps(authenticated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            raw = stream.readline(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise CLIError(
            "Får ikkje kontakt med meshpi-tenesta. "
            "Kontroller at ho køyrer med «systemctl status meshpi»."
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
    try:
        sock = socket.create_connection((settings.ipc_host, settings.ipc_port), timeout=10)
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
    except OSError as exc:
        raise CLIError("Får ikkje kontakt med meshpi-tenesta") from exc
