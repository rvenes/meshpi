from __future__ import annotations

import json
import socket
from typing import Any, BinaryIO

from meshpi.config import Settings


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
            stream.write(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            stream.flush()
            raw = stream.readline()
    except OSError as exc:
        raise CLIError(
            "Får ikkje kontakt med meshpi-tenesta. "
            "Kontroller at ho køyrer med «systemctl status meshpi»."
        ) from exc
    if not raw:
        raise CLIError("Meshpi-tenesta lukka sambandet utan svar")
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
                {"command": "watch", "conversation": conversation},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        stream.flush()
        response = json.loads(stream.readline())
        if not response.get("ok"):
            raise CLIError(str(response.get("error", "Klarte ikkje starte overvaking")))
        return sock, stream
    except OSError as exc:
        raise CLIError("Får ikkje kontakt med meshpi-tenesta") from exc
