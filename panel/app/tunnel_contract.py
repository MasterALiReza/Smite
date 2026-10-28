"""Authoritative Type x Transport contract for the five tunnel cores.

The UI, the API and the spec builders disagreed about what a "type" and a
"transport" mean (GOST ``grpc``/``tcpmux`` also change the carrier; FRP ``wss``
used to collapse to ``websocket``; Backhaul UDP silently fell back to TCP with
some carriers). This module is the single server-side reference used to validate
new and edited tunnels *before* any node side effect.

It deliberately does not rewrite the stored ``type`` label of existing rows:
builders keep normalizing legacy names for tunnels that are already running, so
an upgrade never changes a live configuration on its own. Only explicit
create/update requests pass through :func:`validate_tunnel_request`.
"""
from typing import Any, Dict, Optional

CONTRACT_VERSION = 1

CORE_TYPES: Dict[str, Dict[str, Any]] = {
    "gost": {"tcp", "udp", "tcp+udp", "grpc", "tcpmux"},
    "rathole": {"tcp", "udp", "tcp+udp"},
    "backhaul": {"tcp", "udp", "tcp+udp"},
    "chisel": {"tcp", "udp", "tcp+udp", "socks5"},
    "frp": {"tcp", "udp", "tcp+udp", "http", "https"},
}

CORE_TRANSPORTS: Dict[str, set] = {
    "gost": {"tcp", "ws", "wss", "mws", "mwss", "quic", "grpc", "kcp", "ssh"},
    "rathole": {"tcp", "noise", "ws", "wss"},
    "backhaul": {"tcp", "tcpmux", "ws", "wsmux", "wss", "wssmux"},
    "chisel": {"ws", "wss"},
    "frp": {"tcp", "kcp", "quic", "websocket", "wss"},
}

DEFAULT_TRANSPORT = {
    "gost": "tcp",
    "rathole": "tcp",
    "backhaul": "tcpmux",
    "chisel": "ws",
    "frp": "tcp",
}

# Legacy spellings accepted on input and mapped to the canonical value.
TRANSPORT_ALIASES: Dict[str, Dict[str, str]] = {
    "gost": {"websocket": "ws", "https": "wss", "tls": "wss", "mux": "mws", "http2": "grpc"},
    "rathole": {"websocket": "ws", "https": "wss", "tls": "wss"},
    "backhaul": {"websocket": "ws", "https": "wss", "tls": "wss", "mux": "tcpmux"},
    "chisel": {"websocket": "ws", "https": "wss", "tls": "wss"},
    "frp": {"ws": "websocket", "https": "wss", "tls": "wss"},
}

# GOST expresses a carrier choice through its Type selector. These composite
# names are accepted, but a second, contradictory carrier is rejected instead of
# being silently overwritten (the old builder behaviour).
GOST_GRPC_COMPATIBLE_TRANSPORTS = {"", "tcp", "grpc"}

BACKHAUL_WEB_CARRIERS = {"ws", "wsmux", "wss", "wssmux"}

_TLS_CARRIERS = {
    "gost": {"wss", "mwss", "quic", "grpc", "ssh"},
    "rathole": {"wss", "noise"},
    "backhaul": {"wss", "wssmux"},
    "chisel": {"wss"},
    "frp": {"wss", "quic"},
}


class TunnelContractError(ValueError):
    """A create/update request asks for a combination the core cannot honour."""


def normalize_transport(core: str, transport: Optional[str]) -> str:
    core_key = (core or "").strip().lower()
    if core_key not in CORE_TRANSPORTS:
        raise TunnelContractError(f"Unsupported tunnel core '{core}'. Supported: {sorted(CORE_TRANSPORTS)}")
    value = (transport or DEFAULT_TRANSPORT[core_key]).strip().lower()
    value = TRANSPORT_ALIASES.get(core_key, {}).get(value, value)
    if value not in CORE_TRANSPORTS[core_key]:
        raise TunnelContractError(
            f"{core_key}: transport '{transport}' is not supported. "
            f"Allowed: {sorted(CORE_TRANSPORTS[core_key])}"
        )
    return value


def validate_tunnel_request(
    core: str,
    tunnel_type: Optional[str],
    transport: Optional[str],
    spec: Optional[Dict[str, Any]] = None,
    security_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate a create/update request and return the effective semantics.

    Returns ``type`` (payload label kept for the UI), ``payload`` (what is
    forwarded), ``transport`` (canonical carrier), ``multiplex`` (explicit
    multiplexing intent), ``tls`` (whether the carrier is TLS by definition) and
    human-readable ``warnings``.
    """
    core_key = (core or "").strip().lower()
    if core_key not in CORE_TYPES:
        raise TunnelContractError(f"Unsupported tunnel core '{core}'. Supported: {sorted(CORE_TYPES)}")

    raw_type = (tunnel_type or "").strip().lower()
    if not raw_type:
        raise TunnelContractError(f"{core_key}: tunnel type is required")
    if raw_type not in CORE_TYPES[core_key]:
        raise TunnelContractError(
            f"{core_key}: type '{tunnel_type}' is not supported. "
            f"Allowed: {sorted(CORE_TYPES[core_key])}"
        )

    carrier = normalize_transport(core_key, transport)
    warnings = []
    multiplex = False
    payload = raw_type

    if core_key == "gost":
        if raw_type == "grpc":
            supplied = (transport or "").strip().lower()
            if supplied not in GOST_GRPC_COMPATIBLE_TRANSPORTS:
                raise TunnelContractError(
                    "gost: Type 'gRPC' already selects the gRPC carrier; "
                    f"transport '{transport}' contradicts it. Choose Type=TCP to keep that carrier."
                )
            payload, carrier = "tcp", "grpc"
        elif raw_type == "tcpmux":
            if carrier == "grpc":
                raise TunnelContractError(
                    "gost: Type 'TCP Mux' and transport 'gRPC' are two different multiplexers; pick one."
                )
            payload, multiplex = "tcp", True
    elif core_key == "backhaul":
        if payload in ("udp", "tcp+udp") and carrier in BACKHAUL_WEB_CARRIERS:
            raise TunnelContractError(
                f"backhaul: UDP over {carrier} is not implemented by upstream Backhaul. "
                "Use transport tcp or tcpmux for UDP (accept_udp) instead."
            )

    tls = carrier in _TLS_CARRIERS.get(core_key, set())
    if security_type:
        security = security_type.strip().lower()
        if core_key == "frp" and carrier == "quic" and security == "none":
            warnings.append("frp: QUIC always uses TLS 1.3; security 'none' is ignored for this carrier")
        if core_key == "frp" and security == "none" and carrier == "wss":
            raise TunnelContractError("frp: WSS is TLS by definition; security 'none' contradicts it")

    return {
        "contract_version": CONTRACT_VERSION,
        "type": raw_type,
        "payload": payload,
        "transport": carrier,
        "multiplex": multiplex,
        "tls": tls,
        "warnings": warnings,
    }
