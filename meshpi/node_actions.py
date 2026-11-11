from __future__ import annotations

import base64
from typing import Any

from meshpi.i18n import tr
from meshpi.models import node_num_to_id

UNKNOWN_SNR = -128

ROUTING_ERRORS = {
    "NO_ROUTE": "backend.routing.no_route",
    "NO_RESPONSE": "backend.routing.no_response",
    "MAX_RETRANSMIT": "backend.routing.max_retransmit",
    "NOT_AUTHORIZED": "backend.routing.not_authorized",
    "PKI_FAILED": "backend.routing.pki_failed",
}


class NodeActionError(RuntimeError):
    """Ei venta feiltilstand frå ei nodehandling."""


def node_info_payload(user: Any, local_node_id: str) -> Any:
    """Copy only public User fields from the active radio's own registry entry."""
    from google.protobuf.json_format import ParseDict, ParseError
    from meshtastic.protobuf import mesh_pb2

    if not isinstance(user, dict) or not user:
        raise NodeActionError(tr("backend.node_info.local_unavailable"))
    fields = {}
    for field_name in (
        "long_name", "short_name", "hw_model", "role", "is_licensed",
        "public_key", "is_unmessagable",
    ):
        field = mesh_pb2.User.DESCRIPTOR.fields_by_name[field_name]
        value = user.get(field.json_name, user.get(field.name))
        if value is not None:
            if field.name == "public_key" and isinstance(value, bytes):
                value = base64.b64encode(value).decode("ascii")
            fields[field.json_name] = value
    try:
        payload = ParseDict(fields, mesh_pb2.User())
    except (ValueError, TypeError, ParseError):
        raise NodeActionError(tr("backend.node_info.local_unavailable")) from None
    payload.id = local_node_id
    if payload.public_key and len(payload.public_key) != 32:
        raise NodeActionError(tr("backend.node_info.local_unavailable"))
    return payload


def parse_node_info_response(packet: dict[str, Any], target_node_id: str) -> dict[str, bool]:
    from google.protobuf.json_format import ParseDict
    from meshtastic.protobuf import mesh_pb2

    decoded = packet.get("decoded")
    if not isinstance(decoded, dict):
        raise NodeActionError(tr("backend.node_info.wrong_response"))
    if decoded.get("portnum") in {"ROUTING_APP", 5}:
        reason = (decoded.get("routing") or {}).get("errorReason", "UKJEND_FEIL")
        raise NodeActionError(tr(
            "backend.error.with_reason",
            message=tr("backend.node_info.request_failed"), reason=reason,
        ))
    try:
        source = node_num_to_id(int(packet["from"])) if "from" in packet else None
    except (TypeError, ValueError):
        raise NodeActionError(tr("backend.node_info.wrong_response")) from None
    if source is None:
        source = str(packet.get("fromId", "")).lower()
    if decoded.get("portnum") not in {"NODEINFO_APP", 4} or source != target_node_id:
        raise NodeActionError(tr("backend.node_info.wrong_response"))
    try:
        user = mesh_pb2.User()
        payload = decoded.get("payload")
        if isinstance(payload, bytes):
            user.ParseFromString(payload)
        elif isinstance(decoded.get("user"), dict):
            # The SDK also supplies a raw protobuf; never copy it to action history.
            values = {k: v for k, v in decoded["user"].items() if k != "raw"}
            ParseDict(values, user, ignore_unknown_fields=True)
        else:
            raise ValueError
        if (not user.ListFields() or (user.id and user.id.lower() != target_node_id)
                or (user.public_key and len(user.public_key) != 32)):
            raise ValueError
    except Exception:
        raise NodeActionError(tr("backend.node_info.wrong_response")) from None
    return {"node_info_received": True, "public_key_received": len(user.public_key) == 32}


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
        raise NodeActionError(tr("backend.traceroute.missing_decoded"))

    portnum = decoded.get("portnum")
    if portnum in {"ROUTING_APP", 5}:
        routing = decoded.get("routing")
        reason = routing.get("errorReason") if isinstance(routing, dict) else None
        reason = str(reason or "UKJEND_FEIL")
        if reason == "NONE":
            raise NodeActionError(tr("backend.traceroute.ack_without_result"))
        message = tr(ROUTING_ERRORS.get(reason, "backend.traceroute.failed"))
        raise NodeActionError(
            tr("backend.error.with_reason", message=message, reason=reason)
        )

    if portnum not in {"TRACEROUTE_APP", 70}:
        raise NodeActionError(tr("backend.traceroute.wrong_response"))
    route = decoded.get("traceroute")
    if not isinstance(route, dict):
        raise NodeActionError(tr("backend.traceroute.missing_route"))

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
