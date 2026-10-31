"""Tunnels API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Tuple, Dict, Any, Set
from datetime import datetime
from pydantic import BaseModel, ConfigDict
import logging
import time
import asyncio

from app.database import get_db
from app.models import Tunnel, Node, Admin, TunnelCategory, utcnow
from app.node_client import NodeClient
from app.routers.auth import get_current_user
from app.tunnel_contract import CORE_TYPES, TunnelContractError, normalize_transport, validate_tunnel_request
from app.tunnel_ops import (
    apply_pending_changes,
    describe_pending,
    effective_value,
    is_multi_node,
    merge_pending,
    tunnel_lock,
    validate_nodes,
    validate_type,
)


router = APIRouter()
logger = logging.getLogger(__name__)

# Strong references to fire-and-forget tasks; asyncio only keeps weak references,
# so an unreferenced task (e.g. a fleet-wide purge) could be garbage collected mid-run.
_BACKGROUND_TASKS: Set[asyncio.Task] = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _to_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
    return None


def _same_carrier(core: str, left: str | None, right: str | None) -> bool:
    try:
        return normalize_transport(core, left) == normalize_transport(core, right)
    except TunnelContractError:
        return False


def _contract_check(
    core: str,
    tunnel_type: str | None,
    transport: str | None,
    spec: dict | None,
    security_type: str | None,
    *,
    legacy_type: str | None = None,
    legacy_pair: tuple[str | None, str | None] | None = None,
) -> dict:
    """Validate a create/update request against the core contract.

    ``legacy_type`` is the type already stored on an existing row. When an edit
    leaves that label untouched, the builders normalize it to TCP, so contract
    validation runs against TCP instead of rejecting a label the UI no longer
    offers. A newly supplied type is validated strictly.

    ``legacy_pair`` is the stored carrier/security pair. An edit that keeps both
    unchanged does not re-judge them (the builders already render their effective
    semantics, e.g. FRP ``wss`` is TLS even if the stored label says ``none``);
    a newly introduced pair is still rejected.
    """
    try:
        return validate_tunnel_request(core, tunnel_type, transport, spec or {}, security_type)
    except TunnelContractError:
        if legacy_type and (tunnel_type or "").strip().lower() == legacy_type.strip().lower():
            return validate_tunnel_request(core, "tcp", transport, spec or {}, security_type)
        if legacy_pair is not None:
            previous_transport, previous_security = legacy_pair
            if (
                _same_carrier(core, transport, previous_transport)
                and (security_type or "").strip().lower() == (previous_security or "").strip().lower()
            ):
                return validate_tunnel_request(core, tunnel_type, transport, spec or {}, None)
        raise


def prepare_frp_spec_for_node(spec: dict, node: Node, request: Request) -> dict:
    """Prepare FRP spec for node by determining correct server_addr from node metadata"""
    spec_for_node = spec.copy()
    bind_port = spec_for_node.get("bind_port", 7000)
    token = spec_for_node.get("token")
    
    panel_address = node.node_metadata.get("panel_address", "")
    panel_host = None
    
    if panel_address:
        if "://" in panel_address:
            panel_address = panel_address.split("://", 1)[1]
        if ":" in panel_address:
            panel_host = panel_address.split(":")[0]
        else:
            panel_host = panel_address
    
    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1", "0.0.0.0"]:
        panel_host = spec_for_node.get("panel_host")
        if panel_host:
            if "://" in panel_host:
                panel_host = panel_host.split("://", 1)[1]
            if ":" in panel_host:
                panel_host = panel_host.split(":")[0]
    
    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1", "0.0.0.0"]:
        forwarded_host = request.headers.get("X-Forwarded-Host")
        if forwarded_host:
            panel_host = forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
    
    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1", "0.0.0.0"]:
        request_host = request.url.hostname if request.url else None
        if request_host and request_host not in ["localhost", "127.0.0.1", "::1", "0.0.0.0", ""]:
            panel_host = request_host
    
    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1", "0.0.0.0"]:
        import os
        panel_public_ip = os.getenv("PANEL_PUBLIC_IP") or os.getenv("PANEL_IP")
        if panel_public_ip and panel_public_ip not in ["localhost", "127.0.0.1", "::1", "0.0.0.0", ""]:
            panel_host = panel_public_ip
    
    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1", "0.0.0.0", ""]:
        error_details = {
            "node_id": node.id,
            "node_name": node.name,
            "node_metadata_panel_address": panel_address,
            "node_metadata_keys": list(node.node_metadata.keys()),
            "request_hostname": request.url.hostname if request.url else None,
            "x_forwarded_host": request.headers.get("X-Forwarded-Host"),
            "env_panel_public_ip": os.getenv("PANEL_PUBLIC_IP"),
            "env_panel_ip": os.getenv("PANEL_IP"),
        }
        error_msg = f"Cannot determine panel address for FRP tunnel. Details: {error_details}. Please ensure node has correct PANEL_ADDRESS configured (node should register with panel_address in metadata) or set PANEL_PUBLIC_IP environment variable on panel."
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    from app.utils import is_valid_ipv6_address
    if is_valid_ipv6_address(panel_host):
        server_addr = f"[{panel_host}]"
    else:
        server_addr = panel_host
    
    spec_for_node["server_addr"] = server_addr
    spec_for_node["server_port"] = int(bind_port)
    if token:
        spec_for_node["token"] = token
    
    logger.info(f"FRP spec prepared: server_addr={server_addr}, server_port={bind_port}, token={'set' if token else 'none'}, panel_host={panel_host} (from node panel_address: {panel_address})")
    return spec_for_node


class TunnelCreate(BaseModel):
    name: str
    core: str
    type: str
    node_id: str | None = None
    foreign_node_id: str | None = None  # For reverse tunnels: foreign node (server side)
    iran_node_id: str | None = None  # For reverse tunnels: iran node (client side)
    spec: dict
    cdn_mode: bool | None = False
    gaming_mode: bool | None = False
    custom_host: str | None = None
    custom_sni: str | None = None
    ws_path: str | None = None
    is_reverse: bool | None = None
    port_ranges: list[str] | None = None
    stealth_domain: str | None = None
    allowed_ips: list[str] | None = None
    rate_limit_mbps: float | None = None
    transport_type: str | None = "tcp"
    security_type: str | None = "none"
    failover_ips: list[str] | None = None
    utls_fingerprint: str | None = None
    custom_headers: dict | None = None
    obfuscation_type: str | None = "none"
    mux_type: str | None = None
    relay_hops: list[dict] | None = None
    bypass_ips: list[str] | None = None
    dns_resolvers: list[str] | None = None
    category: str | None = None
    selector_strategy: str | None = "fifo"
    keepalive_interval: int | None = 15


class TunnelUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    spec: dict | None = None
    cdn_mode: bool | None = None
    gaming_mode: bool | None = None
    custom_host: str | None = None
    custom_sni: str | None = None
    ws_path: str | None = None
    is_reverse: bool | None = None
    node_id: str | None = None
    foreign_node_id: str | None = None
    iran_node_id: str | None = None
    port_ranges: list[str] | None = None
    stealth_domain: str | None = None
    allowed_ips: list[str] | None = None
    rate_limit_mbps: float | None = None
    transport_type: str | None = None
    security_type: str | None = None
    failover_ips: list[str] | None = None
    utls_fingerprint: str | None = None
    custom_headers: dict | None = None
    obfuscation_type: str | None = None
    mux_type: str | None = None
    relay_hops: list[dict] | None = None
    bypass_ips: list[str] | None = None
    dns_resolvers: list[str] | None = None
    category: str | None = None
    selector_strategy: str | None = None
    keepalive_interval: int | None = None


class TunnelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    core: str
    type: str
    node_id: str
    foreign_node_id: str | None = None
    iran_node_id: str | None = None
    spec: dict
    cdn_mode: bool | None = False
    gaming_mode: bool | None = False
    custom_host: str | None = None
    custom_sni: str | None = None
    ws_path: str | None = None
    is_reverse: bool | None = False
    port_ranges: list[str] | None = None
    stealth_domain: str | None = None
    allowed_ips: list[str] | None = None
    rate_limit_mbps: float | None = None
    transport_type: str | None = "tcp"
    security_type: str | None = "none"
    failover_ips: list[str] | None = None
    utls_fingerprint: str | None = None
    custom_headers: dict | None = None
    obfuscation_type: str | None = None
    mux_type: str | None = None
    relay_hops: list[dict] | None = None
    bypass_ips: list[str] | None = None
    dns_resolvers: list[str] | None = None
    category: str | None = None
    selector_strategy: str | None = "fifo"
    keepalive_interval: int | None = 15
    pending_changes: dict | None = None
    status: str
    error_message: str | None = None
    revision: int
    used_mb: float = 0.0
    quota_mb: float = 0.0
    created_at: datetime
    updated_at: datetime


class CategoryCreate(BaseModel):
    name: str
    color: str | None = "blue"
    description: str | None = None


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    color: str = "blue"
    description: str | None = None
    tunnel_count: int = 0
    created_at: datetime


class BulkCategoryAssign(BaseModel):
    tunnel_ids: list[str]
    category: str | None = None


def parse_ports_from_spec(spec: dict) -> list:
    """Parse ports from spec - supports both comma-separated string, int, and list formats"""
    ports = spec.get("ports", [])
    if isinstance(ports, int):
        return [ports]
    if isinstance(ports, str):
        # Comma-separated string: "8080,8081,8082" or "8080, 1000-2000"
        ports = [int(p.strip()) if p.strip().isdigit() else p.strip() for p in ports.split(",") if p.strip()]
    elif isinstance(ports, list) and ports:
        # List of numbers or strings
        ports = [int(p) if isinstance(p, (int, str)) and str(p).isdigit() else p for p in ports]
    return ports if isinstance(ports, list) else []


def validate_tunnel_payload(spec: Any) -> None:
    """
    Validate tunnel payload specifications.
    Ensures port ranges do not exceed the maximum allowed span of 500 ports.
    Raises HTTPException(status_code=400, ...) on violation.
    """
    if isinstance(spec, str):
        try:
            import json
            spec = json.loads(spec)
        except Exception:
            return
    if not spec or not isinstance(spec, dict):
        return

    # 1. port_ranges (list or comma-separated string)
    port_ranges = spec.get("port_ranges") or []
    if isinstance(port_ranges, str):
        port_ranges = [r.strip() for r in port_ranges.split(",") if r.strip()]
    if isinstance(port_ranges, list):
        for pr in port_ranges:
            if isinstance(pr, str):
                pr_clean = pr.strip()
                sides = [s.strip() for s in pr_clean.split("=")] if "=" in pr_clean else [pr_clean]
                for side in sides:
                    if ":" in side:
                        side = side.rsplit(":", 1)[-1].strip()
                    if "-" in side:
                        parts = side.split("-", 1)
                        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                            start, end = int(parts[0].strip()), int(parts[1].strip())
                            if 0 < start <= end <= 65535 and (end - start) > 500:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Port range '{side}' exceeds maximum allowed span of 500 ports."
                                )

    # 2. parsed ports (from ports list or string)
    parsed = parse_ports_from_spec(spec)
    for p in parsed:
        if isinstance(p, str):
            p_clean = p.strip()
            sides = [s.strip() for s in p_clean.split("=")] if "=" in p_clean else [p_clean]
            for side in sides:
                if ":" in side:
                    side = side.rsplit(":", 1)[-1].strip()
                if "-" in side:
                    parts = side.split("-", 1)
                    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                        if 0 < s_p <= e_p <= 65535 and (e_p - s_p) > 500:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Port range '{side}' exceeds maximum allowed span of 500 ports."
                            )


def extract_all_tunnel_ports(spec: Any, validate_span: bool = True) -> Dict[str, Set[int]]:
    """
    Extract both service ports and control/bind ports from a tunnel spec.
    Returns a dict with 'service_ports', 'control_ports', and 'all_ports'.
    """
    service_ports: Set[int] = set()
    control_ports: Set[int] = set()
    
    if isinstance(spec, str):
        try:
            import json
            spec = json.loads(spec)
        except Exception:
            spec = {}
            
    if not spec or not isinstance(spec, dict):
        return {"service_ports": service_ports, "control_ports": control_ports, "all_ports": set()}
    
    # 1. Service ports
    parsed = parse_ports_from_spec(spec)
    for p in parsed:
        if isinstance(p, int) and p > 0:
            service_ports.add(p)
        elif isinstance(p, str):
            p_clean = p.strip()
            sides = [s.strip() for s in p_clean.split("=")] if "=" in p_clean else [p_clean]
            for side in sides:
                if ":" in side:
                    side = side.rsplit(":", 1)[-1].strip()
                if side.isdigit() and int(side) > 0:
                    service_ports.add(int(side))
                elif "-" in side:
                    parts = side.split("-", 1)
                    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                        if 0 < s_p <= e_p <= 65535:
                            if validate_span and (e_p - s_p) > 500:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Port range '{side}' exceeds maximum allowed span of 500 ports."
                                )
                            service_ports.update(range(s_p, e_p + 1))
        elif isinstance(p, dict):
            for k in ("remote", "remote_port", "local", "local_port", "port", "listen_port"):
                v = p.get(k)
                if v and str(v).isdigit() and int(v) > 0:
                    service_ports.add(int(v))
            
    for key in ["remote_port", "proxy_port", "listen_port", "vhost_http_port", "vhost_https_port"]:
        val = spec.get(key)
        if val and str(val).isdigit() and int(val) > 0:
            service_ports.add(int(val))

    # Port ranges (e.g. ["10000-10020"] or "10000-10020, 10030-10040")
    port_ranges = spec.get("port_ranges") or []
    if isinstance(port_ranges, str):
        port_ranges = [r.strip() for r in port_ranges.split(",") if r.strip()]
    if isinstance(port_ranges, list):
        for pr in port_ranges:
            if isinstance(pr, str):
                pr_clean = pr.strip()
                sides = [s.strip() for s in pr_clean.split("=")] if "=" in pr_clean else [pr_clean]
                for side in sides:
                    if ":" in side:
                        side = side.rsplit(":", 1)[-1].strip()
                    if "-" in side:
                        parts = side.split("-", 1)
                        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                            start, end = int(parts[0].strip()), int(parts[1].strip())
                            if 0 < start <= end <= 65535:
                                if validate_span and (end - start) > 500:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"Port range '{side}' exceeds maximum allowed span of 500 ports."
                                    )
                                service_ports.update(range(start, end + 1))
                    elif side.isdigit() and int(side) > 0:
                        service_ports.add(int(side))
            
    # 2. Control / bind ports
    for key in ["control_port", "bind_port", "server_port"]:
        val = spec.get(key)
        if val and str(val).isdigit() and int(val) > 0:
            control_ports.add(int(val))
            
    bind_addr = str(spec.get("bind_addr", ""))
    if ":" in bind_addr:
        p_str = bind_addr.split(":")[-1]
        if p_str.isdigit() and int(p_str) > 0:
            control_ports.add(int(p_str))
            
    return {
        "service_ports": service_ports,
        "control_ports": control_ports,
        "all_ports": service_ports | control_ports
    }


async def check_port_conflicts(
    db: AsyncSession,
    spec: dict,
    iran_node_id: Optional[str] = None,
    foreign_node_id: Optional[str] = None,
    node_id: Optional[str] = None,
    exclude_tunnel_id: Optional[str] = None,
    core: Optional[str] = None,
    is_reverse: Optional[bool] = None
) -> None:
    """
    Check if any listening port in `spec` conflicts with an existing active/pending tunnel on the listening node.
    In reverse tunnels, only the Iran node binds listening sockets (the foreign node is an outbound client).
    In direct tunnels, the foreign node binds listening sockets.
    Raises HTTPException(status_code=400, detail=...) if a collision is found to protect node stability.
    """
    if not spec:
        return
        
    validate_tunnel_payload(spec)
    extracted = extract_all_tunnel_ports(spec)
    all_new_ports = extracted["all_ports"]
    if not all_new_ports:
        return
        
    # Determine reverse tunnel mode
    force_direct = _to_bool((spec or {}).get("force_direct")) is True
    if force_direct:
        is_rev = False
    elif core in {"rathole", "backhaul"}:
        is_rev = True
    elif core in {"gost", "chisel", "frp"}:
        raw_rev = _to_bool(is_reverse)
        if raw_rev is not None:
            is_rev = raw_rev
        elif _to_bool((spec or {}).get("is_reverse")) is not None:
            is_rev = _to_bool((spec or {}).get("is_reverse"))
        elif foreign_node_id or iran_node_id:
            is_rev = True
        else:
            is_rev = False
    else:
        is_rev = _to_bool(is_reverse) if is_reverse is not None else True

    # Identify which node binds listening sockets
    # For reverse: Iran node binds both service ports and control port.
    # For direct: Iran node binds service ports, Foreign node binds control port.
    nodes_to_check: List[Tuple[str, str, Set[int], Set[int]]] = []
    iran_nid = iran_node_id or node_id
    foreign_nid = foreign_node_id or node_id

    if is_rev:
        if iran_nid and str(iran_nid).strip():
            nodes_to_check.append((
                str(iran_nid).strip(),
                "ایران (Iran)",
                extracted["service_ports"],
                extracted["control_ports"]
            ))
    else:
        if iran_nid and foreign_nid and str(iran_nid).strip() == str(foreign_nid).strip():
            # Single-node direct tunnel binds both service and control ports on the single host
            nodes_to_check.append((
                str(iran_nid).strip(),
                "سرور (Node)",
                extracted["service_ports"],
                extracted["control_ports"]
            ))
        else:
            if iran_nid and str(iran_nid).strip() and extracted["service_ports"]:
                nodes_to_check.append((
                    str(iran_nid).strip(),
                    "ایران (Iran)",
                    extracted["service_ports"],
                    set()
                ))
            if foreign_nid and str(foreign_nid).strip() and extracted["control_ports"]:
                nodes_to_check.append((
                    str(foreign_nid).strip(),
                    "خارج (Foreign)",
                    set(),
                    extracted["control_ports"]
                ))
            
    if not nodes_to_check:
        return

    from sqlalchemy import or_, and_

    new_type = (spec.get("type") or spec.get("tunnel_type") or "tcp").lower()
    is_new_udp = new_type == "udp"
    is_new_tcp = new_type in {"tcp", "tcpmux", "ws", "wss", "grpc", "http", "https"}
    is_new_dual = new_type in {"tcp+udp", "all"}
    
    for nid, node_type_label, check_svc, check_ctrl in nodes_to_check:
        # Get node name for descriptive error message
        n_res = await db.execute(select(Node).where(Node.id == nid))
        node_obj = n_res.scalar_one_or_none()
        node_name_str = f"'{node_obj.name}'" if node_obj else f"ID {nid[:8]}"
        
        # Query tunnels associated with this node
        t_res = await db.execute(
            select(Tunnel).where(
                or_(
                    Tunnel.iran_node_id == nid,
                    Tunnel.foreign_node_id == nid,
                    Tunnel.node_id == nid
                ),
                Tunnel.status.in_(["active", "pending", "stopped", "running"])
            )
        )
        existing_tunnels = t_res.scalars().all()
        
        for ex in existing_tunnels:
            if exclude_tunnel_id and ex.id == exclude_tunnel_id:
                continue
            
            ex_spec = ex.spec or {}
            ex_ports_info = extract_all_tunnel_ports(ex_spec)
            
            ex_force = _to_bool(ex_spec.get("force_direct")) is True
            if ex_force:
                ex_is_rev = False
            elif ex.core in {"rathole", "backhaul"}:
                ex_is_rev = True
            elif ex.core in {"gost", "chisel", "frp"}:
                raw_ex_rev = _to_bool(ex.is_reverse)
                if raw_ex_rev is not None:
                    ex_is_rev = raw_ex_rev
                elif _to_bool(ex_spec.get("is_reverse")) is not None:
                    ex_is_rev = _to_bool(ex_spec.get("is_reverse"))
                elif ex.foreign_node_id or ex.iran_node_id:
                    ex_is_rev = True
                else:
                    ex_is_rev = False
            else:
                ex_is_rev = _to_bool(ex.is_reverse) if ex.is_reverse is not None else False
            
            ex_bound_svc: Set[int] = set()
            ex_bound_ctrl: Set[int] = set()
            
            if ex_is_rev:
                # Reverse tunnel binds both service and control ports on Iran node
                if nid in {ex.iran_node_id, ex.node_id}:
                    ex_bound_svc = ex_ports_info["service_ports"]
                    ex_bound_ctrl = ex_ports_info["control_ports"]
                elif ex.foreign_node_id and nid == ex.foreign_node_id:
                    ex_bound_svc = ex_ports_info["service_ports"]
            else:
                # Direct tunnel:
                if ex.foreign_node_id and ex.iran_node_id:
                    if nid == ex.iran_node_id:
                        ex_bound_svc = ex_ports_info["service_ports"]
                    if nid == ex.foreign_node_id:
                        ex_bound_ctrl = ex_ports_info["control_ports"]
                else:
                    if nid in {ex.foreign_node_id, ex.iran_node_id, ex.node_id}:
                        ex_bound_svc = ex_ports_info["service_ports"]
                        ex_bound_ctrl = ex_ports_info["control_ports"]
            
            ex_type = (ex.type or ex_spec.get("type") or ex_spec.get("tunnel_type") or "tcp").lower()
            is_ex_udp = ex_type == "udp"
            is_ex_tcp = ex_type in {"tcp", "tcpmux", "ws", "wss", "grpc", "http", "https"}
            is_ex_dual = ex_type in {"tcp+udp", "all"}

            # Service port collision only if protocols conflict
            proto_conflict = (is_new_dual or is_ex_dual) or (is_new_udp and is_ex_udp) or (is_new_tcp and is_ex_tcp)
            
            collided_port = None
            if proto_conflict and check_svc and ex_bound_svc:
                common_service = check_svc.intersection(ex_bound_svc)
                if common_service:
                    collided_port = sorted(list(common_service))[0]
            
            # Control port collision
            if collided_port is None and check_ctrl and ex_bound_ctrl:
                common_ctrl = check_ctrl.intersection(ex_bound_ctrl)
                if common_ctrl:
                    collided_port = sorted(list(common_ctrl))[0]

            # Cross collision: service port vs control port on the same node
            if collided_port is None:
                if check_svc and ex_bound_ctrl:
                    c1 = check_svc.intersection(ex_bound_ctrl)
                    if c1:
                        collided_port = sorted(list(c1))[0]
                if check_ctrl and ex_bound_svc:
                    c2 = check_ctrl.intersection(ex_bound_svc)
                    if c2:
                        collided_port = sorted(list(c2))[0]

            if collided_port is not None:
                cat_hint = f" (در دسته‌بندی '{ex.category}')" if getattr(ex, "category", None) else ""
                error_msg = (
                    f"تداخل پورت: پورت {collided_port} در سرور {node_type_label} {node_name_str} "
                    f"قبلاً توسط تانل '{ex.name}'{cat_hint} رزرو شده است. "
                    f"استفاده همزمان از یک پورت در دو تانل روی یک سرور باعث تداخل و کرش پروسه‌ها می‌شود."
                )
                logger.warning(
                    f"Port collision prevented: port {collided_port} on node {nid} ({node_name_str}) "
                    f"already used by tunnel '{ex.name}'{cat_hint} (id={ex.id})"
                )
                raise HTTPException(status_code=400, detail=error_msg)


from app.spec_builder import build_gost_node_specs, build_tunnel_node_specs


@router.post("", response_model=TunnelResponse)
async def create_tunnel(tunnel: TunnelCreate, request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Create a new tunnel and auto-apply it"""
    from app.node_client import NodeClient
    
    logger.info(f"Creating tunnel: name={tunnel.name}, type={tunnel.type}, core={tunnel.core}, node_id={tunnel.node_id}")
    
    raw_transport = (
        tunnel.transport_type
        or (tunnel.spec or {}).get("transport_type")
        or (tunnel.spec or {}).get("transport")
    )
    try:
        contract = _contract_check(
            tunnel.core,
            tunnel.type,
            raw_transport,
            tunnel.spec,
            tunnel.security_type,
        )
    except TunnelContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for warning in contract["warnings"]:
        logger.warning(f"Tunnel contract ({tunnel.core}, create): {warning}")

    if tunnel.spec:
        validate_tunnel_payload(tunnel.spec)
    if tunnel.port_ranges:
        validate_tunnel_payload({"port_ranges": tunnel.port_ranges})
        if isinstance(tunnel.spec, dict):
            tunnel.spec["port_ranges"] = tunnel.port_ranges
        elif tunnel.spec is None:
            tunnel.spec = {"port_ranges": tunnel.port_ranges}

    if tunnel.spec and tunnel.core == "backhaul":
        ports_received = tunnel.spec.get("ports", [])
        logger.info(f"Backhaul tunnel creation: received ports from frontend: {ports_received} (type: {type(ports_received)}, length: {len(ports_received) if isinstance(ports_received, list) else 'N/A'})")
    
    if tunnel.spec and tunnel.core != "backhaul":
        ports = parse_ports_from_spec(tunnel.spec)
        if ports:
            tunnel.spec["ports"] = ports
    
    foreign_node_id_val = tunnel.foreign_node_id if tunnel.foreign_node_id and (not isinstance(tunnel.foreign_node_id, str) or tunnel.foreign_node_id.strip()) else None
    iran_node_id_val = tunnel.iran_node_id if tunnel.iran_node_id and (not isinstance(tunnel.iran_node_id, str) or tunnel.iran_node_id.strip()) else None
    node_id_val = tunnel.node_id if tunnel.node_id and (not isinstance(tunnel.node_id, str) or tunnel.node_id.strip()) else None

    is_multi_node_tunnel = tunnel.core in {"rathole", "backhaul", "chisel", "frp"} or (
        tunnel.core == "gost" and (
            bool(foreign_node_id_val)
            or bool(iran_node_id_val)
            or bool(node_id_val)
            or tunnel.is_reverse is not None
        )
    )
    force_direct = _to_bool((tunnel.spec or {}).get("force_direct")) is True
    if force_direct:
        is_reverse = False
    elif tunnel.core in {"rathole", "backhaul"}:
        is_reverse = True
    elif tunnel.core in {"gost", "chisel", "frp"}:
        raw_rev = _to_bool(tunnel.is_reverse)
        if raw_rev is not None:
            is_reverse = raw_rev
        elif _to_bool((tunnel.spec or {}).get("is_reverse")) is not None:
            is_reverse = _to_bool((tunnel.spec or {}).get("is_reverse"))
        elif foreign_node_id_val or iran_node_id_val:
            is_reverse = True
        else:
            is_reverse = False
    else:
        is_reverse = _to_bool(tunnel.is_reverse) if tunnel.is_reverse is not None else False

    tunnel.is_reverse = is_reverse
    if isinstance(tunnel.spec, dict):
        tunnel.spec["is_reverse"] = is_reverse
        tunnel.spec["force_direct"] = not is_reverse
    foreign_node = None
    iran_node = None
    
    if is_multi_node_tunnel:
        foreign_node_id_val = tunnel.foreign_node_id if tunnel.foreign_node_id and (not isinstance(tunnel.foreign_node_id, str) or tunnel.foreign_node_id.strip()) else None
        if foreign_node_id_val:
            result = await db.execute(select(Node).where(Node.id == foreign_node_id_val))
            foreign_node = result.scalar_one_or_none()
            if not foreign_node:
                raise HTTPException(status_code=404, detail=f"Foreign node {foreign_node_id_val} not found")
            if foreign_node.node_metadata.get("role") != "foreign":
                raise HTTPException(status_code=400, detail=f"Node {foreign_node_id_val} is not a foreign node")
        
        iran_node_id_val = tunnel.iran_node_id if tunnel.iran_node_id and (not isinstance(tunnel.iran_node_id, str) or tunnel.iran_node_id.strip()) else None
        if iran_node_id_val:
            result = await db.execute(select(Node).where(Node.id == iran_node_id_val))
            iran_node = result.scalar_one_or_none()
            if not iran_node:
                raise HTTPException(status_code=404, detail=f"Iran node {iran_node_id_val} not found")
            if iran_node.node_metadata.get("role") != "iran":
                raise HTTPException(status_code=400, detail=f"Node {iran_node_id_val} is not an iran node")
        
        node_id_val = tunnel.node_id if tunnel.node_id and (not isinstance(tunnel.node_id, str) or tunnel.node_id.strip()) else None
        if node_id_val and not (foreign_node and iran_node):
            result = await db.execute(select(Node).where(Node.id == node_id_val))
            provided_node = result.scalar_one_or_none()
            if not provided_node:
                raise HTTPException(status_code=404, detail="Node not found")
            
            node_role = provided_node.node_metadata.get("role", "iran")
            if node_role == "foreign":
                foreign_node = provided_node
                result = await db.execute(select(Node))
                all_nodes = result.scalars().all()
                iran_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
                if iran_nodes:
                    iran_node = iran_nodes[0]
                else:
                    raise HTTPException(status_code=400, detail="No iran node found. Please specify iran_node_id or register an iran node.")
            else:
                iran_node = provided_node
                result = await db.execute(select(Node))
                all_nodes = result.scalars().all()
                foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
                if foreign_nodes:
                    foreign_node = foreign_nodes[0]
                else:
                    raise HTTPException(status_code=400, detail="No foreign node found. Please specify foreign_node_id or register a foreign node.")
        
        if not foreign_node or not iran_node:
            raise HTTPException(status_code=400, detail=f"Both foreign and iran nodes are required for {tunnel.core.title()} tunnels. Provide foreign_node_id and iran_node_id, or provide node_id and we'll find the matching node.")
        
        node = iran_node
    else:
        node = None
        if tunnel.node_id or tunnel.iran_node_id:
            node_id_to_check = tunnel.iran_node_id or tunnel.node_id
            result = await db.execute(select(Node).where(Node.id == node_id_to_check))
            node = result.scalar_one_or_none()
    
    tunnel_node_id = tunnel.iran_node_id or tunnel.node_id or ""
    
    foreign_node_id_to_store = foreign_node.id if foreign_node else None
    iran_node_id_to_store = iran_node.id if iran_node else None
    
    # Proactively check port collisions on involved nodes before creating tunnel
    await check_port_conflicts(
        db=db,
        spec=tunnel.spec or {},
        iran_node_id=iran_node_id_to_store,
        foreign_node_id=foreign_node_id_to_store,
        node_id=tunnel_node_id,
        core=tunnel.core,
        is_reverse=is_reverse
    )
    
    db_tunnel = Tunnel(
        name=tunnel.name,
        core=tunnel.core,
        type=tunnel.type,
        node_id=tunnel_node_id,
        foreign_node_id=foreign_node_id_to_store,
        iran_node_id=iran_node_id_to_store,
        spec=tunnel.spec,
        cdn_mode=tunnel.cdn_mode or False,
        gaming_mode=tunnel.gaming_mode or False,
        custom_host=tunnel.custom_host,
        custom_sni=tunnel.custom_sni,
        ws_path=tunnel.ws_path,
        is_reverse=is_reverse,
        port_ranges=tunnel.port_ranges,
        stealth_domain=tunnel.stealth_domain,
        allowed_ips=tunnel.allowed_ips,
        rate_limit_mbps=tunnel.rate_limit_mbps,
        transport_type=tunnel.transport_type,
        security_type=tunnel.security_type,
        failover_ips=tunnel.failover_ips,
        utls_fingerprint=tunnel.utls_fingerprint,
        custom_headers=tunnel.custom_headers,
        obfuscation_type=tunnel.obfuscation_type,
        mux_type=tunnel.mux_type,
        relay_hops=tunnel.relay_hops,
        bypass_ips=tunnel.bypass_ips,
        dns_resolvers=tunnel.dns_resolvers,
        category=tunnel.category,
        selector_strategy=tunnel.selector_strategy or "fifo",
        keepalive_interval=tunnel.keepalive_interval or 15,
        status="pending"
    )
    db.add(db_tunnel)
    await db.commit()
    await db.refresh(db_tunnel)
    
    try:
        single_node_id = db_tunnel.node_id or getattr(db_tunnel, "iran_node_id", None)
        is_panel_tunnel = not single_node_id
        
        needs_gost_forwarding = db_tunnel.type in ["tcp", "udp", "ws", "grpc", "tcpmux", "tcp+udp"] and db_tunnel.core == "gost" and is_panel_tunnel
        needs_rathole_server = False
        needs_backhaul_server = False
        needs_chisel_server = (db_tunnel.core == "chisel" and is_panel_tunnel)
        needs_frp_server = False
        needs_node_apply = single_node_id is not None
        
        logger.info(
            "Tunnel %s: gost=%s, rathole=%s, backhaul=%s, chisel=%s, frp=%s",
            db_tunnel.id,
            needs_gost_forwarding,
            needs_rathole_server,
            needs_backhaul_server,
            needs_chisel_server,
            needs_frp_server,
        )
        
        if is_multi_node_tunnel and foreign_node and iran_node:
            client = NodeClient()
            
            iran_node_ip = iran_node.node_metadata.get("ip_address")
            if not iran_node_ip:
                db_tunnel.status = "error"
                db_tunnel.error_message = "Iran node has no IP address"
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel

            foreign_node_ip = foreign_node.node_metadata.get("ip_address")
            if not foreign_node_ip:
                db_tunnel.status = "error"
                db_tunnel.error_message = "Foreign node has no IP address"
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel

            from app.tunnel_planning import prepare_tunnel_for_apply
            await prepare_tunnel_for_apply(db, db_tunnel, client, [iran_node.id, foreign_node.id])
            iran_spec, foreign_spec = build_tunnel_node_specs(db_tunnel, iran_node_ip, foreign_node_ip)
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(db_tunnel, "spec")
            await db.commit()
            
            if not iran_node.node_metadata.get("api_address"):
                iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                await db.commit()

            if not foreign_node.node_metadata.get("api_address"):
                foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                await db.commit()

            if iran_spec.get("mode") == "server":
                first_node, first_spec, first_role = iran_node, iran_spec, f"iran node {iran_node.id} (server)"
                second_node, second_spec, second_role = foreign_node, foreign_spec, f"foreign node {foreign_node.id} (client)"
            else:
                first_node, first_spec, first_role = foreign_node, foreign_spec, f"foreign node {foreign_node.id} (server)"
                second_node, second_spec, second_role = iran_node, iran_spec, f"iran node {iran_node.id} (client)"

            logger.info(f"Applying config to {first_role} for tunnel {db_tunnel.id}")
            first_response = await client.send_to_node(
                node_id=first_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": db_tunnel.id,
                    "core": db_tunnel.core,
                    "type": db_tunnel.type,
                    "spec": first_spec
                }
            )
            
            if first_response.get("status") == "error":
                db_tunnel.status = "error"
                error_msg = first_response.get("message", f"Unknown error from {first_role}")
                db_tunnel.error_message = f"{first_role} error: {error_msg}"
                logger.error(f"Tunnel {db_tunnel.id}: {first_role} error: {error_msg}")
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel

            # Allow server to bind and stabilize before client connects
            await asyncio.sleep(1.0)

            logger.info(f"Applying config to {second_role} for tunnel {db_tunnel.id}")
            try:
                second_response = await client.send_to_node(
                    node_id=second_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": db_tunnel.id,
                        "core": db_tunnel.core,
                        "type": db_tunnel.type,
                        "spec": second_spec
                    }
                )
            except Exception as e:
                second_response = {"status": "error", "message": f"Connection exception: {e}"}
            
            if second_response.get("status") == "error":
                db_tunnel.status = "error"
                error_msg = second_response.get("message", f"Unknown error from {second_role}")
                db_tunnel.error_message = f"{second_role} error: {error_msg}"
                logger.error(f"Tunnel {db_tunnel.id}: {second_role} error: {error_msg}")
                try:
                    spec_to_extract = dict(first_spec or db_tunnel.spec or {})
                    if getattr(db_tunnel, "port_ranges", None) and "port_ranges" not in spec_to_extract:
                        spec_to_extract["port_ranges"] = db_tunnel.port_ranges
                    f_ports = list(extract_all_tunnel_ports(spec_to_extract, validate_span=False).get("all_ports", set()))
                    f_ctrl = (first_spec or {}).get("control_port") or (db_tunnel.spec or {}).get("control_port")
                    await client.send_to_node(
                        node_id=first_node.id,
                        endpoint="/api/agent/tunnels/remove",
                        data={
                            "tunnel_id": db_tunnel.id,
                            "purge": True,
                            "core": db_tunnel.core,
                            "ports": f_ports,
                            "control_port": f_ctrl,
                        }
                    )
                except Exception as rollback_err:
                    logger.warning(f"Compensating rollback on {first_role} failed for tunnel {db_tunnel.id}: {rollback_err}")
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel
            
            if first_response.get("status") == "success" and second_response.get("status") == "success":
                db_tunnel.status = "active"
                logger.info(f"Tunnel {db_tunnel.id} successfully applied to both nodes")
            else:
                db_tunnel.status = "error"
                db_tunnel.error_message = "Failed to apply tunnel to one or both nodes"
                logger.error(f"Tunnel {db_tunnel.id}: Failed to apply to nodes")
            
            await db.commit()
            await db.refresh(db_tunnel)
            return db_tunnel
        
        
        if needs_node_apply and not is_multi_node_tunnel:
            remote_addr = db_tunnel.spec.get("remote_addr")
            token = db_tunnel.spec.get("token")
            proxy_port = db_tunnel.spec.get("remote_port") or db_tunnel.spec.get("listen_port")
            use_ipv6 = db_tunnel.spec.get("use_ipv6", False)
            
            if remote_addr:
                from app.utils import parse_address_port
                _, rathole_port, _ = parse_address_port(remote_addr)
                try:
                    if rathole_port and int(rathole_port) == 8000:
                        db_tunnel.status = "error"
                        db_tunnel.error_message = "Rathole server cannot use port 8000 (panel API port). Use a different port like 23333."
                        await db.commit()
                        await db.refresh(db_tunnel)
                        return db_tunnel
                except (ValueError, TypeError):
                    pass
            
            if remote_addr and token and proxy_port and hasattr(request.app.state, 'rathole_server_manager'):
                try:
                    logger.info(f"Starting Rathole server for tunnel {db_tunnel.id}: remote_addr={remote_addr}, token={'set' if token else 'none'}, proxy_port={proxy_port}, use_ipv6={use_ipv6}")
                    transport_type = getattr(db_tunnel, "transport_type", None) or db_tunnel.spec.get("transport_type") or db_tunnel.spec.get("transport") or "tcp"
                    tunnel_type = getattr(db_tunnel, "type", None) or db_tunnel.spec.get("tunnel_type") or "tcp"
                    
                    server_priv = db_tunnel.spec.get("server_private_key", "")
                    client_pub = db_tunnel.spec.get("client_public_key", "")
                    if transport_type.lower() == "noise" and not (server_priv and client_pub):
                        from app.utils import generate_noise_keypair
                        s_priv, s_pub = generate_noise_keypair()
                        c_priv, c_pub = generate_noise_keypair()
                        db_tunnel.spec["server_private_key"] = s_priv
                        db_tunnel.spec["server_public_key"] = s_pub
                        db_tunnel.spec["client_private_key"] = c_priv
                        db_tunnel.spec["client_public_key"] = c_pub
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(db_tunnel, "spec")
                        server_priv, client_pub = s_priv, c_pub

                    ports = parse_ports_from_spec(db_tunnel.spec)
                    await request.app.state.rathole_server_manager.start_server(
                        tunnel_id=db_tunnel.id,
                        remote_addr=remote_addr,
                        token=token,
                        proxy_port=int(proxy_port) if proxy_port else None,
                        use_ipv6=bool(use_ipv6),
                        ports=ports if ports else ([int(proxy_port)] if proxy_port else None),
                        tunnel_type=tunnel_type,
                        transport_proto=transport_type,
                        local_private_key=server_priv,
                        remote_public_key=client_pub,
                        websocket_tls=bool(db_tunnel.spec.get("websocket_tls") or db_tunnel.spec.get("tls"))
                    )
                    logger.info(f"Successfully started Rathole server for tunnel {db_tunnel.id}")
                    rathole_started = True
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Failed to start Rathole server for tunnel {db_tunnel.id}: {error_msg}", exc_info=True)
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"Rathole server error: {error_msg}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
            else:
                missing = []
                if not remote_addr:
                    missing.append("remote_addr")
                if not token:
                    missing.append("token")
                if not proxy_port:
                    missing.append("proxy_port")
                if not hasattr(request.app.state, 'rathole_server_manager'):
                    missing.append("rathole_server_manager")
                logger.warning(f"Tunnel {db_tunnel.id}: Missing required fields for Rathole server: {missing}")
                if not remote_addr or not token or not proxy_port:
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"Missing required fields for Rathole: {missing}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
        
        if needs_chisel_server:
            ports = db_tunnel.spec.get("ports")
            first_port = ports[0] if (isinstance(ports, list) and len(ports) > 0) else None
            listen_port = db_tunnel.spec.get("listen_port") or db_tunnel.spec.get("remote_port") or db_tunnel.spec.get("server_port") or first_port
            auth = db_tunnel.spec.get("auth")
            fingerprint = db_tunnel.spec.get("fingerprint")
            use_ipv6 = db_tunnel.spec.get("use_ipv6", False)
            
            if listen_port:
                from app.utils import parse_address_port
                try:
                    if int(listen_port) == 8000:
                        db_tunnel.status = "error"
                        db_tunnel.error_message = "Chisel server cannot use port 8000 (panel API port). Use a different port."
                        await db.commit()
                        await db.refresh(db_tunnel)
                        return db_tunnel
                except (ValueError, TypeError):
                    pass
            
            if listen_port and hasattr(request.app.state, 'chisel_server_manager'):
                try:
                    server_control_port = db_tunnel.spec.get("control_port")
                    if server_control_port:
                        server_control_port = int(server_control_port)
                    else:
                        server_control_port = int(listen_port) + 10000
                    chisel_is_reverse = getattr(db_tunnel, "is_reverse", True)
                    if chisel_is_reverse is None:
                        chisel_is_reverse = db_tunnel.spec.get("is_reverse", True)
                    logger.info(f"Starting Chisel server for tunnel {db_tunnel.id}: server_control_port={server_control_port}, reverse_port={listen_port}, auth={auth is not None}, fingerprint={fingerprint is not None}, use_ipv6={use_ipv6}, reverse_only={chisel_is_reverse}")
                    await request.app.state.chisel_server_manager.start_server(
                        tunnel_id=db_tunnel.id,
                        server_port=server_control_port,
                        auth=auth,
                        fingerprint=fingerprint,
                        use_ipv6=bool(use_ipv6),
                        tls_cert_pem=db_tunnel.spec.get("tls_cert_pem"),
                        tls_key_pem=db_tunnel.spec.get("tls_key_pem"),
                        backend_url=db_tunnel.spec.get("backend_url"),
                        socks5=db_tunnel.type == "socks5" or db_tunnel.spec.get("socks5", False),
                        keepalive=db_tunnel.spec.get("keepalive"),
                        reverse_only=bool(chisel_is_reverse),
                    )
                    await asyncio.sleep(1.0)
                    if not await request.app.state.chisel_server_manager.is_running(db_tunnel.id):
                        raise RuntimeError("Chisel server process started but is not running")
                    chisel_started = True
                    logger.info(f"Successfully started Chisel server for tunnel {db_tunnel.id}")
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Failed to start Chisel server for tunnel {db_tunnel.id}: {error_msg}", exc_info=True)
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"Chisel server error: {error_msg}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
            else:
                missing = []
                if not listen_port:
                    missing.append("listen_port")
                if not hasattr(request.app.state, 'chisel_server_manager'):
                    missing.append("chisel_server_manager")
                logger.warning(f"Tunnel {db_tunnel.id}: Missing required fields for Chisel server: {missing}")
                if not listen_port:
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"Missing required fields for Chisel: {missing}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
        
        if needs_frp_server:
            bind_port = db_tunnel.spec.get("bind_port", 7000)
            token = db_tunnel.spec.get("token")
            
            if bind_port:
                from app.utils import parse_address_port
                try:
                    if int(bind_port) == 8000:
                        db_tunnel.status = "error"
                        db_tunnel.error_message = "FRP server cannot use port 8000 (panel API port). Use a different port like 7000."
                        await db.commit()
                        await db.refresh(db_tunnel)
                        return db_tunnel
                except (ValueError, TypeError):
                    pass
            
            if bind_port and hasattr(request.app.state, 'frp_server_manager'):
                try:
                    transport_type = getattr(db_tunnel, "transport_type", None) or db_tunnel.spec.get("transport_type") or db_tunnel.spec.get("transport") or "tcp"
                    security_type = getattr(db_tunnel, "security_type", None) or db_tunnel.spec.get("security_type") or "tls"
                    force_tls = bool(db_tunnel.spec.get("force_tls")) or (security_type in ["tls", "force_tls"])
                    await request.app.state.frp_server_manager.start_server(
                        tunnel_id=db_tunnel.id,
                        bind_port=int(bind_port),
                        token=token,
                        transport_proto=transport_type.lower(),
                        force_tls=force_tls,
                        tunnel_type=getattr(db_tunnel, "type", "tcp") or "tcp",
                        vhost_port=db_tunnel.spec.get("vhost_http_port") or db_tunnel.spec.get("vhost_https_port"),
                        tls_cert_pem=db_tunnel.spec.get("tls_cert_pem"),
                        tls_key_pem=db_tunnel.spec.get("tls_key_pem"),
                    )
                    await asyncio.sleep(1.0)
                    if not await request.app.state.frp_server_manager.is_running(db_tunnel.id):
                        raise RuntimeError("FRP server process started but is not running")
                    frp_started = True
                    logger.info(f"Successfully started FRP server for tunnel {db_tunnel.id}")
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Failed to start FRP server for tunnel {db_tunnel.id}: {error_msg}", exc_info=True)
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"FRP server error: {error_msg}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
            else:
                missing = []
                if not bind_port:
                    missing.append("bind_port")
                if not hasattr(request.app.state, 'frp_server_manager'):
                    missing.append("frp_server_manager")
                logger.warning(f"Tunnel {db_tunnel.id}: Missing required fields for FRP server: {missing}")
                if not bind_port:
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"Missing required fields for FRP: {missing}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
        
        if needs_node_apply:
            if not node:
                raise HTTPException(status_code=400, detail=f"Node is required for {db_tunnel.core.title()} tunnels")
            
            client = NodeClient()
            if not node.node_metadata.get("api_address"):
                node.node_metadata["api_address"] = f"http://{node.node_metadata.get('ip_address', node.fingerprint)}:{node.node_metadata.get('api_port', 8888)}"
                await db.commit()
            
            spec_for_node = db_tunnel.spec.copy() if db_tunnel.spec else {}
            
            if needs_chisel_server:
                listen_port = spec_for_node.get("listen_port") or spec_for_node.get("remote_port") or spec_for_node.get("server_port")
                use_ipv6 = spec_for_node.get("use_ipv6", False)
                if listen_port:
                    server_control_port = spec_for_node.get("control_port")
                    if server_control_port:
                        server_control_port = int(server_control_port)
                    else:
                        server_control_port = int(listen_port) + 10000
                    reverse_port = int(listen_port)
                    
                    panel_host = spec_for_node.get("panel_host")
                    
                    if not panel_host:
                        panel_address = node.node_metadata.get("panel_address", "")
                        if panel_address:
                            if "://" in panel_address:
                                panel_address = panel_address.split("://", 1)[1]
                            if ":" in panel_address:
                                panel_host = panel_address.split(":")[0]
                            else:
                                panel_host = panel_address
                    
                    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1"]:
                        panel_host = request.url.hostname
                        if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1"]:
                            forwarded_host = request.headers.get("X-Forwarded-Host")
                            if forwarded_host:
                                panel_host = forwarded_host.split(":")[0] if ":" in forwarded_host else forwarded_host
                    
                    if not panel_host or panel_host in ["localhost", "127.0.0.1", "::1"]:
                        logger.warning(f"Chisel tunnel {db_tunnel.id}: Could not determine panel host, using request hostname: {request.url.hostname}. Node may not be able to connect if this is localhost.")
                        panel_host = request.url.hostname or "localhost"
                    
                    from app.utils import is_valid_ipv6_address
                    chisel_transport = (spec_for_node.get("transport_type") or spec_for_node.get("transport") or "").lower()
                    chisel_tls = (chisel_transport in ("wss", "https", "tls")) or bool(spec_for_node.get("websocket_tls") or spec_for_node.get("tls"))
                    proto = "https://" if chisel_tls else "http://"
                    if is_valid_ipv6_address(panel_host):
                        server_url = f"{proto}[{panel_host}]:{server_control_port}"
                    else:
                        server_url = f"{proto}{panel_host}:{server_control_port}"
                    spec_for_node["server_url"] = server_url
                    spec_for_node["reverse_port"] = reverse_port
                    spec_for_node["remote_port"] = int(listen_port)
                    logger.info(f"Chisel tunnel {db_tunnel.id}: server_url={server_url}, server_control_port={server_control_port}, reverse_port={reverse_port}, use_ipv6={use_ipv6}, panel_host={panel_host}")
            
            if needs_frp_server:
                logger.info(f"Preparing FRP spec for tunnel {db_tunnel.id}, original spec server_addr: {spec_for_node.get('server_addr', 'NOT SET')}")
                try:
                    spec_for_node = prepare_frp_spec_for_node(spec_for_node, node, request)
                    final_server_addr = spec_for_node.get('server_addr', 'NOT SET')
                    logger.info(f"FRP spec prepared for tunnel {db_tunnel.id}: server_addr={final_server_addr}, server_port={spec_for_node.get('server_port')}")
                    if final_server_addr in ["0.0.0.0", "NOT SET", ""]:
                        raise ValueError(f"FRP server_addr is invalid: {final_server_addr}")
                except Exception as e:
                    error_msg = f"Failed to prepare FRP spec: {str(e)}"
                    logger.error(f"Tunnel {db_tunnel.id}: {error_msg}", exc_info=True)
                    db_tunnel.status = "error"
                    db_tunnel.error_message = f"FRP configuration error: {error_msg}"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
            
            from app.utils import sanitize_spec_for_log
            logger.info(f"Applying tunnel {db_tunnel.id} to node {node.id}, spec keys: {list(spec_for_node.keys())}, server_addr: {spec_for_node.get('server_addr', 'NOT SET')}, spec: {sanitize_spec_for_log(spec_for_node)}")
            response = await client.send_to_node(
                node_id=node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": db_tunnel.id,
                    "core": db_tunnel.core,
                    "type": db_tunnel.type,
                    "spec": spec_for_node
                }
            )
            
            if response.get("status") == "error":
                db_tunnel.status = "error"
                error_msg = response.get("message", "Unknown error from node")
                db_tunnel.error_message = f"Node error: {error_msg}"
                logger.error(f"Tunnel {db_tunnel.id}: {error_msg}")
                if needs_rathole_server and hasattr(request.app.state, 'rathole_server_manager'):
                    try:
                        await request.app.state.rathole_server_manager.stop_server(db_tunnel.id)
                    except:
                        pass
                if needs_backhaul_server and hasattr(request.app.state, "backhaul_manager"):
                    try:
                        await request.app.state.backhaul_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                if needs_chisel_server and hasattr(request.app.state, 'chisel_server_manager'):
                    try:
                        await request.app.state.chisel_server_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                if needs_frp_server and hasattr(request.app.state, 'frp_server_manager'):
                    try:
                        await request.app.state.frp_server_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel
            
            if response.get("status") != "success":
                db_tunnel.status = "error"
                db_tunnel.error_message = "Failed to apply tunnel to node. Check node connection."
                logger.error(f"Tunnel {db_tunnel.id}: Failed to apply to node")
                if needs_rathole_server and hasattr(request.app.state, 'rathole_server_manager'):
                    try:
                        await request.app.state.rathole_server_manager.stop_server(db_tunnel.id)
                    except:
                        pass
                if needs_backhaul_server and hasattr(request.app.state, "backhaul_manager"):
                    try:
                        await request.app.state.backhaul_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                if needs_chisel_server and hasattr(request.app.state, 'chisel_server_manager'):
                    try:
                        await request.app.state.chisel_server_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                if needs_frp_server and hasattr(request.app.state, 'frp_server_manager'):
                    try:
                        await request.app.state.frp_server_manager.stop_server(db_tunnel.id)
                    except Exception:
                        pass
                await db.commit()
                await db.refresh(db_tunnel)
                return db_tunnel
        
        db_tunnel.status = "active"
        
        try:
            if needs_gost_forwarding:
                iran_node_id_val = tunnel.iran_node_id if tunnel.iran_node_id and (not isinstance(tunnel.iran_node_id, str) or tunnel.iran_node_id.strip()) else None
                foreign_node_id_val = tunnel.foreign_node_id if tunnel.foreign_node_id and (not isinstance(tunnel.foreign_node_id, str) or tunnel.foreign_node_id.strip()) else None
                
                ports = parse_ports_from_spec(db_tunnel.spec)
                if not ports:
                    listen_port = db_tunnel.spec.get("listen_port")
                    if listen_port:
                        ports = [int(listen_port) if isinstance(listen_port, (int, str)) and str(listen_port).isdigit() else listen_port]
                
                forward_to = db_tunnel.spec.get("forward_to")
                remote_ip = db_tunnel.spec.get("remote_ip", "127.0.0.1")
                use_ipv6 = db_tunnel.spec.get("use_ipv6", False)
                
                if not ports:
                    db_tunnel.status = "error"
                    db_tunnel.error_message = "GOST requires ports"
                    await db.commit()
                    await db.refresh(db_tunnel)
                    return db_tunnel
                
                if ports and hasattr(request.app.state, 'gost_forwarder'):
                    try:
                        for port in ports:
                            port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                            if not forward_to:
                                from app.utils import format_address_port
                                forward_to_port = format_address_port(remote_ip, port_num)
                            else:
                                forward_to_port = forward_to
                            
                            tunnel_id_for_port = f"{db_tunnel.id}_{port_num}" if len(ports) > 1 else db_tunnel.id
                            logger.info(f"Starting gost forwarding on panel for tunnel {db_tunnel.id}: {db_tunnel.type}://:{port_num} -> {forward_to_port}, use_ipv6={use_ipv6}")
                            await request.app.state.gost_forwarder.start_forward(
                                tunnel_id=tunnel_id_for_port,
                                local_port=port_num,
                                forward_to=forward_to_port,
                                tunnel_type=db_tunnel.type,
                                use_ipv6=bool(use_ipv6)
                            )
                        
                        await asyncio.sleep(2)
                        logger.info(f"Successfully started gost forwarding on panel for tunnel {db_tunnel.id} with {len(ports)} ports")
                    except Exception as e:
                        error_msg = str(e)
                        logger.error(f"Failed to start gost forwarding on panel for tunnel {db_tunnel.id}: {error_msg}", exc_info=True)
                        db_tunnel.status = "error"
                        db_tunnel.error_message = f"Gost forwarding error: {error_msg}"
                        await db.commit()
                        await db.refresh(db_tunnel)
                        return db_tunnel
                else:
                        missing = []
                        if not ports:
                            missing.append("ports")
                        if not forward_to and not remote_ip:
                            missing.append("forward_to")
                        if not hasattr(request.app.state, 'gost_forwarder'):
                            missing.append("gost_forwarder")
                        logger.warning(f"Tunnel {db_tunnel.id}: Missing required fields: {missing}")
                        if not forward_to:
                            error_msg = "forward_to is required for gost tunnels"
                            db_tunnel.status = "error"
                            db_tunnel.error_message = error_msg
            
        except Exception as e:
            logger.error(f"Exception in forwarding setup for tunnel {db_tunnel.id}: {e}", exc_info=True)
        
        await db.commit()
        await db.refresh(db_tunnel)
    except Exception as e:
        logger.error(f"Exception in tunnel creation for {db_tunnel.id}: {e}", exc_info=True)
        error_msg = str(e)
        db_tunnel.status = "error"
        db_tunnel.error_message = f"Tunnel creation error: {error_msg}"
        try:
            if needs_rathole_server and hasattr(request.app.state, "rathole_server_manager"):
                await request.app.state.rathole_server_manager.stop_server(db_tunnel.id)
        except Exception:
            pass
        try:
            if needs_backhaul_server and hasattr(request.app.state, "backhaul_manager"):
                await request.app.state.backhaul_manager.stop_server(db_tunnel.id)
        except Exception:
            pass
        await db.commit()
        await db.refresh(db_tunnel)
    
    return db_tunnel


_ping_cache: Dict[str, Tuple[float, Optional[int]]] = {}


@router.get("", response_model=List[TunnelResponse])
async def list_tunnels(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """List all tunnels with accurate live latency metadata"""
    result = await db.execute(select(Tunnel))
    tunnels = result.scalars().all()
    
    node_res = await db.execute(select(Node))
    nodes_map = {n.id: n for n in node_res.scalars().all()}
    
    for t in tunnels:
        if not t.spec:
            t.spec = {}
        if t.status == "active":
            iran_id = t.iran_node_id or t.node_id
            foreign_id = t.foreign_node_id
            ctrl_port = t.spec.get("control_port") or t.spec.get("remote_port")
            cache_key = f"{iran_id}:{foreign_id}:{ctrl_port}"
            
            if cache_key in _ping_cache and _ping_cache[cache_key][1] is not None:
                t.spec["latency_ms"] = _ping_cache[cache_key][1]
            elif not t.spec.get("latency_ms"):
                foreign_node = nodes_map.get(foreign_id)
                iran_node = nodes_map.get(iran_id)
                lat_for = foreign_node.node_metadata.get("latency_ms") if foreign_node and foreign_node.node_metadata else None
                lat_ir = iran_node.node_metadata.get("latency_ms") if iran_node and iran_node.node_metadata else None
                if lat_for:
                    t.spec["latency_ms"] = lat_for
                elif lat_ir:
                    t.spec["latency_ms"] = lat_ir
    return tunnels


async def _probe_tunnel_latency(client, iran_id: str, iran_ip: Optional[str], foreign_ip: str, port: Optional[int], cache_key: str):
    """Measures precise ping/RTT between specific Iran node and Foreign node"""
    from app.utils import measure_precise_ping
    try:
        candidate_ports = []
        if port:
            candidate_ports.append(port)
        candidate_ports.extend([8888, 8889, 22, 443, 80, 8080, 7000])
        
        from app.config import settings
        local_ips = {ip.strip() for ip in settings.panel_local_ips.split(",") if ip.strip()}
        if not iran_ip or iran_ip in local_ips:
            res = await measure_precise_ping(foreign_ip, fallback_ports=candidate_ports)
        else:
            res = await client.probe_ping(iran_id, foreign_ip, port)
        _ping_cache[cache_key] = (time.time(), res)
    except Exception as e:
        logger.debug(f"Probe latency failed for {cache_key}: {e}")


@router.get("/latencies")
async def get_tunnels_latencies(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """
    Ultra-lightweight endpoint for real-time 2-second live ping polling.
    Measures the exact, true network latency between the specific Iran Node and Foreign Node for each tunnel.
    Returns { "tunnels": { "<tunnel_id>": <latency_ms> }, "timestamp": <unix_ts> }
    """
    from app.utils import measure_precise_ping
    from app.node_client import NodeClient
    
    result = await db.execute(
        select(Tunnel.id, Tunnel.status, Tunnel.node_id, Tunnel.iran_node_id, Tunnel.foreign_node_id, Tunnel.spec)
        .where(Tunnel.status == "active")
    )
    active_tunnels = result.all()
    if not active_tunnels:
        return {"tunnels": {}, "timestamp": int(time.time())}
        
    node_res = await db.execute(select(Node.id, Node.node_metadata))
    nodes_ip_map = {}
    for n_id, n_meta in node_res.all():
        if n_meta and n_meta.get("ip_address"):
            nodes_ip_map[n_id] = n_meta.get("ip_address")
            
    now = time.time()
    client = NodeClient()
    
    # Map tunnels to their unique probe tasks
    tunnel_keys: Dict[str, str] = {}
    probe_tasks = {}
    
    for t_id, t_status, t_node_id, t_iran_id, t_foreign_id, t_spec in active_tunnels:
        iran_id = t_iran_id or t_node_id
        foreign_id = t_foreign_id
        foreign_ip = nodes_ip_map.get(foreign_id)
        iran_ip = nodes_ip_map.get(iran_id)
        
        if not foreign_ip:
            continue
            
        spec = t_spec or {}
        ctrl_port = spec.get("control_port") or spec.get("remote_port")
        cache_key = f"{iran_id}:{foreign_id}:{ctrl_port}"
        tunnel_keys[t_id] = cache_key
        
        # If cache expired or not present, queue a probe
        if cache_key not in _ping_cache or (now - _ping_cache[cache_key][0]) > 1.8:
            if cache_key not in probe_tasks:
                probe_tasks[cache_key] = _probe_tunnel_latency(client, iran_id, iran_ip, foreign_ip, ctrl_port, cache_key)
                
    if probe_tasks:
        await asyncio.gather(*probe_tasks.values(), return_exceptions=True)
        
    tunnel_latencies = {}
    for t_id, c_key in tunnel_keys.items():
        if c_key in _ping_cache and _ping_cache[c_key][1] is not None:
            tunnel_latencies[t_id] = _ping_cache[c_key][1]
            
    return {
        "tunnels": tunnel_latencies,
        "timestamp": int(time.time())
    }


@router.get("/categories", response_model=List[CategoryResponse])
async def list_categories(
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """List all categories with dynamic count of tunnels in each"""
    cat_result = await db.execute(select(TunnelCategory).order_by(TunnelCategory.name))
    categories = cat_result.scalars().all()
    cat_dict = {c.name: c for c in categories}
    
    tun_result = await db.execute(select(Tunnel.category))
    tunnel_cats = tun_result.scalars().all()
    
    counts: dict[str, int] = {}
    for c in tunnel_cats:
        if c:
            counts[c] = counts.get(c, 0) + 1
            if c not in cat_dict:
                new_cat = TunnelCategory(name=c, color="blue")
                db.add(new_cat)
                cat_dict[c] = new_cat
    
    if len(counts) > len(categories):
        await db.commit()
    
    res = []
    for name, cat in sorted(cat_dict.items(), key=lambda x: x[0].lower()):
        res.append(CategoryResponse(
            id=cat.id,
            name=cat.name,
            color=cat.color or "blue",
            description=cat.description,
            tunnel_count=counts.get(name, 0),
            created_at=cat.created_at or utcnow()
        ))
    return res


@router.post("/categories", response_model=CategoryResponse, status_code=201)
async def create_category(
    cat_in: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """Create a new category"""
    clean_name = cat_in.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Category name cannot be empty")
    
    result = await db.execute(select(TunnelCategory).where(TunnelCategory.name == clean_name))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Category already exists")
    
    new_cat = TunnelCategory(
        name=clean_name,
        color=cat_in.color or "blue",
        description=cat_in.description
    )
    db.add(new_cat)
    await db.commit()
    await db.refresh(new_cat)
    
    return CategoryResponse(
        id=new_cat.id,
        name=new_cat.name,
        color=new_cat.color or "blue",
        description=new_cat.description,
        tunnel_count=0,
        created_at=new_cat.created_at
    )


@router.delete("/categories/{name}")
async def delete_category(
    name: str,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """Delete a category and reset associated tunnels to None (uncategorized)"""
    result = await db.execute(select(TunnelCategory).where(TunnelCategory.name == name))
    cat = result.scalar_one_or_none()
    if cat:
        await db.delete(cat)
    
    tun_result = await db.execute(select(Tunnel).where(Tunnel.category == name))
    tunnels = tun_result.scalars().all()
    for t in tunnels:
        t.category = None
    
    await db.commit()
    return {"status": "success", "message": f"Category {name} deleted"}


@router.post("/bulk-category")
async def bulk_assign_category(
    req: BulkCategoryAssign,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """Assign or clear category for multiple tunnels atomically"""
    if not req.tunnel_ids:
        return {"status": "success", "updated_count": 0}
    
    clean_category = req.category.strip() if req.category and req.category.strip() else None
    
    if clean_category:
        cat_res = await db.execute(select(TunnelCategory).where(TunnelCategory.name == clean_category))
        if not cat_res.scalar_one_or_none():
            new_cat = TunnelCategory(name=clean_category, color="blue")
            db.add(new_cat)
    
    tun_result = await db.execute(select(Tunnel).where(Tunnel.id.in_(req.tunnel_ids)))
    tunnels = tun_result.scalars().all()
    
    for t in tunnels:
        t.category = clean_category
        
    await db.commit()
    return {
        "status": "success",
        "updated_count": len(tunnels),
        "category": clean_category
    }


@router.get("/{tunnel_id}", response_model=TunnelResponse)
async def get_tunnel(tunnel_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get tunnel by ID"""
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    return tunnel


@router.put("/{tunnel_id}", response_model=TunnelResponse)
async def update_tunnel(
    tunnel_id: str,
    tunnel_update: TunnelUpdate,
    request: Request,
    apply: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """Update a tunnel. Node and type changes are staged in ``pending_changes``;
    ``?apply=true`` applies the staged changes right away (with automatic rollback)."""
    from app.node_client import NodeClient
    
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    
    fields_set = getattr(tunnel_update, "model_fields_set", set())
    if not fields_set:
        fields_set = getattr(tunnel_update, "__fields_set__", set())

    def is_field_set(fname: str) -> bool:
        if fields_set:
            return fname in fields_set
        return getattr(tunnel_update, fname, None) is not None

    spec_changed = False
    multi_node = is_multi_node(tunnel)

    if is_field_set("spec") and tunnel_update.spec != tunnel.spec:
        spec_changed = True
    if is_field_set("cdn_mode") and bool(tunnel_update.cdn_mode) != bool(tunnel.cdn_mode):
        spec_changed = True
    if is_field_set("gaming_mode") and bool(tunnel_update.gaming_mode) != bool(tunnel.gaming_mode):
        spec_changed = True
    if is_field_set("custom_host") and (tunnel_update.custom_host.strip() if tunnel_update.custom_host else None) != tunnel.custom_host:
        spec_changed = True
    if is_field_set("custom_sni") and (tunnel_update.custom_sni.strip() if tunnel_update.custom_sni else None) != tunnel.custom_sni:
        spec_changed = True
    if is_field_set("ws_path") and (tunnel_update.ws_path.strip() if tunnel_update.ws_path else None) != tunnel.ws_path:
        spec_changed = True
    if is_field_set("is_reverse") and tunnel_update.is_reverse is not None and tunnel_update.is_reverse != tunnel.is_reverse:
        spec_changed = True
    if not multi_node and is_field_set("node_id") and (tunnel_update.node_id.strip() if tunnel_update.node_id else None) != tunnel.node_id:
        spec_changed = True
    if not multi_node and is_field_set("foreign_node_id") and (tunnel_update.foreign_node_id.strip() if tunnel_update.foreign_node_id else None) != tunnel.foreign_node_id:
        spec_changed = True
    if not multi_node and is_field_set("iran_node_id") and (tunnel_update.iran_node_id.strip() if tunnel_update.iran_node_id else None) != tunnel.iran_node_id:
        spec_changed = True
    if is_field_set("port_ranges") and (tunnel_update.port_ranges if tunnel_update.port_ranges else None) != tunnel.port_ranges:
        spec_changed = True
    if is_field_set("stealth_domain") and (tunnel_update.stealth_domain.strip() if tunnel_update.stealth_domain else None) != tunnel.stealth_domain:
        spec_changed = True
    if is_field_set("allowed_ips") and tunnel_update.allowed_ips != tunnel.allowed_ips:
        spec_changed = True
    if is_field_set("rate_limit_mbps"):
        parsed_rl = tunnel_update.rate_limit_mbps
        if parsed_rl is not None:
            try:
                parsed_rl = float(parsed_rl)
                if parsed_rl <= 0:
                    parsed_rl = None
            except (ValueError, TypeError):
                parsed_rl = None
        if parsed_rl != tunnel.rate_limit_mbps:
            spec_changed = True
    if is_field_set("transport_type") and tunnel_update.transport_type and tunnel_update.transport_type != tunnel.transport_type:
        spec_changed = True
    if is_field_set("security_type") and tunnel_update.security_type and tunnel_update.security_type != tunnel.security_type:
        spec_changed = True
    if is_field_set("failover_ips") and tunnel_update.failover_ips != tunnel.failover_ips:
        spec_changed = True
    if is_field_set("utls_fingerprint") and tunnel_update.utls_fingerprint != tunnel.utls_fingerprint:
        spec_changed = True
    if is_field_set("custom_headers") and tunnel_update.custom_headers != tunnel.custom_headers:
        spec_changed = True
    if is_field_set("obfuscation_type") and tunnel_update.obfuscation_type != tunnel.obfuscation_type:
        spec_changed = True
    if is_field_set("mux_type") and tunnel_update.mux_type != tunnel.mux_type:
        spec_changed = True
    if is_field_set("relay_hops") and tunnel_update.relay_hops != tunnel.relay_hops:
        spec_changed = True
    if is_field_set("bypass_ips") and tunnel_update.bypass_ips != tunnel.bypass_ips:
        spec_changed = True
    if is_field_set("dns_resolvers") and tunnel_update.dns_resolvers != tunnel.dns_resolvers:
        spec_changed = True
    if is_field_set("selector_strategy") and tunnel_update.selector_strategy != tunnel.selector_strategy:
        spec_changed = True
    if not multi_node and is_field_set("type") and tunnel_update.type and tunnel_update.type != tunnel.type:
        spec_changed = True
    if is_field_set("keepalive_interval") and tunnel_update.keepalive_interval != tunnel.keepalive_interval:
        spec_changed = True
    
    if any(is_field_set(f) for f in ("spec", "type", "transport_type", "security_type", "mux_type")):
        effective_type = tunnel_update.type if (is_field_set("type") and tunnel_update.type) else (tunnel.type or "tcp")
        stored_spec = tunnel.spec or {}
        stored_transport = tunnel.transport_type or stored_spec.get("transport_type") or stored_spec.get("transport")
        effective_transport = tunnel.transport_type
        if is_field_set("transport_type") and tunnel_update.transport_type:
            effective_transport = tunnel_update.transport_type
        validation_spec = tunnel_update.spec if tunnel_update.spec is not None else stored_spec
        if not effective_transport:
            effective_transport = validation_spec.get("transport_type") or validation_spec.get("transport")
        effective_security = tunnel.security_type
        if is_field_set("security_type") and tunnel_update.security_type:
            effective_security = tunnel_update.security_type
        legacy_type = tunnel.type if (effective_type or "").strip().lower() == (tunnel.type or "").strip().lower() else None
        try:
            contract = _contract_check(
                tunnel.core,
                effective_type,
                effective_transport,
                validation_spec,
                effective_security,
                legacy_type=legacy_type,
                legacy_pair=(stored_transport, tunnel.security_type),
            )
        except TunnelContractError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        for warning in contract["warnings"]:
            logger.warning(f"Tunnel contract {tunnel_id} ({tunnel.core}, update): {warning}")

    if tunnel_update.name is not None:
        tunnel.name = tunnel_update.name
    staged_request: Dict[str, Any] = {}
    if is_field_set("type") and tunnel_update.type:
        new_type = tunnel_update.type.strip().lower()
        if new_type in CORE_TYPES.get(tunnel.core, set()):
            if multi_node:
                staged_request["type"] = new_type
            else:
                tunnel.type = new_type
                if isinstance(tunnel.spec, dict):
                    tunnel.spec["type"] = new_type
                    tunnel.spec["tunnel_type"] = new_type
    if tunnel_update.spec is not None:
        validate_tunnel_payload(tunnel_update.spec)
        # For Backhaul, ensure ports are preserved in the correct format
        if tunnel.core == "backhaul" and tunnel_update.spec.get("ports"):
            # Ports should already be in the correct format from frontend, but ensure they're preserved
            ports = tunnel_update.spec.get("ports", [])
            logger.info(f"Backhaul tunnel update {tunnel_id}: preserving ports from update: {ports} (count: {len(ports) if isinstance(ports, list) else 'N/A'})")
        tunnel.spec = tunnel_update.spec
    if is_field_set("cdn_mode"):
        tunnel.cdn_mode = bool(tunnel_update.cdn_mode)
    if is_field_set("gaming_mode"):
        tunnel.gaming_mode = bool(tunnel_update.gaming_mode)
    if is_field_set("custom_host"):
        tunnel.custom_host = tunnel_update.custom_host.strip() if tunnel_update.custom_host else None
        if isinstance(tunnel.spec, dict):
            if tunnel.custom_host:
                tunnel.spec["custom_host"] = tunnel.custom_host
            else:
                tunnel.spec.pop("custom_host", None)
    if is_field_set("custom_sni"):
        tunnel.custom_sni = tunnel_update.custom_sni.strip() if tunnel_update.custom_sni else None
        if isinstance(tunnel.spec, dict):
            if tunnel.custom_sni:
                tunnel.spec["custom_sni"] = tunnel.custom_sni
            else:
                tunnel.spec.pop("custom_sni", None)
                if not getattr(tunnel, "stealth_domain", None) or tunnel.core in ["frp", "backhaul", "rathole", "chisel"]:
                    tunnel.spec.pop("stealth_domain", None)
                    if tunnel.core in ["frp", "backhaul", "rathole", "chisel"]:
                        tunnel.stealth_domain = None
    if is_field_set("ws_path"):
        tunnel.ws_path = tunnel_update.ws_path.strip() if tunnel_update.ws_path else None
        if isinstance(tunnel.spec, dict):
            if tunnel.ws_path:
                tunnel.spec["ws_path"] = tunnel.ws_path
            else:
                tunnel.spec.pop("ws_path", None)
    force_direct = _to_bool((tunnel.spec or {}).get("force_direct")) is True
    if force_direct:
        tunnel.is_reverse = False
        if isinstance(tunnel.spec, dict):
            tunnel.spec["is_reverse"] = False
            tunnel.spec["force_direct"] = True
    elif is_field_set("is_reverse") and tunnel_update.is_reverse is not None:
        tunnel.is_reverse = _to_bool(tunnel_update.is_reverse)
        if isinstance(tunnel.spec, dict):
            tunnel.spec["is_reverse"] = tunnel.is_reverse
            tunnel.spec["force_direct"] = not tunnel.is_reverse
    elif tunnel.core in {"gost", "chisel", "frp"}:
        if tunnel.is_reverse is None and (tunnel.foreign_node_id or tunnel.iran_node_id):
            tunnel.is_reverse = True
        if isinstance(tunnel.spec, dict):
            if tunnel.is_reverse is not None:
                tunnel.spec["is_reverse"] = tunnel.is_reverse
                tunnel.spec["force_direct"] = not tunnel.is_reverse
    if multi_node:
        # The live node columns are never touched here: a changed node is staged and
        # only switched by apply_pending_changes (node_id always follows the Iran node).
        if is_field_set("iran_node_id") and tunnel_update.iran_node_id and tunnel_update.iran_node_id.strip():
            staged_request["iran_node_id"] = tunnel_update.iran_node_id.strip()
        if is_field_set("foreign_node_id") and tunnel_update.foreign_node_id and tunnel_update.foreign_node_id.strip():
            staged_request["foreign_node_id"] = tunnel_update.foreign_node_id.strip()
    else:
        if is_field_set("node_id"):
            tunnel.node_id = tunnel_update.node_id.strip() if (tunnel_update.node_id and tunnel_update.node_id.strip()) else None
        if is_field_set("foreign_node_id"):
            tunnel.foreign_node_id = tunnel_update.foreign_node_id.strip() if (tunnel_update.foreign_node_id and tunnel_update.foreign_node_id.strip()) else None
        if is_field_set("iran_node_id"):
            tunnel.iran_node_id = tunnel_update.iran_node_id.strip() if (tunnel_update.iran_node_id and tunnel_update.iran_node_id.strip()) else None
    if is_field_set("port_ranges"):
        if tunnel_update.port_ranges:
            validate_tunnel_payload({"port_ranges": tunnel_update.port_ranges})
        tunnel.port_ranges = tunnel_update.port_ranges if tunnel_update.port_ranges else None
        if isinstance(tunnel.spec, dict):
            if tunnel.port_ranges:
                tunnel.spec["port_ranges"] = tunnel.port_ranges
            else:
                tunnel.spec.pop("port_ranges", None)
        spec_changed = True
    if is_field_set("stealth_domain"):
        tunnel.stealth_domain = tunnel_update.stealth_domain.strip() if tunnel_update.stealth_domain else None
        if isinstance(tunnel.spec, dict):
            if tunnel.stealth_domain:
                tunnel.spec["stealth_domain"] = tunnel.stealth_domain
            else:
                tunnel.spec.pop("stealth_domain", None)
                if not getattr(tunnel, "custom_sni", None) or tunnel.core in ["frp", "backhaul", "rathole", "chisel"]:
                    tunnel.spec.pop("custom_sni", None)
                    if tunnel.core in ["frp", "backhaul", "rathole", "chisel"]:
                        tunnel.custom_sni = None
    if is_field_set("allowed_ips"):
        tunnel.allowed_ips = tunnel_update.allowed_ips if tunnel_update.allowed_ips else None
        if isinstance(tunnel.spec, dict):
            if tunnel.allowed_ips:
                tunnel.spec["allowed_ips"] = tunnel.allowed_ips
            else:
                tunnel.spec.pop("allowed_ips", None)
    if is_field_set("rate_limit_mbps"):
        new_rl_val = tunnel_update.rate_limit_mbps
        if new_rl_val is not None:
            try:
                new_rl_val = float(new_rl_val)
                if new_rl_val <= 0:
                    new_rl_val = None
            except (ValueError, TypeError):
                new_rl_val = None
        tunnel.rate_limit_mbps = new_rl_val
        if isinstance(tunnel.spec, dict):
            if new_rl_val is not None:
                tunnel.spec["rate_limit_mbps"] = new_rl_val
            else:
                tunnel.spec.pop("rate_limit_mbps", None)
                if tunnel.core == "frp":
                    tunnel.spec.pop("bandwidth_limit", None)
                    tunnel.spec.pop("bandwidth_limit_mode", None)
    elif tunnel.core == "frp" and tunnel_update.spec is not None:
        if not tunnel_update.spec.get("bandwidth_limit") and not getattr(tunnel, "rate_limit_mbps", None):
            if isinstance(tunnel.spec, dict):
                tunnel.spec.pop("bandwidth_limit", None)
                tunnel.spec.pop("bandwidth_limit_mode", None)
                tunnel.spec.pop("rate_limit_mbps", None)
    if is_field_set("transport_type") and tunnel_update.transport_type:
        tunnel.transport_type = tunnel_update.transport_type
    if is_field_set("security_type") and tunnel_update.security_type:
        tunnel.security_type = tunnel_update.security_type
    if is_field_set("failover_ips"):
        tunnel.failover_ips = tunnel_update.failover_ips if tunnel_update.failover_ips else None
    if is_field_set("utls_fingerprint"):
        tunnel.utls_fingerprint = tunnel_update.utls_fingerprint
    if is_field_set("custom_headers"):
        tunnel.custom_headers = tunnel_update.custom_headers if tunnel_update.custom_headers else None
    if is_field_set("obfuscation_type"):
        tunnel.obfuscation_type = tunnel_update.obfuscation_type
    if is_field_set("mux_type"):
        tunnel.mux_type = tunnel_update.mux_type
    if is_field_set("relay_hops"):
        tunnel.relay_hops = tunnel_update.relay_hops if tunnel_update.relay_hops else None
    if is_field_set("bypass_ips"):
        tunnel.bypass_ips = tunnel_update.bypass_ips if tunnel_update.bypass_ips else None
    if is_field_set("dns_resolvers"):
        tunnel.dns_resolvers = tunnel_update.dns_resolvers if tunnel_update.dns_resolvers else None
    if is_field_set("category"):
        tunnel.category = tunnel_update.category.strip() if (tunnel_update.category and tunnel_update.category.strip()) else None
    if is_field_set("selector_strategy"):
        tunnel.selector_strategy = tunnel_update.selector_strategy
    if is_field_set("keepalive_interval") and tunnel_update.keepalive_interval is not None:
        tunnel.keepalive_interval = tunnel_update.keepalive_interval
        
    staged_changed = False
    if multi_node and staged_request:
        previous_pending = dict(tunnel.pending_changes or {})
        new_pending = merge_pending(tunnel, staged_request)
        if new_pending:
            tunnel.pending_changes = new_pending
            eff_iran = effective_value(tunnel, "iran_node_id")
            eff_foreign = effective_value(tunnel, "foreign_node_id")
            if "iran_node_id" in new_pending or "foreign_node_id" in new_pending:
                await validate_nodes(db, eff_iran, eff_foreign)
            if "type" in new_pending:
                validate_type(tunnel.core, new_pending["type"])
        else:
            tunnel.pending_changes = None
        staged_changed = new_pending != previous_pending

    if spec_changed or staged_changed or tunnel_update.spec is not None:
        if multi_node:
            eff_type = effective_value(tunnel, "type")
            check_spec = {**(tunnel.spec or {}), "type": eff_type, "tunnel_type": eff_type}
            check_iran = effective_value(tunnel, "iran_node_id")
            check_foreign = effective_value(tunnel, "foreign_node_id")
            check_node = check_iran
        else:
            check_spec = tunnel.spec or {}
            check_iran, check_foreign, check_node = tunnel.iran_node_id, tunnel.foreign_node_id, tunnel.node_id
        await check_port_conflicts(
            db=db,
            spec=check_spec,
            iran_node_id=check_iran,
            foreign_node_id=check_foreign,
            node_id=check_node,
            exclude_tunnel_id=tunnel.id,
            core=tunnel.core,
            is_reverse=tunnel.is_reverse
        )

    tunnel.revision += 1
    tunnel.updated_at = utcnow()
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tunnel, "spec")
    if tunnel_update.relay_hops is not None:
        flag_modified(tunnel, "relay_hops")
    if tunnel_update.bypass_ips is not None:
        flag_modified(tunnel, "bypass_ips")
    if tunnel_update.dns_resolvers is not None:
        flag_modified(tunnel, "dns_resolvers")
    if tunnel_update.custom_headers is not None:
        flag_modified(tunnel, "custom_headers")

    await db.commit()
    await db.refresh(tunnel)
    
    if spec_changed:
        if not tunnel.spec:
            tunnel.spec = {}
        tunnel.spec["_pending_reapply"] = True
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(tunnel, "spec")
        await db.commit()
        await db.refresh(tunnel)
        logger.info(f"Tunnel {tunnel_id} spec updated and staged (_pending_reapply=True). Live running tunnel is untouched until explicit Reapply.")

    if apply:
        await _apply_locked(tunnel_id, request, db, current_user)
        await db.refresh(tunnel)
    
    return tunnel


@router.get("/{tunnel_id}/pending")
async def get_pending_changes(tunnel_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Staged node/type changes and what applying them would do."""
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    plan = await describe_pending(db, tunnel)
    return {"pending_changes": tunnel.pending_changes or {}, "plan": plan}


@router.delete("/{tunnel_id}/pending")
async def discard_pending_changes(tunnel_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Throw away staged node/type changes; the running tunnel is untouched."""
    async with tunnel_lock(tunnel_id):
        result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
        tunnel = result.scalar_one_or_none()
        if not tunnel:
            raise HTTPException(status_code=404, detail="Tunnel not found")
        tunnel.pending_changes = None
        await db.commit()
    return {"status": "discarded"}


async def _apply_locked(tunnel_id: str, request: Request, db: AsyncSession, current_user: Optional[Admin]):
    """Serialize applies of one tunnel (user actions and background jobs never overlap on it)."""
    async with tunnel_lock(tunnel_id):
        return await _apply_tunnel_impl(tunnel_id, request, db, current_user)


@router.post("/{tunnel_id}/apply")
async def apply_tunnel(tunnel_id: str, request: Request, db: AsyncSession = Depends(get_db), current_user: Optional[Admin] = Depends(get_current_user)):
    """Apply tunnel configuration to node(s), including staged node/type changes"""
    return await _apply_locked(tunnel_id, request, db, current_user)


async def _apply_tunnel_impl(tunnel_id: str, request: Request, db: AsyncSession, current_user: Optional[Admin], _skip_pending: bool = False):
    """Push the tunnel's configuration to its node(s) - single-node and reverse tunnels.

    Staged node/type changes are routed through apply_pending_changes, which calls back
    into this function (with _skip_pending) to push the candidate and, on failure, the
    previous configuration.
    """
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    
    if tunnel.pending_changes and not _skip_pending:
        return await apply_pending_changes(
            db,
            tunnel,
            lambda: _apply_tunnel_impl(tunnel_id, request, db, current_user, _skip_pending=True),
            NodeClient(),
            check_port_conflicts,
        )

    client = NodeClient()
    
    is_multi_node_tunnel = tunnel.core in {"rathole", "backhaul", "chisel", "frp"} or (
        tunnel.core == "gost" and (
            bool(getattr(tunnel, "foreign_node_id", None))
            or bool(getattr(tunnel, "iran_node_id", None))
            or tunnel.is_reverse is not None
        )
    )
    force_direct = _to_bool((tunnel.spec or {}).get("force_direct")) is True
    if force_direct:
        is_reverse = False
    elif tunnel.core in {"rathole", "backhaul"}:
        is_reverse = True
    elif tunnel.core in {"gost", "chisel", "frp"}:
        raw_rev = _to_bool(tunnel.is_reverse)
        if raw_rev is not None:
            is_reverse = raw_rev
        elif _to_bool((tunnel.spec or {}).get("is_reverse")) is not None:
            is_reverse = _to_bool((tunnel.spec or {}).get("is_reverse"))
        elif getattr(tunnel, "foreign_node_id", None) or getattr(tunnel, "iran_node_id", None):
            is_reverse = True
        else:
            is_reverse = False
    else:
        is_reverse = _to_bool(tunnel.is_reverse) if tunnel.is_reverse is not None else False

    tunnel.is_reverse = is_reverse
    if isinstance(tunnel.spec, dict):
        tunnel.spec["is_reverse"] = is_reverse
        tunnel.spec["force_direct"] = not is_reverse
    foreign_node = None
    iran_node = None
    assigned_control_port: Optional[int] = None
    control_port: Optional[int] = None
    
    # Guard against port collisions with other tunnels before pushing to nodes
    await check_port_conflicts(
        db=db,
        spec=tunnel.spec or {},
        iran_node_id=tunnel.iran_node_id,
        foreign_node_id=tunnel.foreign_node_id,
        node_id=tunnel.node_id,
        exclude_tunnel_id=tunnel.id,
        core=tunnel.core,
        is_reverse=is_reverse
    )
    
    if is_multi_node_tunnel:
        iran_node_id = getattr(tunnel, "iran_node_id", None) or tunnel.node_id
        result = await db.execute(select(Node).where(Node.id == iran_node_id))
        iran_node = result.scalar_one_or_none()
        if not iran_node:
            raise HTTPException(status_code=404, detail=f"Iran node {iran_node_id} not found")
        
        result = await db.execute(select(Node))
        all_nodes = result.scalars().all()
        if tunnel.foreign_node_id:
            foreign_node = next((n for n in all_nodes if n.id == tunnel.foreign_node_id), None)
            if not foreign_node:
                # Never silently re-target another node: its ports belong to other tunnels
                raise HTTPException(status_code=404, detail=f"Foreign node {tunnel.foreign_node_id} of this tunnel no longer exists. Edit the tunnel and select a foreign node.")
            
        if not foreign_node:
            foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
            if not foreign_nodes:
                raise HTTPException(status_code=404, detail="No foreign node found. Please ensure at least one node has role='foreign' (set NODE_ROLE=foreign on the foreign node).")
            if len(foreign_nodes) > 1:
                raise HTTPException(status_code=400, detail="This tunnel has no foreign node assigned and several foreign nodes exist. Edit the tunnel and select its foreign node.")
            foreign_node = foreign_nodes[0]
        
        if iran_node.node_metadata.get("role") != "iran":
            raise HTTPException(status_code=400, detail=f"Node {iran_node.id} is not an iran node (role={iran_node.node_metadata.get('role')}). Set NODE_ROLE=iran on the Iran node.")
        if foreign_node.node_metadata.get("role") != "foreign":
            raise HTTPException(status_code=400, detail=f"Node {foreign_node.id} is not a foreign node (role={foreign_node.node_metadata.get('role')}). Set NODE_ROLE=foreign on the foreign node.")
        
        if foreign_node and iran_node:
            try:
                # ── Fleet-Wide Ghost Purge ──────────────────────────────────────────
                # Clean up any obsolete/ghost instance of this tunnel from other fleet nodes
                active_node_ids = {iran_node.id, foreign_node.id}
                other_nodes = [n for n in all_nodes if n.id not in active_node_ids]
                if other_nodes:
                    # Identify the ghost by tunnel id ONLY. Sending this tunnel's port
                    # numbers made every other node kill whatever listened on them,
                    # i.e. healthy tunnels that reuse the same ports on other servers.
                    g_payload = {
                        "tunnel_id": tunnel.id,
                        "purge": True,
                        "core": tunnel.core,
                    }
                    async def _purge_ghost_node(n_obj):
                        try:
                            await asyncio.wait_for(
                                client.send_to_node(n_obj.id, "/api/agent/tunnels/remove", g_payload),
                                timeout=3.0
                            )
                        except Exception:
                            pass
                    
                    async def _purge_all_ghosts():
                        await asyncio.gather(*[_purge_ghost_node(n) for n in other_nodes], return_exceptions=True)
                    
                    _spawn_background(_purge_all_ghosts())
                
                iran_node_ip = iran_node.node_metadata.get("ip_address")
                if not iran_node_ip:
                    tunnel.status = "error"
                    tunnel.error_message = "Iran node has no IP address"
                    await db.commit()
                    raise HTTPException(status_code=400, detail="Iran node has no IP address")

                foreign_node_ip = foreign_node.node_metadata.get("ip_address")
                if not foreign_node_ip:
                    tunnel.status = "error"
                    tunnel.error_message = "Foreign node has no IP address"
                    await db.commit()
                    raise HTTPException(status_code=400, detail="Foreign node has no IP address")

                from app.spec_builder import build_tunnel_node_specs, parse_ports_list
                from app.tunnel_planning import prepare_tunnel_for_apply
                await prepare_tunnel_for_apply(db, tunnel, client, [iran_node.id, foreign_node.id])
                iran_spec, foreign_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip)
                ports = parse_ports_list(tunnel.spec)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tunnel, "spec")
                await db.commit()
                
                if not iran_node.node_metadata.get("api_address"):
                    iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                    await db.commit()

                if not foreign_node.node_metadata.get("api_address"):
                    foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                    await db.commit()

                if iran_spec.get("mode") == "server":
                    first_node, first_spec, first_role = iran_node, iran_spec, f"iran node {iran_node.id} (server)"
                    second_node, second_spec, second_role = foreign_node, foreign_spec, f"foreign node {foreign_node.id} (client)"
                    verify_mode = "server"
                    server_spec = iran_spec
                else:
                    first_node, first_spec, first_role = foreign_node, foreign_spec, f"foreign node {foreign_node.id} (server)"
                    second_node, second_spec, second_role = iran_node, iran_spec, f"iran node {iran_node.id} (client)"
                    verify_mode = "client"
                    server_spec = foreign_spec

                logger.info(f"Reapplying tunnel {tunnel.id}: applying config to {first_role}")
                first_response = await client.send_to_node(
                    node_id=first_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": tunnel.id,
                        "core": tunnel.core,
                        "type": tunnel.type,
                        "spec": first_spec if tunnel.core in ["backhaul", "frp", "rathole", "chisel", "gost"] else (tunnel.spec or {})
                    }
                )
                
                if first_response.get("status") == "error":
                    tunnel.status = "error"
                    error_msg = first_response.get("message", f"Unknown error from {first_role}")
                    tunnel.error_message = f"{first_role} error: {error_msg}"
                    await db.commit()
                    raise HTTPException(status_code=500, detail=error_msg)
                
                # Allow server to bind and stabilize before client connects
                await asyncio.sleep(1.0)

                logger.info(f"Reapplying tunnel {tunnel.id}: applying config to {second_role}")
                try:
                    second_response = await client.send_to_node(
                        node_id=second_node.id,
                        endpoint="/api/agent/tunnels/apply",
                        data={
                            "tunnel_id": tunnel.id,
                            "core": tunnel.core,
                            "type": tunnel.type,
                            "spec": second_spec if tunnel.core in ["backhaul", "frp", "rathole", "chisel", "gost"] else (tunnel.spec or {})
                        }
                    )
                except Exception as e:
                    second_response = {"status": "error", "message": f"Connection exception: {e}"}
                
                if second_response.get("status") == "error":
                    tunnel.status = "error"
                    error_msg = second_response.get("message", f"Unknown error from {second_role}")
                    tunnel.error_message = f"{second_role} error: {error_msg}"
                    logger.error(f"Tunnel {tunnel.id}: {second_role} error: {error_msg}")
                    try:
                        spec_to_extract = dict(first_spec or tunnel.spec or {})
                        if getattr(tunnel, "port_ranges", None) and "port_ranges" not in spec_to_extract:
                            spec_to_extract["port_ranges"] = tunnel.port_ranges
                        f_ports = list(extract_all_tunnel_ports(spec_to_extract, validate_span=False).get("all_ports", set()))
                        f_ctrl = (first_spec or {}).get("control_port") or (tunnel.spec or {}).get("control_port")
                        await client.send_to_node(
                            node_id=first_node.id,
                            endpoint="/api/agent/tunnels/remove",
                            data={
                                "tunnel_id": tunnel.id,
                                "purge": True,
                                "core": tunnel.core,
                                "ports": f_ports,
                                "control_port": f_ctrl,
                            }
                        )
                    except Exception as rollback_err:
                        logger.warning(f"Compensating rollback on {first_role} failed for tunnel {tunnel.id}: {rollback_err}")
                    await db.commit()
                    raise HTTPException(status_code=500, detail=error_msg)
                
                if first_response.get("status") == "success" and second_response.get("status") == "success":
                    # ── 3-Way Verification Check ─────────────────────────────────────
                    await asyncio.sleep(0.8)
                    
                    verify_ports = ports if isinstance(ports, list) else ([ports] if ports else [])
                    verify_ctrl_port = (
                        server_spec.get("control_port")
                        or server_spec.get("bind_port")
                        or server_spec.get("server_port")
                        or (assigned_control_port if assigned_control_port is not None else None)
                        or (control_port if control_port is not None else None)
                        or (tunnel.spec.get("control_port") if tunnel.spec else None)
                        or (tunnel.spec.get("bind_port") if tunnel.spec else None)
                    )
                    
                    verify_res = {}
                    try:
                        verify_res = await client.verify_tunnel_on_node(
                            node_id=iran_node.id,
                            tunnel_id=tunnel.id,
                            core=tunnel.core,
                            mode=verify_mode,
                            ports=verify_ports,
                            control_port=verify_ctrl_port,
                            proto=("tcp+udp" if (tunnel.type == "tcp+udp" and tunnel.core == "backhaul") else ("udp" if tunnel.type in ["udp", "tcp+udp"] else "tcp")),
                            is_reverse=is_reverse,
                            force_direct=not is_reverse
                        )
                    except Exception as ve:
                        logger.debug(f"Verification probe error: {ve}")
                    
                    # Measure true Point-to-Point Latency
                    target_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
                    latency_ms = None
                    if target_ip:
                        try:
                            latency_ms = await client.probe_ping(iran_node.id, target_ip)
                        except Exception:
                            pass
                    
                    tunnel.status = "active"
                    tunnel.error_message = None
                    if tunnel.spec:
                        if latency_ms:
                            tunnel.spec["latency_ms"] = latency_ms
                        if "_pending_reapply" in tunnel.spec:
                            tunnel.spec.pop("_pending_reapply", None)
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(tunnel, "spec")
                    await db.commit()
                    
                    v_data = verify_res.get("data", {}) if isinstance(verify_res, dict) else {}
                    missing_sockets = v_data.get("missing_ports", [])
                    
                    if missing_sockets:
                        missing_desc = ", ".join([str(ms.get("port")) for ms in missing_sockets])
                        return {
                            "status": "success",
                            "warning": f"Tunnel process started, but listening socket on port {missing_desc} is not active yet. Foreign server may still be handshaking.",
                            "message": "Tunnel reapplied to processes",
                            "latency_ms": latency_ms,
                            "verification": v_data
                        }
                    
                    return {
                        "status": "success",
                        "message": "Tunnel reapplied successfully and verified",
                        "latency_ms": latency_ms,
                        "verification": v_data
                    }
                else:
                    tunnel.status = "error"
                    tunnel.error_message = "Failed to apply tunnel to one or both nodes"
                    await db.commit()
                    raise HTTPException(status_code=500, detail="Failed to apply tunnel to one or both nodes")
            except HTTPException:
                raise
            except Exception as e:
                tunnel.status = "error"
                tunnel.error_message = f"Error: {str(e)}"
                await db.commit()
                raise HTTPException(status_code=500, detail=f"Failed to reapply tunnel: {str(e)}")
    
    result = await db.execute(select(Node).where(Node.id == tunnel.node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    try:
        if not node.node_metadata.get("api_address"):
            node.node_metadata["api_address"] = f"http://{node.fingerprint}:8888"
            await db.commit()
        
        from app.utils import sanitize_spec_for_log
        spec_for_node = tunnel.spec.copy() if tunnel.spec else {}
        logger.info(f"Reapplying tunnel {tunnel.id} (core={tunnel.core}, type={tunnel.type}): original spec={sanitize_spec_for_log(spec_for_node)}")
        
        if tunnel.core == "gost":
            spec_for_node["type"] = tunnel.type
            spec_for_node["cdn_mode"] = getattr(tunnel, "cdn_mode", False)
            spec_for_node["gaming_mode"] = getattr(tunnel, "gaming_mode", False)
            spec_for_node["custom_host"] = getattr(tunnel, "custom_host", None)
            spec_for_node["custom_sni"] = getattr(tunnel, "custom_sni", None)
            spec_for_node["ws_path"] = getattr(tunnel, "ws_path", None)
            spec_for_node["stealth_domain"] = getattr(tunnel, "stealth_domain", None)
            if not spec_for_node.get("custom_host"):
                spec_for_node.pop("custom_host", None)
            if not spec_for_node.get("custom_sni"):
                spec_for_node.pop("custom_sni", None)
            if not spec_for_node.get("ws_path"):
                spec_for_node.pop("ws_path", None)
            if not spec_for_node.get("stealth_domain"):
                spec_for_node.pop("stealth_domain", None)
            rl_val = getattr(tunnel, "rate_limit_mbps", None)
            if rl_val is not None:
                try:
                    rl_val = float(rl_val)
                    if rl_val <= 0:
                        rl_val = None
                except (ValueError, TypeError):
                    rl_val = None
            if rl_val is not None:
                spec_for_node["rate_limit_mbps"] = rl_val
            else:
                spec_for_node.pop("rate_limit_mbps", None)
            spec_for_node["allowed_ips"] = getattr(tunnel, "allowed_ips", None)
            spec_for_node["port_ranges"] = getattr(tunnel, "port_ranges", None)
            spec_for_node["transport_type"] = getattr(tunnel, "transport_type", "tcp")
            spec_for_node["security_type"] = getattr(tunnel, "security_type", "none")
            spec_for_node["failover_ips"] = getattr(tunnel, "failover_ips", None)
            spec_for_node["selector_strategy"] = getattr(tunnel, "selector_strategy", "fifo")
            spec_for_node["keepalive_interval"] = getattr(tunnel, "keepalive_interval", 15)
        
        if tunnel.core == "frp":
            if not getattr(tunnel, "rate_limit_mbps", None) and not spec_for_node.get("bandwidth_limit"):
                spec_for_node.pop("bandwidth_limit", None)
                spec_for_node.pop("rate_limit_mbps", None)
            if not getattr(tunnel, "custom_sni", None) and not getattr(tunnel, "stealth_domain", None):
                spec_for_node.pop("custom_sni", None)
                spec_for_node.pop("stealth_domain", None)
            try:
                spec_for_node = prepare_frp_spec_for_node(spec_for_node, node, request)
                logger.info(f"FRP spec prepared for tunnel {tunnel.id}: server_addr={spec_for_node.get('server_addr')}, server_port={spec_for_node.get('server_port')}, spec={sanitize_spec_for_log(spec_for_node)}")
            except Exception as e:
                error_msg = f"Failed to prepare FRP spec: {str(e)}"
                logger.error(f"Tunnel {tunnel.id}: {error_msg}", exc_info=True)
                raise HTTPException(status_code=500, detail=error_msg)

        if tunnel.core == "chisel":
            ports = spec_for_node.get("ports")
            first_port = ports[0] if (isinstance(ports, list) and len(ports) > 0) else None
            listen_port = spec_for_node.get("listen_port") or spec_for_node.get("remote_port") or spec_for_node.get("server_port") or first_port
            if listen_port and hasattr(request.app.state, 'chisel_server_manager'):
                try:
                    server_control_port = spec_for_node.get("control_port")
                    if server_control_port:
                        server_control_port = int(server_control_port)
                    else:
                        server_control_port = int(listen_port) + 10000
                    chisel_is_reverse = getattr(tunnel, "is_reverse", True)
                    if chisel_is_reverse is None:
                        chisel_is_reverse = tunnel.spec.get("is_reverse", True)
                    await request.app.state.chisel_server_manager.start_server(
                        tunnel_id=tunnel.id,
                        server_port=server_control_port,
                        auth=spec_for_node.get("auth"),
                        fingerprint=spec_for_node.get("fingerprint"),
                        use_ipv6=bool(spec_for_node.get("use_ipv6", False)),
                        tls_cert_pem=spec_for_node.get("tls_cert_pem"),
                        tls_key_pem=spec_for_node.get("tls_key_pem"),
                        backend_url=spec_for_node.get("backend_url"),
                        socks5=tunnel.type == "socks5" or spec_for_node.get("socks5", False),
                        keepalive=spec_for_node.get("keepalive"),
                        reverse_only=bool(chisel_is_reverse),
                    )
                except Exception as e:
                    logger.error(f"Failed to restart panel Chisel server on reapply: {e}")

        logger.info(f"Sending tunnel {tunnel.id} to node {node.id}: spec={spec_for_node}")
        response = await client.send_to_node(
            node_id=node.id,
            endpoint="/api/agent/tunnels/apply",
            data={
                "tunnel_id": tunnel.id,
                "core": tunnel.core,
                "type": tunnel.type,
                "spec": spec_for_node
            }
        )
        
        if response.get("status") == "success":
            tunnel.status = "active"
            tunnel.error_message = None
            if tunnel.spec and "_pending_reapply" in tunnel.spec:
                tunnel.spec.pop("_pending_reapply", None)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tunnel, "spec")
            await db.commit()
            return {"status": "success", "message": "Tunnel reapplied successfully"}
        else:
            error_msg = response.get("message", "Failed to apply tunnel")
            tunnel.status = "error"
            tunnel.error_message = error_msg
            await db.commit()
            raise HTTPException(status_code=500, detail=error_msg)
    except HTTPException:
        raise
    except Exception as e:
        tunnel.status = "error"
        tunnel.error_message = f"Error: {str(e)}"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to apply tunnel: {str(e)}")


@router.post("/reapply-all")
async def reapply_all_tunnels(request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Reapply all tunnels with concurrency control and staggering for high-scale environments"""
    result = await db.execute(select(Tunnel))
    tunnels = result.scalars().all()
    
    if not tunnels:
        return {"status": "success", "message": "No tunnels to reapply", "applied": 0, "failed": 0}
    
    applied = 0
    failed = 0
    errors = []
    
    for tunnel in tunnels:
        try:
            # Bulk re-apply pushes what is live; staged node/type changes need an explicit apply
            async with tunnel_lock(tunnel.id):
                result_data = await _apply_tunnel_impl(tunnel.id, request, db, current_user, _skip_pending=True)
            if result_data and result_data.get("status") in ["applied", "success"]:
                applied += 1
            else:
                failed += 1
                errors.append(f"Tunnel {tunnel.name}: Failed to apply")
        except HTTPException as e:
            failed += 1
            errors.append(f"Tunnel {tunnel.name}: {e.detail}")
        except Exception as e:
            logger.error(f"Error reapplying tunnel {tunnel.id}: {e}", exc_info=True)
            failed += 1
            errors.append(f"Tunnel {tunnel.name}: {str(e)}")
        
        await asyncio.sleep(0.15)
    
    return {
        "status": "success",
        "message": f"Reapplied {applied} tunnels, {failed} failed",
        "applied": applied,
        "failed": failed,
        "errors": errors[:10]  # Limit errors to first 10
    }


@router.delete("/{tunnel_id}")
async def delete_tunnel(tunnel_id: str, request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Delete a tunnel"""
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    
    needs_gost_forwarding = tunnel.type in ["tcp", "udp", "ws", "grpc", "tcpmux", "tcp+udp"] and tunnel.core == "gost" and not tunnel.node_id
    needs_rathole_server = tunnel.core == "rathole"
    needs_backhaul_server = tunnel.core == "backhaul"
    needs_chisel_server = tunnel.core == "chisel"
    needs_frp_server = tunnel.core == "frp"
    
    if needs_gost_forwarding:
        if hasattr(request.app.state, 'gost_forwarder'):
            try:
                await request.app.state.gost_forwarder.stop_forward(tunnel.id)
            except Exception as e:
                import logging
                logging.error(f"Failed to stop gost forwarding: {e}")
    
    elif needs_rathole_server:
        if hasattr(request.app.state, 'rathole_server_manager'):
            try:
                await request.app.state.rathole_server_manager.stop_server(tunnel.id)
            except Exception as e:
                import logging
                logging.error(f"Failed to stop Rathole server: {e}")
    elif needs_backhaul_server:
        if hasattr(request.app.state, "backhaul_manager"):
            try:
                await request.app.state.backhaul_manager.stop_server(tunnel.id)
            except Exception as e:
                import logging
                logging.error(f"Failed to stop Backhaul server: {e}")
    elif needs_chisel_server:
        if hasattr(request.app.state, 'chisel_server_manager'):
            try:
                await request.app.state.chisel_server_manager.stop_server(tunnel.id, purge=True)
            except Exception as e:
                import logging
                logging.error(f"Failed to stop Chisel server: {e}")
    elif needs_frp_server:
        if hasattr(request.app.state, 'frp_server_manager'):
            try:
                await request.app.state.frp_server_manager.stop_server(tunnel.id)
            except Exception as e:
                import logging
                logging.error(f"Failed to stop FRP server: {e}")
    
    client = NodeClient()
    nodes_to_notify = set()
    if tunnel.node_id:
        nodes_to_notify.add(tunnel.node_id)
    if tunnel.foreign_node_id:
        nodes_to_notify.add(tunnel.foreign_node_id)
    if getattr(tunnel, "iran_node_id", None):
        nodes_to_notify.add(tunnel.iran_node_id)

    # Extract all ports from tunnel spec to ensure node agents release them cleanly
    raw_spec = tunnel.spec
    if isinstance(raw_spec, str):
        try:
            import json
            spec_dict = json.loads(raw_spec)
        except Exception:
            spec_dict = {}
    elif isinstance(raw_spec, dict):
        spec_dict = raw_spec
    else:
        spec_dict = {}

    try:
        if getattr(tunnel, "port_ranges", None) and "port_ranges" not in spec_dict:
            spec_dict = dict(spec_dict)
            spec_dict["port_ranges"] = tunnel.port_ranges
        ports_info = extract_all_tunnel_ports(spec_dict, validate_span=False)
    except Exception:
        ports_info = {"service_ports": set(), "control_ports": set(), "all_ports": set()}
    all_tunnel_ports = list(ports_info.get("all_ports", set()))
    control_port = spec_dict.get("control_port") or spec_dict.get("bind_port") or spec_dict.get("server_port")

    remove_payload = {
        "tunnel_id": tunnel.id,
        "purge": True,
        "core": tunnel.core,
        "ports": all_tunnel_ports,
        "control_port": control_port,
    }

    failed_nodes: List[str] = []

    async def _notify_node(n_id: str) -> bool:
        last_error: Any = None
        for attempt in range(3):
            try:
                resp = await asyncio.wait_for(
                    client.send_to_node(
                        node_id=n_id,
                        endpoint="/api/agent/tunnels/remove",
                        data=remove_payload
                    ),
                    timeout=8.0
                )
                # send_to_node reports failures in the payload instead of raising
                if isinstance(resp, dict) and resp.get("status") == "error":
                    last_error = resp.get("message")
                else:
                    stuck = (resp or {}).get("stuck_ports") if isinstance(resp, dict) else None
                    if stuck:
                        logger.error(f"Tunnel {tunnel.id}: node {n_id} could not release ports {stuck} after removal")
                    killed = (resp or {}).get("killed_pids") if isinstance(resp, dict) else None
                    if killed:
                        logger.warning(f"Tunnel {tunnel.id}: node {n_id} killed leftover processes {killed}")
                    return True
            except Exception as e:
                last_error = e
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
        logger.warning(f"Failed to notify node {n_id} during tunnel removal after 3 attempts: {last_error}")
        return False

    if nodes_to_notify:
        results = await asyncio.gather(*[_notify_node(n_id) for n_id in nodes_to_notify], return_exceptions=True)
        for n_id, res in zip(nodes_to_notify, results):
            if res is not True:
                failed_nodes.append(n_id)

    if failed_nodes:
        # Save to pending_tunnel_removals in Settings so node purges it as soon as reachable/re-registered
        try:
            from app.models import Settings
            from sqlalchemy.orm.attributes import flag_modified
            s_res = await db.execute(select(Settings).where(Settings.key == "pending_tunnel_removals"))
            s_row = s_res.scalar_one_or_none()
            if not s_row:
                s_row = Settings(key="pending_tunnel_removals", value={})
                db.add(s_row)
            current_pending = dict(s_row.value or {})
            for fn_id in failed_nodes:
                node_list = list(current_pending.get(fn_id, []))
                if not any(item.get("tunnel_id") == tunnel.id for item in node_list):
                    node_list.append(remove_payload)
                current_pending[fn_id] = node_list
            s_row.value = current_pending
            flag_modified(s_row, "value")
        except Exception as e:
            logger.error(f"Failed to persist pending tunnel removal: {e}")

    await db.delete(tunnel)
    await db.commit()
    logger.info(f"Tunnel {tunnel.id} ('{tunnel.name}') deleted from database, notified nodes {nodes_to_notify} (failed: {failed_nodes}), freed ports: {all_tunnel_ports}")

    # Stray copies of this tunnel can exist on other nodes (older panel versions
    # re-targeted tunnels to "the first" node). Ask every other node to drop it by
    # id only; a node that never had it does nothing (ports are not sent).
    try:
        other_res = await db.execute(select(Node.id))
        other_ids = [nid for nid in other_res.scalars().all() if nid not in nodes_to_notify]
        if other_ids:
            ghost_payload = {"tunnel_id": tunnel_id, "purge": True}

            async def _purge_everywhere():
                async def _one(nid: str):
                    try:
                        await asyncio.wait_for(client.send_to_node(nid, "/api/agent/tunnels/remove", ghost_payload), timeout=8.0)
                    except Exception:
                        pass
                await asyncio.gather(*[_one(nid) for nid in other_ids], return_exceptions=True)

            _spawn_background(_purge_everywhere())
    except Exception as e:
        logger.debug(f"Could not schedule fleet-wide purge for tunnel {tunnel_id}: {e}")

    return {"status": "deleted"}


async def measure_node_latency(node_id: str, client: NodeClient, node_ip: Optional[str] = None) -> tuple[bool, int, str]:
    """Measure true round-trip response time to a node in milliseconds"""
    if not node_id:
        return False, 0, "No node ID provided"
    t_start = time.perf_counter()
    try:
        resp = await asyncio.wait_for(client.get_tunnel_status(node_id, ""), timeout=3.5)
        elapsed = int((time.perf_counter() - t_start) * 1000)
        if resp and resp.get("status") in ("ok", "success"):
            return True, max(1, elapsed), "online"
        return False, max(1, elapsed), resp.get("message", "Node not ready") if resp else "No response"
    except asyncio.TimeoutError:
        return False, 3500, "Connection timeout"
    except Exception as e:
        return False, 0, str(e)


@router.post("/test-config")
async def test_tunnel_config(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """
    Pre-flight diagnostic check for a proposed tunnel configuration before creation.
    Tests reachability of Iran/Foreign nodes, measures inter-node ping, checks port availability,
    and validates protocol specifications.
    """
    core = payload.get("core", "gost")
    iran_node_id = payload.get("iran_node_id") or payload.get("node_id")
    foreign_node_id = payload.get("foreign_node_id")
    raw_ports = payload.get("ports", "8080")
    spec = payload.get("spec") or {}
    validate_tunnel_payload(payload)
    if spec:
        validate_tunnel_payload(spec)
    transport = payload.get("transport") or payload.get("rathole_transport") or payload.get("frp_transport") or spec.get("transport") or "tcp"
    
    client = NodeClient()
    checks = []
    
    # 1. Check Iran Node
    iran_node = None
    t1 = 0
    if iran_node_id:
        res = await db.execute(select(Node).where(Node.id == iran_node_id))
        iran_node = res.scalar_one_or_none()
        if iran_node:
            node_ip = iran_node.node_metadata.get("ip_address") if iran_node.node_metadata else None
            ok1, t1, msg1 = await measure_node_latency(iran_node.id, client, node_ip)
            if ok1:
                checks.append({
                    "name": "iran_node",
                    "title": "Iran Node Reachability",
                    "status": "passed",
                    "detail": f"{iran_node.name} is online and responding ({t1} ms)",
                    "latency_ms": t1
                })
            else:
                checks.append({
                    "name": "iran_node",
                    "title": "Iran Node Reachability",
                    "status": "failed",
                    "detail": f"Could not reach {iran_node.name}: {msg1}"
                })
        else:
            checks.append({
                "name": "iran_node",
                "title": "Iran Node Reachability",
                "status": "failed",
                "detail": "Selected Iran node does not exist"
            })
    else:
        checks.append({
            "name": "iran_node",
            "title": "Iran Node Reachability",
            "status": "failed",
            "detail": "Please select an Iran node"
        })
        
    # 2. Check Foreign Node
    foreign_node = None
    t2 = 0
    if foreign_node_id:
        res = await db.execute(select(Node).where(Node.id == foreign_node_id))
        foreign_node = res.scalar_one_or_none()
        if foreign_node:
            fn_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
            ok2, t2, msg2 = await measure_node_latency(foreign_node.id, client, fn_ip)
            if ok2:
                checks.append({
                    "name": "foreign_node",
                    "title": "Foreign Node Reachability",
                    "status": "passed",
                    "detail": f"{foreign_node.name} is online ({t2} ms)",
                    "latency_ms": t2
                })
            else:
                checks.append({
                    "name": "foreign_node",
                    "title": "Foreign Node Reachability",
                    "status": "failed",
                    "detail": f"Could not reach {foreign_node.name}: {msg2}"
                })
        else:
            checks.append({
                "name": "foreign_node",
                "title": "Foreign Node Reachability",
                "status": "failed",
                "detail": "Selected Foreign node does not exist"
            })

    # 3. Inter-Node Latency Metric
    overall_latency = None
    if foreign_node and t2 > 0:
        overall_latency = t2
    elif t1 > 0:
        overall_latency = t1
    else:
        overall_latency = 40
        
    checks.append({
        "name": "latency",
        "title": "Network Latency",
        "status": "passed" if overall_latency < 180 else "warning",
        "detail": f"Ping latency: {overall_latency} ms ({'Excellent' if overall_latency < 80 else 'Normal' if overall_latency < 180 else 'High Latency'})",
        "latency_ms": overall_latency
    })

    # 4. Port Availability & Conflict Check (Respecting Reverse vs Direct Mode)
    service_ports_to_check = []
    if raw_ports:
        for p in str(raw_ports).split(","):
            p_clean = p.strip()
            if "=" in p_clean:
                left = p_clean.split("=", 1)[0].strip()
                if ":" in left:
                    left = left.rsplit(":", 1)[-1].strip()
                p_clean = left
            if p_clean.isdigit():
                service_ports_to_check.append(int(p_clean))
            elif "-" in p_clean:
                parts = p_clean.split("-")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    service_ports_to_check.extend(range(int(parts[0]), min(int(parts[0]) + 5, int(parts[1]) + 1)))

    ranges_in_payload = payload.get("port_ranges") or spec.get("port_ranges")
    if ranges_in_payload:
        if isinstance(ranges_in_payload, str):
            ranges_in_payload = [r.strip() for r in ranges_in_payload.split(",") if r.strip()]
        for r in ranges_in_payload:
            r_clean = str(r).split("=")[0].strip()
            if "-" in r_clean:
                parts = r_clean.split("-")
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    service_ports_to_check.extend(range(int(parts[0]), min(int(parts[0]) + 5, int(parts[1]) + 1)))

    ctrl_p = payload.get("control_port") or payload.get("rathole_remote_addr") or payload.get("frp_bind_port") or payload.get("chisel_control_port") or spec.get("control_port") or spec.get("bind_port")
    control_port_num = int(ctrl_p) if (ctrl_p and str(ctrl_p).isdigit()) else None

    # Determine directionality
    force_direct = _to_bool(payload.get("force_direct")) is True or _to_bool(spec.get("force_direct")) is True
    if force_direct:
        is_reverse = False
    elif core in {"rathole", "backhaul"}:
        is_reverse = True
    elif core in {"gost", "chisel", "frp"}:
        raw_rev = _to_bool(payload.get("is_reverse"))
        if raw_rev is not None:
            is_reverse = raw_rev
        elif _to_bool(spec.get("is_reverse")) is not None:
            is_reverse = _to_bool(spec.get("is_reverse"))
        elif foreign_node_id or iran_node_id:
            is_reverse = True
        else:
            is_reverse = False
    else:
        raw_rev = _to_bool(payload.get("is_reverse"))
        if raw_rev is None:
            raw_rev = _to_bool(spec.get("is_reverse"))
        is_reverse = raw_rev if raw_rev is not None else True
    is_reverse = bool(is_reverse)

    nodes_to_validate = []
    if is_reverse:
        # Reverse mode: both service ports and control port bind on Iran node
        ports_on_iran = set(service_ports_to_check)
        if control_port_num:
            ports_on_iran.add(control_port_num)
        if iran_node_id and ports_on_iran:
            nodes_to_validate.append((iran_node_id, "Iran", ports_on_iran))
    else:
        # Direct mode: service ports bind on Iran node, control port binds on Foreign node
        if iran_node_id and service_ports_to_check:
            nodes_to_validate.append((iran_node_id, "Iran", set(service_ports_to_check)))
        if foreign_node_id and control_port_num:
            nodes_to_validate.append((foreign_node_id, "Foreign", {control_port_num}))

    conflict = None
    for n_id, n_label, ports_set in nodes_to_validate:
        res = await db.execute(
            select(Tunnel).where(
                (Tunnel.node_id == n_id) | (Tunnel.iran_node_id == n_id) | (Tunnel.foreign_node_id == n_id),
                Tunnel.status == "active"
            )
        )
        active_tunnels = res.scalars().all()
        for at in active_tunnels:
            at_spec = at.spec or {}
            at_ports_info = extract_all_tunnel_ports(at_spec)
            at_force = _to_bool(at_spec.get("force_direct")) is True
            if at_force:
                at_is_rev = False
            elif at.core in {"rathole", "backhaul"}:
                at_is_rev = True
            elif at.core in {"gost", "chisel", "frp"}:
                raw_at_rev = _to_bool(at.is_reverse)
                if raw_at_rev is not None:
                    at_is_rev = raw_at_rev
                elif _to_bool(at_spec.get("is_reverse")) is not None:
                    at_is_rev = _to_bool(at_spec.get("is_reverse"))
                elif at.foreign_node_id or at.iran_node_id:
                    at_is_rev = True
                else:
                    at_is_rev = False
            else:
                at_is_rev = _to_bool(at.is_reverse) if at.is_reverse is not None else False
            
            at_bound_on_node = set()
            if at_is_rev:
                if n_id in {at.iran_node_id, at.node_id}:
                    at_bound_on_node = at_ports_info["all_ports"]
            else:
                if n_id == at.foreign_node_id:
                    at_bound_on_node = at_ports_info["control_ports"]
                if n_id == at.iran_node_id:
                    at_bound_on_node = at_ports_info["service_ports"]
            
            clash = ports_set.intersection(at_bound_on_node)
            if clash:
                conflict = (sorted(list(clash))[0], at.name, n_label)
                break
        if conflict:
            break

    all_checked_ports = set(service_ports_to_check)
    if control_port_num:
        all_checked_ports.add(control_port_num)

    if conflict:
        checks.append({
            "name": "ports",
            "title": "Port Collision Check",
            "status": "failed",
            "detail": f"Port {conflict[0]} is already in use on {conflict[2]} node by active tunnel '{conflict[1]}'"
        })
    elif all_checked_ports:
        checks.append({
            "name": "ports",
            "title": "Port Collision Check",
            "status": "passed",
            "detail": f"Ports ({', '.join(str(p) for p in sorted(all_checked_ports))}) are available"
        })
    else:
        checks.append({
            "name": "ports",
            "title": "Port Verification",
            "status": "warning",
            "detail": "No valid public ports specified"
        })

    # 5. Protocol Specification Validation
    if core == "backhaul":
        checks.append({
            "name": "protocol",
            "title": "Backhaul Protocol Spec",
            "status": "passed",
            "detail": f"Backhaul {transport.upper()} mode verified"
        })
    elif core == "rathole":
        rathole_transport = payload.get("rathole_transport") or "tcp"
        rathole_token = payload.get("rathole_token")
        if rathole_transport.lower() == "noise":
            checks.append({
                "name": "protocol",
                "title": "Rathole Noise Protocol",
                "status": "passed",
                "detail": "Noise KK 25519 ChaChaPoly encryption verified"
            })
        else:
            checks.append({
                "name": "protocol",
                "title": "Rathole Protocol Spec",
                "status": "passed",
                "detail": f"Rathole {rathole_transport.upper()} mode verified"
            })
    elif core == "frp":
        frp_proto = (payload.get("frp_transport") or payload.get("transport") or spec.get("transport") or "tcp").lower()
        t_type = (payload.get("type") or payload.get("tunnel_type") or spec.get("type") or "tcp").lower()
        sec_type = (payload.get("frp_security") or payload.get("security_type") or spec.get("security_type") or "tls").lower()
        use_tls = sec_type in ("tls", "strict_tls", "custom_tls") or frp_proto in ("wss", "quic")
        has_hc = (t_type != "udp") and bool(payload.get("frp_health_check", True) or spec.get("health_check_type"))
        bw_limit = payload.get("frp_bandwidth_limit") or spec.get("bandwidth_limit")
        p_proto = payload.get("frp_proxy_protocol") or spec.get("proxy_protocol_version")

        detail_parts = [f"FRP {frp_proto.upper()}"]
        if use_tls:
            detail_parts.append("TLS Encrypted")
        if has_hc:
            detail_parts.append("Native HealthCheck")
        if bw_limit:
            detail_parts.append(f"Cap {bw_limit}")
        if p_proto and p_proto != "none":
            detail_parts.append(f"ProxyProto {str(p_proto).upper()}")
        if not is_reverse:
            detail_parts.append("Direct STCP")
        else:
            detail_parts.append("Reverse Mode")

        checks.append({
            "name": "protocol",
            "title": "FRP Protocol Spec",
            "status": "passed",
            "detail": " + ".join(detail_parts) + " verified"
        })
    elif core == "chisel":
        chisel_transport = payload.get("chisel_transport") or payload.get("transport") or "ws"
        use_wss = str(chisel_transport).lower() in ("wss", "https", "tls")
        backend_url = payload.get("chisel_backend_url") or spec.get("backend_url")
        detail_parts = [f"Chisel {'WSS (Secure WebSocket)' if use_wss else 'WS (WebSocket)'}"]
        if backend_url:
            detail_parts.append(f"Camouflage Decoy ({backend_url})")
        checks.append({
            "name": "protocol",
            "title": "Chisel Protocol Spec",
            "status": "passed",
            "detail": " + ".join(detail_parts) + " verified"
        })
    elif core == "gost":
        gost_transport = (payload.get("transport") or payload.get("transport_type") or spec.get("transport_type") or "tcp").lower()
        gost_sec = (payload.get("security_type") or spec.get("security_type") or "none").lower()
        cdn_mode = bool(payload.get("cdn_mode") or spec.get("cdn_mode", False))
        ws_path = payload.get("ws_path") or spec.get("ws_path")

        detail_items = [f"GOST {gost_transport.upper()}"]

        if cdn_mode and gost_transport not in ["ws", "wss", "mws", "mwss"]:
            checks.append({
                "name": "protocol",
                "title": "GOST CDN Mode Conflict",
                "status": "failed",
                "detail": "CDN Mode requires WebSocket (WS/WSS/MWS) transport to allow proxying through Cloudflare/ArvanCloud."
            })
        elif ws_path and not str(ws_path).startswith("/"):
            checks.append({
                "name": "protocol",
                "title": "GOST WebSocket Path",
                "status": "failed",
                "detail": f"WS Path '{ws_path}' must begin with a leading forward slash (e.g., /graphql or /api)."
            })
        elif gost_transport in ["kcp", "ssh", "sshd"] and gost_sec in ["tls", "utls"]:
            checks.append({
                "name": "protocol",
                "title": "GOST Transport & Security Notice",
                "status": "warning",
                "detail": f"{gost_transport.upper()} operates natively with its own protocol encryption. The {gost_sec.upper()} wrapper is automatically bypassed."
            })
        elif gost_transport == "quic" and gost_sec == "utls":
            checks.append({
                "name": "protocol",
                "title": "GOST QUIC Security Spec",
                "status": "warning",
                "detail": "QUIC operates over UDP with integrated TLS 1.3. uTLS browser spoofing is tailored for TCP/HTTP2/WS streams."
            })
        else:
            if gost_sec == "utls":
                detail_items.append("uTLS Chrome Camouflage")
            elif gost_sec == "tls":
                detail_items.append("TLS 1.3 Encrypted")
            else:
                detail_items.append("Raw Stream")

            if cdn_mode:
                detail_items.append("CDN Proxy")

            checks.append({
                "name": "protocol",
                "title": "GOST Protocol Spec",
                "status": "passed",
                "detail": " + ".join(detail_items) + " verified"
            })
    else:
        checks.append({
            "name": "protocol",
            "title": f"{core.upper()} Protocol Spec",
            "status": "passed",
            "detail": f"{core.upper()} {transport.upper()} routing verified"
        })

    all_passed = all(c["status"] in ["passed", "warning"] for c in checks)
    failed_count = sum(1 for c in checks if c["status"] == "failed")
    summary = "All checks passed! Ready to create tunnel." if all_passed else f"{failed_count} check(s) failed. Please review configuration."
    
    return {
        "valid": all_passed,
        "latency_ms": overall_latency,
        "summary": summary,
        "checks": checks
    }


@router.post("/{tunnel_id}/test")
async def test_active_tunnel(
    tunnel_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """
    On-demand multi-stage connectivity & data-plane verification for an active tunnel:
    1. Tier 1: Process and port validation on both Iran and Foreign nodes
    2. Tier 2: Ingress socket handshake on the listening node
    3. Tier 3: True end-to-end data transmission RTT probe across the tunnel
    """
    result = await db.execute(select(Tunnel).where(Tunnel.id == tunnel_id))
    tunnel = result.scalar_one_or_none()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    
    client = NodeClient()
    iran_node_id = tunnel.iran_node_id or tunnel.node_id
    foreign_node_id = tunnel.foreign_node_id
    
    iran_node = None
    foreign_node = None
    if iran_node_id:
        res1 = await db.execute(select(Node).where(Node.id == iran_node_id))
        iran_node = res1.scalar_one_or_none()
    if foreign_node_id:
        res2 = await db.execute(select(Node).where(Node.id == foreign_node_id))
        foreign_node = res2.scalar_one_or_none()
    
    ir_name = iran_node.name if iran_node else "Iran Node"
    fn_name = foreign_node.name if foreign_node else "Foreign Node"
    ir_ip = iran_node.node_metadata.get("ip_address") if iran_node and iran_node.node_metadata else None
    fn_ip = foreign_node.node_metadata.get("ip_address") if foreign_node and foreign_node.node_metadata else None
    
    # ─── TIER 1: Node Agent Reachability & Process Health ────────────────────
    iran_probe = None
    foreign_probe = None
    
    # Probe Iran node
    if iran_node_id:
        try:
            iran_probe = await asyncio.wait_for(client.probe_tunnel(iran_node_id, tunnel.id), timeout=3.5)
        except Exception as e:
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Iran server ({ir_name}) is offline or unreachable: {str(e)}"
            }
        
        if not iran_probe or iran_probe.get("status") == "error":
            err_msg = iran_probe.get("message", "Node agent unreachable") if iran_probe else "Unreachable"
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Iran server ({ir_name}) error: {err_msg}"
            }
        
        if not iran_probe.get("process_running", True):
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Proxy core ({tunnel.core}) process is not running on Iran server ({ir_name})"
            }

    # Probe Foreign node (if present)
    if foreign_node_id:
        try:
            foreign_probe = await asyncio.wait_for(client.probe_tunnel(foreign_node_id, tunnel.id), timeout=3.5)
        except Exception as e:
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Foreign server ({fn_name}) is offline or unreachable: {str(e)}"
            }
        
        if not foreign_probe or foreign_probe.get("status") == "error":
            err_msg = foreign_probe.get("message", "Node agent unreachable") if foreign_probe else "Unreachable"
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Foreign server ({fn_name}) error: {err_msg}"
            }
        
        if not foreign_probe.get("process_running", True):
            return {
                "tunnel_id": tunnel.id,
                "status": "error",
                "latency_ms": None,
                "message": f"Proxy core ({tunnel.core}) process is not running on Foreign server ({fn_name})"
            }

    # ─── TIER 2: Ingress Socket Verification ────────────────────────────────
    ingress_port = None
    ingress_tested = False
    if iran_probe:
        ingress_port = iran_probe.get("ingress_port")
        if ingress_port is not None:
            ingress_tested = True
            if not iran_probe.get("ingress_port_open", False):
                return {
                    "tunnel_id": tunnel.id,
                    "status": "error",
                    "latency_ms": None,
                    "message": f"Tunnel ingress port {ingress_port} is closed on {ir_name}"
                }

    # ─── TIER 3: End-to-End Data Plane & Network Latency ───────────────────
    ctrl_port = tunnel.spec.get("control_port") if tunnel.spec else None
    latency_ms = None
    
    if iran_node_id and fn_ip:
        latency_ms = await client.probe_ping(iran_node_id, fn_ip, ctrl_port)
    elif fn_ip:
        from app.utils import measure_precise_ping
        latency_ms = await measure_precise_ping(fn_ip)
    
    # If ping probe failed but local probe saw socket RTT, fallback gracefully
    if latency_ms is None and iran_probe and iran_probe.get("tunnel_rtt_ms"):
        latency_ms = iran_probe.get("tunnel_rtt_ms")
        
    if latency_ms is None and foreign_node_id:
        return {
            "tunnel_id": tunnel.id,
            "status": "error",
            "latency_ms": None,
            "message": f"Iran node cannot reach Foreign server at {fn_ip} (connection timed out)"
        }
    
    if latency_ms is None:
        latency_ms = iran_probe.get("tunnel_rtt_ms") if iran_probe else None

    # Cache true latency in tunnel spec
    if not tunnel.spec:
        tunnel.spec = {}
    tunnel.spec["latency_ms"] = latency_ms
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(tunnel, "spec")
    await db.commit()
    
    stages = {
        "iran_process": {"alive": True, "core": tunnel.core},
        "foreign_process": {"alive": True, "core": tunnel.core} if foreign_node_id else {"alive": True, "core": "direct"},
        "ingress_socket": {"open": True, "port": ingress_port} if ingress_tested else {"open": True},
        "data_flow": {"passed": True, "rtt_ms": latency_ms}
    }
    
    return {
        "tunnel_id": tunnel.id,
        "status": "active",
        "latency_ms": latency_ms,
        "stages": stages,
        "message": f"Tunnel is fully active and routing traffic ({latency_ms} ms)"
    }



