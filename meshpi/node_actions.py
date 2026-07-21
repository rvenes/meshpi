from __future__ import annotations

from typing import Any

from meshpi.models import node_num_to_id

UNKNOWN_SNR = -128

ROUTING_ERRORS = {
    "NO_ROUTE": "Noden har inga kjend rute til målet",
    "NO_RESPONSE": "Målnoden svara ikkje",
    "MAX_RETRANSMIT": "Sendinga nådde grensa for nye forsøk",
    "NOT_AUTHORIZED": "Noden avviste førespurnaden",
    "PKI_FAILED": "Krypteringa mot målnoden feila",
}


class NodeActionError(RuntimeError):
    """Ei venta feiltilstand frå ei nodehandling."""


def _node_ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for value in values:
        try:
            node_id = node_num_to_id(int(value))
        except (TypeError, ValueError):
            continue
        if node_id is not None:
            result.append(node_id)
    return result


def _snr_values(values: Any, expected: int) -> list[float | None]:
    if not isinstance(values, (list, tuple)) or len(values) != expected:
        return [None] * expected
    result: list[float | None] = []
    for value in values:
        try:
            scaled = int(value)
        except (TypeError, ValueError):
            result.append(None)
            continue
        result.append(None if scaled == UNKNOWN_SNR else scaled / 4)
    return result


def _path(
    start: str,
    intermediate: list[str],
    end: str,
    snr_values: Any,
) -> list[dict[str, Any]]:
    nodes = [start, *intermediate, end]
    snr = _snr_values(snr_values, len(nodes) - 1)
    return [
        {"node_id": node_id, "snr": None if index == 0 else snr[index - 1]}
        for index, node_id in enumerate(nodes)
    ]


def parse_traceroute_response(
    packet: dict[str, Any],
    *,
    local_node_id: str,
    target_node_id: str,
) -> dict[str, Any]:
    """Gjer eit Meshtastic traceroute-svar om til JSON-trygge rutedata."""
    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        raise NodeActionError("Traceroute-svaret manglar dekoda data")

    portnum = decoded.get("portnum")
    if portnum in {"ROUTING_APP", 5}:
        routing = decoded.get("routing")
        reason = routing.get("errorReason") if isinstance(routing, dict) else None
        reason = str(reason or "UKJEND_FEIL")
        if reason == "NONE":
            raise NodeActionError("Mottok stadfesting, men ikkje traceroute-resultat")
        message = ROUTING_ERRORS.get(reason, "Traceroute feila")
        raise NodeActionError(f"{message} ({reason})")

    if portnum not in {"TRACEROUTE_APP", 70}:
        raise NodeActionError("Mottok feil svartype for traceroute")
    route = decoded.get("traceroute")
    if not isinstance(route, dict):
        raise NodeActionError("Traceroute-svaret manglar rutedata")

    forward_hops = _node_ids(route.get("route"))
    result: dict[str, Any] = {
        "forward": _path(
            local_node_id,
            forward_hops,
            target_node_id,
            route.get("snrTowards", route.get("snr_towards")),
        ),
        "return": None,
    }
    route_back_value = route.get("routeBack", route.get("route_back"))
    snr_back_value = route.get("snrBack", route.get("snr_back"))
    if isinstance(route_back_value, (list, tuple)) or isinstance(
        snr_back_value, (list, tuple)
    ):
        result["return"] = _path(
            target_node_id,
            _node_ids(route_back_value),
            local_node_id,
            snr_back_value,
        )
    return result
