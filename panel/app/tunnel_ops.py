"""Safe migration of an existing tunnel to other nodes and/or another type.

Editing the Iran/foreign node or the tunnel type of a tunnel that is already
running used to write the new values straight into the live columns. Every
background job (self-healing, auto re-apply, core reset, panel start-up sync)
reads those columns, so the "staged" configuration was pushed without anybody
asking for it, and the node that lost the tunnel purged the still-running
instance on its next reconciliation.

Here the requested values are *staged* in ``Tunnel.pending_changes`` while the
live columns keep describing what really runs on the nodes. They are applied in
one operation (``apply_pending_changes``) that validates everything first, pushes
the new configuration through the normal apply path (which also removes the
tunnel from nodes that no longer own it) and restores the previous
configuration if anything fails.
"""
import asyncio
import copy
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models import Node, Tunnel, utcnow
from app.tunnel_contract import CORE_TYPES

logger = logging.getLogger(__name__)

# Fields that can be staged. Everything else keeps its existing edit semantics.
STAGEABLE_FIELDS = ("type", "iran_node_id", "foreign_node_id")

# Certificates are issued for the address of the node that listens; they are
# useless (and, for verifying clients, harmful) once the nodes change.
TLS_DERIVED_KEYS = (
    "tls_pkcs12_b64",
    "tls_pkcs12_password",
    "tls_ca_cert_pem",
    "tls_cert_pem",
    "tls_key_pem",
)
# A node that left the tunnel still holds these in its saved configuration.
SECRET_KEYS = (
    "token",
    "auth",
    "auth_token",
    "server_private_key",
    "server_public_key",
    "client_private_key",
    "client_public_key",
)
# Derived from the tunnel type by the spec builders / latency planner.
TYPE_DERIVED_KEYS = ("accept_udp", "latency_profile", "tcp_mux")
# Addresses the builders recompute from the node IPs.
ADDRESS_KEYS = ("remote_addr", "server_addr", "latency_ms")

_TUNNEL_LOCKS: Dict[str, asyncio.Lock] = {}


def tunnel_lock(tunnel_id: str) -> asyncio.Lock:
    """One lock per tunnel: user applies and background jobs never overlap on it."""
    lock = _TUNNEL_LOCKS.get(tunnel_id)
    if lock is None:
        lock = _TUNNEL_LOCKS[tunnel_id] = asyncio.Lock()
    return lock


def live_roles(tunnel: Any) -> Tuple[Optional[str], Optional[str]]:
    """(iran_node_id, foreign_node_id) as currently running."""
    return (tunnel.iran_node_id or tunnel.node_id or None), (tunnel.foreign_node_id or None)


def effective_value(tunnel: Any, key: str) -> Any:
    pending = tunnel.pending_changes or {}
    if key in pending:
        return pending[key]
    if key == "iran_node_id":
        return tunnel.iran_node_id or tunnel.node_id or None
    return getattr(tunnel, key, None)


def is_multi_node(tunnel: Any) -> bool:
    """Tunnels that have an Iran and a foreign side (all cores; legacy single-node GOST excluded)."""
    if tunnel.core != "gost":
        return True
    return bool(tunnel.foreign_node_id or tunnel.iran_node_id)


def merge_pending(tunnel: Any, requested: Dict[str, Any]) -> Dict[str, Any]:
    """Merge requested values into the staged set; values equal to the live ones drop out."""
    pending = dict(tunnel.pending_changes or {})
    live_iran, live_foreign = live_roles(tunnel)
    live = {"type": tunnel.type, "iran_node_id": live_iran, "foreign_node_id": live_foreign}
    for key, value in requested.items():
        if key not in STAGEABLE_FIELDS:
            continue
        if value == live.get(key):
            pending.pop(key, None)
        else:
            pending[key] = value
    return pending


async def validate_nodes(db: Any, iran_id: Optional[str], foreign_id: Optional[str]) -> Tuple[Node, Node]:
    """Both sides must exist, have the right role and a reachable address."""
    if not iran_id or not foreign_id:
        raise HTTPException(status_code=400, detail="Both an Iran node and a foreign node are required")
    if iran_id == foreign_id:
        raise HTTPException(status_code=400, detail="The Iran node and the foreign node must be different servers")
    nodes: Dict[str, Optional[Node]] = {}
    for nid in (iran_id, foreign_id):
        res = await db.execute(select(Node).where(Node.id == nid))
        nodes[nid] = res.scalar_one_or_none()
    iran, foreign = nodes[iran_id], nodes[foreign_id]
    if not iran:
        raise HTTPException(status_code=404, detail=f"Iran node {iran_id} not found")
    if not foreign:
        raise HTTPException(status_code=404, detail=f"Foreign node {foreign_id} not found")
    if (iran.node_metadata or {}).get("role") != "iran":
        raise HTTPException(status_code=400, detail=f"Node '{iran.name}' is not an Iran node")
    if (foreign.node_metadata or {}).get("role") != "foreign":
        raise HTTPException(status_code=400, detail=f"Node '{foreign.name}' is not a foreign node")
    for node in (iran, foreign):
        if not (node.node_metadata or {}).get("ip_address"):
            raise HTTPException(status_code=400, detail=f"Node '{node.name}' has no IP address")
    return iran, foreign


def validate_type(core: str, new_type: Optional[str]) -> str:
    value = (new_type or "").strip().lower()
    if value not in CORE_TYPES.get(core, set()):
        raise HTTPException(
            status_code=400,
            detail=f"{core}: type '{new_type}' is not supported. Allowed: {sorted(CORE_TYPES.get(core, set()))}",
        )
    return value


def invalidate_derived_spec(spec: Optional[Dict[str, Any]], *, nodes_changed: bool, type_changed: bool) -> Dict[str, Any]:
    """Drop stored values that were derived from the old nodes / type.

    The spec builders regenerate every one of them on the next apply and the apply
    path persists the result, so removing them is a safe way to re-issue
    certificates for the new addresses and to rotate secrets the old node still holds.
    """
    out = copy.deepcopy(spec) if isinstance(spec, dict) else {}
    keys: List[str] = []
    if nodes_changed:
        keys += list(TLS_DERIVED_KEYS) + list(SECRET_KEYS) + list(ADDRESS_KEYS)
    if type_changed:
        keys += list(TYPE_DERIVED_KEYS)
    for key in keys:
        out.pop(key, None)
    return out


async def describe_pending(db: Any, tunnel: Any) -> Dict[str, Any]:
    """Human-readable summary of what applying the staged changes would do."""
    pending = dict(tunnel.pending_changes or {})
    live_iran, live_foreign = live_roles(tunnel)
    names: Dict[str, str] = {}
    for nid in {live_iran, live_foreign, pending.get("iran_node_id"), pending.get("foreign_node_id")} - {None}:
        res = await db.execute(select(Node).where(Node.id == nid))
        node = res.scalar_one_or_none()
        names[nid] = node.name if node else str(nid)[:8]

    changes: List[Dict[str, Any]] = []
    for key, label in (("iran_node_id", "iran"), ("foreign_node_id", "foreign")):
        if key in pending:
            old = live_iran if key == "iran_node_id" else live_foreign
            changes.append({
                "field": label,
                "from": names.get(old, old),
                "to": names.get(pending[key], pending[key]),
            })
    if "type" in pending:
        changes.append({"field": "type", "from": tunnel.type, "to": pending["type"]})
    nodes_changed = any(k in pending for k in ("iran_node_id", "foreign_node_id"))
    return {
        "changes": changes,
        "nodes_changed": nodes_changed,
        "secrets_rotate": nodes_changed,
        "expected_downtime": "a few seconds" if pending else "none",
    }


async def _node_online(client: Any, node_id: str, timeout: float = 6.0) -> bool:
    try:
        resp = await asyncio.wait_for(client.get_tunnel_status(node_id, ""), timeout=timeout)
    except Exception:
        return False
    return bool(isinstance(resp, dict) and resp.get("status") in ("ok", "success"))


_ROLLBACK_COLUMNS = ("type", "node_id", "iran_node_id", "foreign_node_id", "status", "error_message", "revision")


async def apply_pending_changes(
    db: Any,
    tunnel: Any,
    apply_fn: Callable[[], Awaitable[Any]],
    client: Any,
    check_conflicts: Optional[Callable[..., Awaitable[None]]] = None,
) -> Any:
    """Apply the staged node/type changes; restore the previous configuration on failure.

    ``apply_fn`` is the regular tunnel apply (server first, then client, with the
    fleet-wide purge of nodes that no longer own the tunnel). The caller holds
    ``tunnel_lock(tunnel.id)``.
    """
    from app.mutation_guard import mutation_guard

    pending = dict(tunnel.pending_changes or {})
    if not pending:
        return await apply_fn()
    if mutation_guard.is_engaged():
        raise HTTPException(status_code=423, detail="Fleet mutations are suspended (mutation guard is engaged)")
    if not is_multi_node(tunnel):
        raise HTTPException(status_code=400, detail="Single-node tunnels cannot change servers; recreate the tunnel instead")

    new_iran = effective_value(tunnel, "iran_node_id")
    new_foreign = effective_value(tunnel, "foreign_node_id")
    new_type = validate_type(tunnel.core, effective_value(tunnel, "type"))

    # --- Preflight: nothing is changed before this passes -------------------------
    iran, foreign = await validate_nodes(db, new_iran, new_foreign)
    live_iran, live_foreign = live_roles(tunnel)
    for node in (iran, foreign):
        if node.id not in (live_iran, live_foreign) and not await _node_online(client, node.id):
            raise HTTPException(status_code=409, detail=f"Node '{node.name}' is not reachable; the tunnel was not changed")
    if check_conflicts is not None:
        await check_conflicts(
            db=db,
            spec={**(tunnel.spec or {}), "type": new_type, "tunnel_type": new_type},
            iran_node_id=iran.id,
            foreign_node_id=foreign.id,
            node_id=iran.id,
            exclude_tunnel_id=tunnel.id,
            core=tunnel.core,
            is_reverse=tunnel.is_reverse,
        )

    nodes_changed = (iran.id, foreign.id) != (live_iran, live_foreign)
    type_changed = new_type != tunnel.type

    # --- Snapshot, then switch the live configuration ------------------------------
    snapshot = {col: getattr(tunnel, col) for col in _ROLLBACK_COLUMNS}
    snapshot["spec"] = copy.deepcopy(tunnel.spec) if isinstance(tunnel.spec, dict) else tunnel.spec

    spec = invalidate_derived_spec(tunnel.spec, nodes_changed=nodes_changed, type_changed=type_changed)
    spec["type"] = new_type
    spec["tunnel_type"] = new_type
    tunnel.type = new_type
    tunnel.iran_node_id = iran.id
    tunnel.node_id = iran.id
    tunnel.foreign_node_id = foreign.id
    tunnel.spec = spec
    flag_modified(tunnel, "spec")
    await db.commit()

    try:
        result = await apply_fn()
    except Exception as exc:
        reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
        logger.error(f"Tunnel {tunnel.id}: applying staged changes failed ({reason}); restoring previous configuration")
        for col, value in snapshot.items():
            setattr(tunnel, col, value)
        tunnel.spec = copy.deepcopy(snapshot["spec"]) if isinstance(snapshot["spec"], dict) else snapshot["spec"]
        flag_modified(tunnel, "spec")
        await db.commit()
        restored = True
        try:
            await apply_fn()
        except Exception as restore_exc:
            restored = False
            restore_reason = restore_exc.detail if isinstance(restore_exc, HTTPException) else str(restore_exc)
            logger.error(f"Tunnel {tunnel.id}: restoring previous configuration failed: {restore_reason}")
            tunnel.status = "error"
            tunnel.error_message = f"Change failed ({reason}) and restoring the previous configuration failed: {restore_reason}"
            await db.commit()
        outcome = "the tunnel was restored to its previous configuration" if restored else "restoring the previous configuration also failed"
        raise HTTPException(status_code=409, detail=f"Applying the changes failed: {reason} - {outcome}")

    tunnel.pending_changes = None
    tunnel.revision = (tunnel.revision or 0) + 1
    tunnel.updated_at = utcnow()
    await db.commit()
    logger.info(
        f"Tunnel {tunnel.id} migrated: iran {live_iran}->{iran.id}, foreign {live_foreign}->{foreign.id}, "
        f"type {snapshot['type']}->{new_type}"
    )
    return result
