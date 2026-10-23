"""Panel-side planning steps that run right before a tunnel's node specs are built.

Only used on *explicit* operations (create / manual re-apply), never by background
re-applies, so they can never restart a running tunnel on their own.
"""
import hashlib
import logging
from typing import Any, Iterable, Optional, Set

from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from app.models import Tunnel

logger = logging.getLogger(__name__)

# Same deterministic ranges the spec builders use for auto-assigned control ports
_CONTROL_PORT_RANGES = {
    "frp": (7000, 1000),
}
_DEFAULT_CONTROL_RANGE = (25000, 25000)

LATENCY_PROFILE_KEY = "latency_profile"
GAMING_PROFILE = "gaming-v1"


def _current_control_port(tunnel: Any) -> Optional[int]:
    spec = tunnel.spec or {}
    keys = ("bind_port", "control_port") if tunnel.core == "frp" else ("control_port",)
    for key in keys:
        raw = spec.get(key)
        try:
            value = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        if value is None:
            continue
        if tunnel.core == "gost" and value == 44300:  # legacy placeholder, re-allocated by the builder
            return None
        if 1024 <= value <= 65535:
            return value
    return None


async def _ports_used_on_nodes(db: Any, tunnel: Any, node_ids: Iterable[str]) -> Set[int]:
    from app.routers.tunnels import extract_all_tunnel_ports

    ids = [n for n in node_ids if n]
    used: Set[int] = set()
    if not ids:
        return used
    result = await db.execute(
        select(Tunnel).where(
            or_(Tunnel.node_id.in_(ids), Tunnel.iran_node_id.in_(ids), Tunnel.foreign_node_id.in_(ids)),
            Tunnel.id != tunnel.id,
        )
    )
    for other in result.scalars().all():
        used |= extract_all_tunnel_ports(other.spec or {}).get("all_ports", set())
    return used


async def ensure_conflict_free_control_port(db: Any, tunnel: Any, node_ids: Iterable[str]) -> Optional[int]:
    """Give a tunnel without an explicit control port one that no other tunnel on its nodes uses.

    The spec builders derive the port from a hash of the tunnel id without looking at
    other tunnels; with FRP's 1000-port range a collision is likely once a node has a
    few dozen tunnels, and the new tunnel then silently fails to bind. Existing
    control ports are never changed (that would break running tunnels).
    """
    if _current_control_port(tunnel) is not None:
        return None
    from app.routers.tunnels import extract_all_tunnel_ports

    base, span = _CONTROL_PORT_RANGES.get(tunnel.core, _DEFAULT_CONTROL_RANGE)
    used = await _ports_used_on_nodes(db, tunnel, node_ids)
    used |= extract_all_tunnel_ports(tunnel.spec or {}).get("service_ports", set())
    start = int(hashlib.sha256(str(tunnel.id).encode()).hexdigest()[:8], 16) % span
    for offset in range(span):
        candidate = base + (start + offset) % span
        if candidate not in used:
            spec = dict(tunnel.spec or {})
            spec["control_port"] = candidate
            if tunnel.core == "frp":
                spec["bind_port"] = candidate
            tunnel.spec = spec
            flag_modified(tunnel, "spec")
            logger.info(f"Tunnel {tunnel.id}: allocated conflict-free control port {candidate}")
            return candidate
    logger.warning(f"Tunnel {tunnel.id}: no free control port in {base}-{base + span - 1}; keeping builder default")
    return None


def is_latency_sensitive(tunnel: Any) -> bool:
    """Only activate gaming-v1 profile if gaming_mode is explicitly enabled."""
    if isinstance(tunnel, dict):
        return bool(tunnel.get("gaming_mode") or (tunnel.get("spec") or {}).get("gaming_mode"))
    return bool(
        getattr(tunnel, "gaming_mode", False)
        or (getattr(tunnel, "spec", None) or {}).get("gaming_mode", False)
    )


async def apply_latency_profile(tunnel: Any, client: Any, node_ids: Iterable[str]) -> bool:
    """Mark latency-sensitive tunnels with the low-latency profile.

    Spec builders turn the marker into per-core tuning. Options that both ends must
    agree on (FRP tcpMux) are only enabled when every node of the tunnel runs an
    agent that understands them, otherwise one side would ignore the option and the
    tunnel would not connect.
    """
    spec = dict(getattr(tunnel, "spec", None) or {})
    changed = False
    if not is_latency_sensitive(tunnel):
        if spec.get(LATENCY_PROFILE_KEY) is not None:
            spec.pop(LATENCY_PROFILE_KEY, None)
            changed = True
        if changed:
            tunnel.spec = spec
            flag_modified(tunnel, "spec")
        return changed
    if spec.get(LATENCY_PROFILE_KEY) != GAMING_PROFILE:
        spec[LATENCY_PROFILE_KEY] = GAMING_PROFILE
        changed = True
    if getattr(tunnel, "core", None) == "frp" and "tcp_mux" not in spec:
        try:
            if await client.nodes_support("frp_tcp_mux_option", *list(node_ids)):
                spec["tcp_mux"] = False
                changed = True
        except Exception as e:
            logger.debug(f"Capability check failed for tunnel {getattr(tunnel, 'id', '')}: {e}")
    if changed:
        tunnel.spec = spec
        flag_modified(tunnel, "spec")
    return changed


async def prepare_tunnel_for_apply(db: Any, tunnel: Any, client: Any, node_ids: Iterable[str]) -> None:
    ids = [n for n in node_ids if n]
    await ensure_conflict_free_control_port(db, tunnel, ids)
    await apply_latency_profile(tunnel, client, ids)
