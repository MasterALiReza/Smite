"""Nodes API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field
import hashlib
import httpx
import logging
import re
import ssl
import time
import asyncio

from app.database import get_db
from app.models import Node, Settings, Admin, Tunnel, utcnow
from app.node_client import NodeClient, public_node_metadata, RESERVED_NODE_METADATA_KEYS
from app.routers.auth import get_current_user, get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter()


def _strip_reserved_metadata(meta: Optional[dict]) -> dict:
    """Drop secret/bookkeeping keys a registering node (or the UI) must never set."""
    cleaned = dict(meta or {})
    for key in RESERVED_NODE_METADATA_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _provisioned_node_token(node: Optional[Node]) -> str:
    """Per-node agent token the panel provisioned for this node ('' if none)."""
    if node is None or not node.node_metadata:
        return ""
    if node.node_metadata.get("agent_token_mode") == "env":
        return ""
    return str(node.node_metadata.get("agent_token") or "")


def _token_matches(provided: Optional[str], expected: Optional[str]) -> bool:
    import hmac
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _node_request_trust(request: Request, node: Optional[Node]) -> Tuple[bool, bool]:
    """Return (authenticated, legacy_open) for a node-originated request.

    authenticated: the request carries the node's provisioned token (or the global
    NODE_API_TOKEN). legacy_open: no token exists for this node yet, so old
    unauthenticated behavior is kept until the panel provisions one.
    """
    from app.config import settings as app_settings
    provided = request.headers.get("X-Node-Token", "")
    node_token = _provisioned_node_token(node)
    global_token = app_settings.node_api_token or ""
    authenticated = _token_matches(provided, node_token) or _token_matches(provided, global_token)
    legacy_open = not node_token and not global_token
    return authenticated, legacy_open


def _api_address(scheme: str, host: str, port: int) -> str:
    formatted = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{formatted}:{port}"


def _agent_transport_values(transport: Optional["NodeAgentTransport"], host: str, port: int, trusted: bool) -> Dict[str, Any]:
    if transport is None:
        return {}
    if transport.scheme.lower() != "https":
        raise HTTPException(status_code=400, detail="Agent transport scheme must be https")
    try:
        der = ssl.PEM_cert_to_DER_cert(transport.certificate)
    except ValueError:
        raise HTTPException(status_code=400, detail="Agent transport certificate is invalid")
    fingerprint = hashlib.sha256(der).hexdigest()
    if not hmac_compare(fingerprint, transport.fingerprint.lower()):
        raise HTTPException(status_code=400, detail="Agent transport certificate fingerprint does not match")

    address = _api_address("https", host, port)
    if trusted:
        return {
            "api_address": address,
            "agent_tls_certificate": transport.certificate,
            "agent_tls_fingerprint": fingerprint,
            "agent_transport_mode": "pinned_https",
        }

    proof = (transport.bootstrap_proof or "").strip()
    expires_at = transport.bootstrap_expires_at
    now = int(time.time())
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", proof):
        raise HTTPException(status_code=400, detail="A valid one-time bootstrap proof is required")
    if not isinstance(expires_at, int) or expires_at <= now or expires_at > now + 3600:
        raise HTTPException(status_code=400, detail="Bootstrap proof expiry is invalid")
    return {
        "agent_pending_api_address": address,
        "agent_pending_tls_certificate": transport.certificate,
        "agent_pending_tls_fingerprint": fingerprint,
        "agent_bootstrap_proof": proof,
        "agent_bootstrap_expires_at": expires_at,
        "agent_transport_mode": "pending_pinned_https",
    }


def hmac_compare(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


async def _probe_agent_status(host: str, port: int, transport: Optional["NodeAgentTransport"]) -> str:
    """Panel-to-node reachability probe, pinned HTTPS when the node advertises it."""
    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            if transport is not None:
                context = ssl.create_default_context(cadata=transport.certificate)
                # The pinned certificate is the trust anchor here; hostname matching
                # is enforced on real management traffic, not on this probe.
                context.check_hostname = False
                context.verify_mode = ssl.CERT_REQUIRED
                url = _api_address("https", host, port) + "/api/agent/status"
                async with httpx.AsyncClient(timeout=2.0, verify=context) as client:
                    resp = await client.get(url)
            else:
                url = _api_address("http", host, port) + "/api/agent/status"
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(url)
                if resp.status_code == 400:
                    alt_url = _api_address("https", host, port) + "/api/agent/status"
                    async with httpx.AsyncClient(timeout=2.0, verify=False) as alt_client:
                        alt_resp = await alt_client.get(alt_url)
                    if alt_resp.status_code in (200, 401):
                        return "connected"
            if resp.status_code in (200, 401):
                return "connected"
        except Exception as error:
            last_error = error
            if transport is None:
                try:
                    alt_url = _api_address("https", host, port) + "/api/agent/status"
                    async with httpx.AsyncClient(timeout=2.0, verify=False) as alt_client:
                        alt_resp = await alt_client.get(alt_url)
                    if alt_resp.status_code in (200, 401):
                        return "connected"
                except Exception:
                    pass
        if attempt < 2:
            await asyncio.sleep(1.0)
    if last_error:
        logger.info(f"Agent reachability probe to {_api_address('https' if transport else 'http', host, port)} failed: {last_error}")
    return "disconnected"


class NodeAgentTransport(BaseModel):
    scheme: str
    certificate: str = Field(min_length=64, max_length=16384)
    fingerprint: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    bootstrap_proof: Optional[str] = Field(default=None, max_length=128)
    bootstrap_expires_at: Optional[int] = None


class NodeCreate(BaseModel):
    name: str
    ip_address: str
    api_port: int = 8888
    metadata: dict = {}
    agent_transport: Optional[NodeAgentTransport] = None


class NodeUpdate(BaseModel):
    name: str = None
    metadata: dict = None


class NodeAutoRegister(BaseModel):
    name: str
    ip_address: str
    api_port: int = 8888
    role: str = "foreign"
    registration_token: str
    metadata: dict = {}
    agent_transport: Optional[NodeAgentTransport] = None


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    fingerprint: str
    status: str
    registered_at: datetime
    last_seen: datetime
    metadata: dict


async def get_country_info(ip: str) -> tuple:
    """Return (country_code, country_name) for a given IP"""
    if not ip or ip.startswith(("127.", "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        return ("IR", "Iran")

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=status,country,countryCode")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return (data.get("countryCode", "").upper(), data.get("country", ""))
    except Exception:
        pass

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"https://ipapi.co/{ip}/json/")
            if resp.status_code == 200:
                data = resp.json()
                cc = data.get("country_code", "")
                if cc:
                    return (cc.upper(), data.get("country_name", ""))
    except Exception:
        pass

    return ("", "")


async def enrich_node_response_metadata(db: AsyncSession, node_id: str, metadata: dict) -> dict:
    """Enrich node response metadata with authoritative active tunnels and pending removals"""
    from app.mutation_guard import mutation_guard

    enriched = metadata.copy() if metadata else {}

    # While the guard is engaged the panel's tunnel list may come from a stale
    # restore. Withholding both fields leaves the node's current tunnels untouched
    # (both are inert when absent) and keeps pending removals queued.
    if mutation_guard.is_engaged():
        logger.info(
            "Mutation guard engaged: withholding authoritative tunnel list and pending removals for node %s",
            node_id,
        )
        return enriched

    # 1. Authoritative active tunnels
    try:
        # Every tunnel that still exists in the database is authoritative, whatever
        # its status. Excluding e.g. "error" tunnels made nodes purge (kill) tunnels
        # that only had a transient apply failure.
        t_res = await db.execute(
            select(Tunnel.id).where(
                or_(
                    Tunnel.node_id == node_id,
                    Tunnel.foreign_node_id == node_id,
                    Tunnel.iran_node_id == node_id
                )
            )
        )
        enriched["active_tunnel_ids"] = list(t_res.scalars().all())
    except Exception as e:
        logger.warning(f"Failed to query active tunnels for node {node_id}: {e}")

    # 2. Check pending tunnel removals
    try:
        from sqlalchemy.orm.attributes import flag_modified
        p_res = await db.execute(select(Settings).where(Settings.key == "pending_tunnel_removals"))
        p_setting = p_res.scalar_one_or_none()
        if p_setting and p_setting.value and node_id in p_setting.value:
            pending_list = p_setting.value.get(node_id, [])
            if pending_list:
                enriched["pending_removals"] = pending_list
                new_p = dict(p_setting.value)
                new_p.pop(node_id, None)
                p_setting.value = new_p
                flag_modified(p_setting, "value")
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to check pending removals for node {node_id}: {e}")

    return enriched


@router.post("/auto-register", response_model=NodeResponse)
async def auto_register_node(payload: NodeAutoRegister, db: AsyncSession = Depends(get_db)):
    """Auto-register a node from the installer script using registration token"""
    import hashlib
    from app.config import settings
    
    expected_token = hashlib.sha256(f"smite_node_reg:{settings.secret_key}".encode()).hexdigest()[:32]
    if payload.registration_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid registration token")
        
    incoming_role = payload.role if payload.role in ["iran", "foreign"] else "foreign"

    fingerprint_data = f"{payload.ip_address}:{payload.api_port}".encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:16]

    # Clear decommission tombstone if node is re-registering via legitimate token
    decom_res = await db.execute(select(Settings).where(Settings.key == "decommissioned_nodes"))
    decom_setting = decom_res.scalar_one_or_none()
    if decom_setting and decom_setting.value and fingerprint in decom_setting.value:
        from sqlalchemy.orm.attributes import flag_modified
        new_val = decom_setting.value.copy()
        new_val.pop(fingerprint, None)
        decom_setting.value = new_val
        flag_modified(decom_setting, "value")

    result = await db.execute(select(Node).where(Node.fingerprint == fingerprint))
    existing = result.scalar_one_or_none()
    existing_meta = dict(existing.node_metadata or {}) if existing else {}
    pinned_existing = existing_meta.get("agent_transport_mode") == "pinned_https"

    incoming_fingerprint = None
    if payload.agent_transport is not None:
        try:
            incoming_fingerprint = hashlib.sha256(
                ssl.PEM_cert_to_DER_cert(payload.agent_transport.certificate)
            ).hexdigest()
        except ValueError:
            raise HTTPException(status_code=400, detail="Agent transport certificate is invalid")
    # A paired node re-registers with the same identity but no proof; that is a
    # heartbeat, not a new enrollment, so its live transport is left untouched.
    same_pinned_identity = bool(
        pinned_existing
        and incoming_fingerprint
        and payload.agent_transport.scheme.lower() == "https"
        and hmac_compare(incoming_fingerprint, str(existing_meta.get("agent_tls_fingerprint") or "").lower())
    )
    transport_values = (
        {}
        if same_pinned_identity
        else _agent_transport_values(
            payload.agent_transport,
            payload.ip_address,
            payload.api_port,
            trusted=False,
        )
    )
    metadata = _strip_reserved_metadata(payload.metadata)
    metadata["api_address"] = _api_address("http", payload.ip_address, payload.api_port)
    metadata["ip_address"] = payload.ip_address
    metadata["api_port"] = payload.api_port
    metadata["role"] = incoming_role
    metadata.update(transport_values)

    # Reverse probe verification with retry (gives starting containers time to accept traffic)
    client_conn_status = await _probe_agent_status(payload.ip_address, payload.api_port, payload.agent_transport)
    metadata["connection_status"] = client_conn_status

    # GeoIP resolution & Intelligent Naming
    country_code = metadata.get("country_code", "")
    country_name = metadata.get("country_name", "")
    if incoming_role == "iran" or (payload.name and "IRAN" in payload.name.upper()):
        country_code = "IR"
        country_name = "Iran"
    elif not country_code:
        cc, cn = await get_country_info(payload.ip_address)
        if cc:
            country_code = cc
            country_name = cn
            
    if country_code:
        metadata["country_code"] = country_code
        if country_name:
            metadata["country_name"] = country_name

    # Determine node name
    final_name = payload.name
    is_generic = (
        not final_name or
        final_name.startswith("node-") or
        final_name.startswith("srv-") or
        "-node-" in final_name or
        final_name.startswith("ubuntu-") or
        final_name.startswith("debian-")
    )

    if is_generic and country_code:
        # Determine next non-colliding sequence number for this country code
        all_nodes_res = await db.execute(select(Node))
        all_nodes = all_nodes_res.scalars().all()
        existing_numbers = []
        for n in all_nodes:
            if n.id != getattr(existing, "id", None) and (
                (n.node_metadata and n.node_metadata.get("country_code") == country_code) or
                n.name.startswith(f"{country_code} Node")
            ):
                m = re.search(rf"^{re.escape(country_code)}\s+Node\s+(\d+)$", n.name)
                if m:
                    existing_numbers.append(int(m.group(1)))
        next_num = (max(existing_numbers) + 1) if existing_numbers else 1
        final_name = f"{country_code} Node {next_num}"

    if existing:
        if final_name and not existing.name:
            existing.name = final_name
        existing.last_seen = utcnow()
        existing.status = "active"
        merged_meta = dict(existing.node_metadata or {})
        if merged_meta.get("agent_transport_mode") == "pinned_https":
            # A paired node's live transport is only changed by an authenticated
            # admin action; an enrollment payload can never downgrade it to HTTP.
            metadata.pop("api_address", None)
        merged_meta.update(metadata)
        existing.node_metadata = merged_meta
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(existing, "node_metadata")
        await db.commit()
        await db.refresh(existing)
        invalidate_node_status_cache(existing.id)
        from app.node_auth_manager import node_auth_manager
        node_auth_manager.schedule_soon(existing.id, existing.node_metadata)
        resp_meta = public_node_metadata(await enrich_node_response_metadata(db, existing.id, existing.node_metadata))
        return NodeResponse(
            id=existing.id,
            name=existing.name,
            fingerprint=existing.fingerprint,
            status=existing.status,
            registered_at=existing.registered_at,
            last_seen=existing.last_seen,
            metadata=resp_meta
        )
    else:
        db_node = Node(
            name=final_name or f"node-1",
            fingerprint=fingerprint,
            status="active",
            node_metadata=metadata
        )
        db.add(db_node)
        await db.commit()
        await db.refresh(db_node)
        invalidate_node_status_cache(db_node.id)
        from app.node_auth_manager import node_auth_manager
        node_auth_manager.schedule_soon(db_node.id, db_node.node_metadata)
        resp_meta = public_node_metadata(await enrich_node_response_metadata(db, db_node.id, db_node.node_metadata))
        return NodeResponse(
            id=db_node.id,
            name=db_node.name,
            fingerprint=db_node.fingerprint,
            status=db_node.status,
            registered_at=db_node.registered_at,
            last_seen=db_node.last_seen,
            metadata=resp_meta
        )


@router.post("", response_model=NodeResponse)
async def create_node(node: NodeCreate, request: Request, db: AsyncSession = Depends(get_db), current_user: Optional[Admin] = Depends(get_current_user_optional)):
    """Register a new node

    Frontend/admin requests (Bearer token) get full behavior. Unauthenticated
    node registrations are accepted for backward compatibility with older
    deployed nodes, but they cannot overwrite metadata of an existing node
    fingerprint (node-hijack protection).
    """
    import hashlib
    
    fingerprint_data = f"{node.ip_address}:{node.api_port}".encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:16]
    
    result = await db.execute(select(Node).where(Node.fingerprint == fingerprint))
    existing = result.scalar_one_or_none()

    # Check tombstone / decommissioned status to prevent zombie node auto-resurrection
    decom_res = await db.execute(select(Settings).where(Settings.key == "decommissioned_nodes"))
    decom_setting = decom_res.scalar_one_or_none()
    is_decommissioned = bool(decom_setting and decom_setting.value and fingerprint in decom_setting.value)

    if is_decommissioned:
        if current_user is not None:
            # Admin is explicitly re-adding the node, remove from tombstone
            from sqlalchemy.orm.attributes import flag_modified
            new_val = decom_setting.value.copy()
            new_val.pop(fingerprint, None)
            decom_setting.value = new_val
            flag_modified(decom_setting, "value")
        else:
            # Unauthenticated registration from decommissioned node: reject!
            raise HTTPException(
                status_code=403,
                detail="This node has been decommissioned. Re-add from panel or use Auto Join."
            )

    if not existing and current_user is None:
        # Prevent unauthenticated zombie node creation
        raise HTTPException(
            status_code=401,
            detail="Unauthenticated registration of new nodes is disabled. Use Auto Join with a valid registration token."
        )

    # Node heartbeat authentication: once the panel has provisioned an agent token
    # for this node, a registration must carry it. Otherwise anybody knowing the
    # node's IP:port could impersonate it and receive the FRP comm credentials and
    # its pending tunnel removals.
    request_authenticated = current_user is not None
    if existing and current_user is None:
        authenticated, legacy_open = _node_request_trust(request, existing)
        request_authenticated = authenticated
        if not authenticated and not legacy_open:
            client_host = request.client.host if request.client else "unknown"
            logger.warning(f"Rejected unauthenticated registration for node {existing.id} from {client_host}")
            raise HTTPException(status_code=401, detail="Invalid or missing node token")

    transport_values = _agent_transport_values(
        node.agent_transport,
        node.ip_address,
        node.api_port,
        trusted=request_authenticated,
    )
    metadata = _strip_reserved_metadata(node.metadata)
    metadata["api_address"] = _api_address("http", node.ip_address, node.api_port)
    metadata["ip_address"] = node.ip_address
    metadata["api_port"] = node.api_port
    metadata.update(transport_values)
    
    incoming_role = node.metadata.get("role", "iran") if node.metadata else "iran"
    if incoming_role not in ["iran", "foreign"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid role '{incoming_role}'. Role must be either 'iran' or 'foreign'"
        )
    metadata["role"] = incoming_role
    
    if existing:
        existing_role = existing.node_metadata.get("role", "iran") if existing.node_metadata else "iran"
        if existing_role != incoming_role:
            raise HTTPException(
                status_code=409,
                detail=f"Node with this fingerprint already exists with role '{existing_role}'. "
                       f"Cannot register as '{incoming_role}'. "
                       f"Each node must have a consistent role."
            )
        
        existing.last_seen = utcnow()
        existing.status = "active"
        if request_authenticated:
            merged_meta = dict(existing.node_metadata or {})
            if merged_meta.get("agent_transport_mode") == "pinned_https" and node.agent_transport is None:
                metadata.pop("api_address", None)
            merged_meta.update(metadata)
            merged_meta["role"] = existing_role
            existing.node_metadata = merged_meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(existing, "node_metadata")
        elif transport_values:
            merged_meta = dict(existing.node_metadata or {})
            merged_meta.update(transport_values)
            existing.node_metadata = merged_meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(existing, "node_metadata")
        else:
            logger.debug(f"Unauthenticated re-registration for node {existing.id}, metadata update skipped (hijack protection)")
        await db.commit()
        await db.refresh(existing)
        
        response_metadata = existing.node_metadata.copy() if existing.node_metadata else {}
        
        result = await db.execute(select(Settings).where(Settings.key == "frp"))
        frp_setting = result.scalar_one_or_none()
        node_role = (existing.node_metadata.get("role") if existing.node_metadata else None) or metadata.get("role")
        if frp_setting and frp_setting.value and frp_setting.value.get("enabled") and node_role == "iran":
            panel_address = node.metadata.get("panel_address", "") if node.metadata else ""
            if panel_address:
                if "://" in panel_address:
                    from urllib.parse import urlparse
                    panel_host = urlparse(panel_address).hostname or ""
                else:
                    panel_host = panel_address.split(":")[0]
            else:
                panel_host = ""
            
            if not panel_host or panel_host == "panel.example.com":
                import socket
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    panel_host = s.getsockname()[0]
                    s.close()
                except:
                    panel_host = "127.0.0.1"
            
            response_metadata["frp_config"] = {
                "enabled": True,
                "server_addr": panel_host,
                "server_port": frp_setting.value.get("port", 7000),
                "token": frp_setting.value.get("token")
            }
        
        response_metadata = public_node_metadata(await enrich_node_response_metadata(db, existing.id, response_metadata))
        try:
            from app.node_auth_manager import node_auth_manager
            node_auth_manager.schedule_soon(existing.id, existing.node_metadata)
        except Exception:
            pass
        return NodeResponse(
            id=existing.id,
            name=existing.name,
            fingerprint=existing.fingerprint,
            status=existing.status,
            registered_at=existing.registered_at,
            last_seen=existing.last_seen,
            metadata=response_metadata
        )
    
    db_node = Node(
        name=node.name,
        fingerprint=fingerprint,
        status="active",
        node_metadata=metadata
    )
    db.add(db_node)
    await db.commit()
    await db.refresh(db_node)
    invalidate_node_status_cache(db_node.id)
    
    response_metadata = db_node.node_metadata.copy() if db_node.node_metadata else {}
    
    result = await db.execute(select(Settings).where(Settings.key == "frp"))
    frp_setting = result.scalar_one_or_none()
    new_node_role = (db_node.node_metadata.get("role") if db_node.node_metadata else None) or metadata.get("role")
    if frp_setting and frp_setting.value and frp_setting.value.get("enabled") and new_node_role == "iran":
        panel_address = node.metadata.get("panel_address", "") if node.metadata else ""
        if panel_address:
            if "://" in panel_address:
                from urllib.parse import urlparse
                panel_host = urlparse(panel_address).hostname or ""
            else:
                panel_host = panel_address.split(":")[0]
        else:
            panel_host = ""
            
        if not panel_host or panel_host == "panel.example.com":
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                panel_host = s.getsockname()[0]
                s.close()
            except:
                panel_host = "127.0.0.1"
        
        response_metadata["frp_config"] = {
            "enabled": True,
            "server_addr": panel_host,
            "server_port": frp_setting.value.get("port", 7000),
            "token": frp_setting.value.get("token")
        }
    
    response_metadata = public_node_metadata(await enrich_node_response_metadata(db, db_node.id, response_metadata))
    return NodeResponse(
        id=db_node.id,
        name=db_node.name,
        fingerprint=db_node.fingerprint,
        status=db_node.status,
        registered_at=db_node.registered_at,
        last_seen=db_node.last_seen,
        metadata=response_metadata
    )


_node_status_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_NODE_STATUS_CACHE_TTL = 3.5  # seconds cache TTL for instantaneous UI transitions


def invalidate_node_status_cache(node_id: Optional[str] = None):
    """Invalidate cached node connection status and latency."""
    global _node_status_cache
    if node_id:
        _node_status_cache.pop(node_id, None)
    else:
        _node_status_cache.clear()


async def check_node_status(node: Any, client: Optional[NodeClient] = None) -> NodeResponse:
    """Check single node connection state and latency with bounded timeouts."""
    if client is None:
        client = NodeClient()
    now = time.time()
    cached = _node_status_cache.get(node.id)
    if cached and (now - cached[0]) < _NODE_STATUS_CACHE_TTL:
        connection_status = cached[1].get("connection_status", "connected")
        latency_ms = cached[1].get("latency_ms")
    else:
        # Calculate time since last registration heartbeat (handling naive & aware UTC datetimes)
        if node.last_seen:
            try:
                if node.last_seen.tzinfo is not None:
                    from datetime import timezone
                    diff = (datetime.now(timezone.utc) - node.last_seen).total_seconds()
                else:
                    diff = (utcnow() - node.last_seen).total_seconds()
                seconds_since_last_seen = max(0.0, diff)
            except Exception:
                seconds_since_last_seen = 999999.0
        else:
            seconds_since_last_seen = 999999.0

        connection_status = "failed"
        latency_ms = None
        t_start = time.perf_counter()
        try:
            # Direct bounded check to node agent (1.8s max timeout)
            response = await asyncio.wait_for(client.get_tunnel_status(node.id, ""), timeout=1.8)
            elapsed = int((time.perf_counter() - t_start) * 1000)
            if response and response.get("status") == "ok":
                connection_status = "connected"
                # Measure true wire network ping (ICMP / TCP handshake) instead of HTTP application overhead
                node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                if node_ip:
                    from app.utils import measure_precise_ping
                    api_p = node.node_metadata.get("api_port", 8888) if node.node_metadata else 8888
                    wire_ping = await measure_precise_ping(node_ip, fallback_ports=[api_p, 22, 443, 80])
                    latency_ms = wire_ping if wire_ping is not None else max(1, elapsed)
                else:
                    latency_ms = max(1, elapsed)
            else:
                latency_ms = None
                if seconds_since_last_seen <= 90:
                    connection_status = "reconnecting"
                else:
                    connection_status = "failed"
        except (httpx.TimeoutException, asyncio.TimeoutError):
            latency_ms = None
            if seconds_since_last_seen <= 90:
                connection_status = "reconnecting"
            else:
                connection_status = "failed"
        except httpx.ConnectError:
            latency_ms = None
            if seconds_since_last_seen <= 60:
                connection_status = "connecting"
            else:
                connection_status = "failed"
        except Exception:
            latency_ms = None
            if seconds_since_last_seen <= 90:
                connection_status = "reconnecting"
            else:
                connection_status = "failed"

        _node_status_cache[node.id] = (now, {
            "connection_status": connection_status,
            "latency_ms": latency_ms
        })
    
    metadata = public_node_metadata(node.node_metadata)
    metadata["connection_status"] = connection_status
    metadata["latency_ms"] = latency_ms
    
    uname = (node.name or "").upper()
    if metadata.get("role") == "iran" or "IRAN" in uname or uname.startswith("IR-") or uname.startswith("IR_") or uname.startswith("IR "):
        metadata["country_code"] = "IR"
        metadata["country_name"] = "Iran"
    elif not metadata.get("country_code") or metadata.get("country_code") == "DE" and metadata.get("role") == "iran":
        if "USA" in uname or "UNITED STATES" in uname or uname.startswith("US-") or uname.startswith("US ") or uname.startswith("US_"):
            metadata["country_code"] = "US"
        elif "TR-" in uname or uname.startswith("TR ") or uname.startswith("TR_") or "TURKEY" in uname:
            metadata["country_code"] = "TR"
        elif "FN-" in uname or "FI-" in uname or uname.startswith("FI ") or uname.startswith("FI_") or "FINLAND" in uname:
            metadata["country_code"] = "FI"
        elif "GERMANY" in uname or uname.startswith("DE-") or uname.startswith("DE ") or uname.startswith("DE_"):
            metadata["country_code"] = "DE"
        elif "NETHERLAND" in uname or uname.startswith("NL-") or uname.startswith("NL ") or uname.startswith("NL_"):
            metadata["country_code"] = "NL"
        elif "FRANCE" in uname or uname.startswith("FR-") or uname.startswith("FR ") or uname.startswith("FR_"):
            metadata["country_code"] = "FR"
        elif "GB-" in uname or "UK-" in uname or uname.startswith("GB ") or "ENGLAND" in uname or "BRITAIN" in uname:
            metadata["country_code"] = "GB"
        elif "HETZ" in uname:
            metadata["country_code"] = "DE"
        else:
            parts = (node.name or "").split()
            if len(parts) >= 2 and len(parts[0]) == 2 and parts[0].isupper():
                metadata["country_code"] = parts[0]
            elif metadata.get("role") == "iran":
                metadata["country_code"] = "IR"
            else:
                metadata["country_code"] = "US"
    
    return NodeResponse(
        id=node.id,
        name=node.name,
        fingerprint=node.fingerprint,
        status=node.status,
        registered_at=node.registered_at,
        last_seen=node.last_seen,
        metadata=metadata
    )


@router.get("", response_model=List[NodeResponse])
async def list_nodes(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """List all nodes with connection state and real-time latency"""
    result = await db.execute(select(Node))
    nodes = result.scalars().all()
    
    client = NodeClient()
    tasks = [check_node_status(node, client) for node in nodes]
    node_responses = await asyncio.gather(*tasks, return_exceptions=True)
    
    results = []
    for i, response in enumerate(node_responses):
        if isinstance(response, Exception):
            node = nodes[i]
            metadata = public_node_metadata(node.node_metadata)
            metadata["connection_status"] = "failed"
            metadata["latency_ms"] = None
            if not metadata.get("country_code"):
                uname = (node.name or "").upper()
                if "USA" in uname or "US-" in uname or uname.startswith("US "):
                    metadata["country_code"] = "US"
                elif "TR-" in uname or uname.startswith("TR "):
                    metadata["country_code"] = "TR"
                elif "FN-" in uname or "FI-" in uname or uname.startswith("FI ") or "HETZ" in uname:
                    metadata["country_code"] = "FI"
                elif "DE-" in uname or uname.startswith("DE "):
                    metadata["country_code"] = "DE"
                elif "IR-" in uname or uname.startswith("IR ") or metadata.get("role") == "iran":
                    metadata["country_code"] = "IR"
                else:
                    parts = (node.name or "").split()
                    if len(parts) >= 2 and len(parts[0]) == 2 and parts[0].isupper():
                        metadata["country_code"] = parts[0]
                    elif metadata.get("role") == "iran":
                        metadata["country_code"] = "IR"
            results.append(NodeResponse(
                id=node.id,
                name=node.name,
                fingerprint=node.fingerprint,
                status=node.status,
                registered_at=node.registered_at,
                last_seen=node.last_seen,
                metadata=metadata
            ))
        else:
            results.append(response)
    
    return results


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get node by ID"""
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return NodeResponse(
        id=node.id,
        name=node.name,
        fingerprint=node.fingerprint,
        status=node.status,
        registered_at=node.registered_at,
        last_seen=node.last_seen,
        metadata=public_node_metadata(node.node_metadata)
    )


@router.put("/{node_id}", response_model=NodeResponse)
async def update_node(node_id: str, payload: NodeUpdate, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Update node name and metadata"""
    from sqlalchemy.orm.attributes import flag_modified
    
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    if payload.name is not None and payload.name.strip():
        node.name = payload.name.strip()
        
    if payload.metadata is not None:
        merged_meta = dict(node.node_metadata or {})
        merged_meta.update(_strip_reserved_metadata(payload.metadata))
        node.node_metadata = merged_meta
        flag_modified(node, "node_metadata")
        
    await db.commit()
    await db.refresh(node)
    invalidate_node_status_cache(node_id)
    
    return NodeResponse(
        id=node.id,
        name=node.name,
        fingerprint=node.fingerprint,
        status=node.status,
        registered_at=node.registered_at,
        last_seen=node.last_seen,
        metadata=public_node_metadata(node.node_metadata)
    )


@router.post("/{node_id}/reset-agent-token")
async def reset_agent_token(node_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Forget the panel-side agent token of a node so it is provisioned again.

    Use after re-installing a node (or after `smite-node reset-token` on a node that
    reports a token conflict). The node is re-paired automatically within ~2 minutes.
    """
    from sqlalchemy.orm.attributes import flag_modified
    from app.node_auth_manager import node_auth_manager

    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    meta = dict(node.node_metadata or {})
    for key in RESERVED_NODE_METADATA_KEYS:
        meta.pop(key, None)
    node.node_metadata = meta
    flag_modified(node, "node_metadata")
    await db.commit()
    node_auth_manager._last_check.pop(node_id, None)
    return {"status": "success", "message": "Agent token cleared; it will be re-provisioned automatically"}


@router.get("/{node_id}/agent-auth")
async def get_agent_auth_status(node_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Report whether the node's agent API is protected by a provisioned token."""
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    meta = node.node_metadata or {}
    if meta.get("agent_token_mode") == "env":
        state = "static_env_token"
    elif meta.get("agent_token"):
        state = "provisioned"
    elif meta.get("agent_token_pending"):
        state = "pending"
    else:
        state = "not_provisioned"
    return {"node_id": node_id, "state": state, "error": meta.get("agent_token_error")}


@router.put("/{node_id}/frp-status")
async def update_frp_status(node_id: str, frp_status: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Update node FRP connection status with authentication & input validation"""
    from sqlalchemy.orm.attributes import flag_modified
    
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Security check: this endpoint redirects all panel->node traffic for the node
    # (frp_remote_port), so it must only be accepted from the node itself.
    authenticated, legacy_open = _node_request_trust(request, node)
    if not authenticated and not legacy_open:
        provided_token = request.headers.get("X-Node-Token")
        node_ip = (node.node_metadata or {}).get("ip_address")
        client_ip = request.client.host if request.client else ""
        # Legacy fallback (global token configured, node not provisioned yet): accept
        # only from the node's own registered IP or a collocated node.
        ip_ok = (
            not provided_token
            and not _provisioned_node_token(node)
            and client_ip in ({"127.0.0.1", "::1"} | ({node_ip} if node_ip else set()))
        )
        if not ip_ok:
            logger.warning(f"[FRP] Unauthenticated FRP status update rejected for node {node_id} from {client_ip}")
            raise HTTPException(status_code=401, detail="Invalid or missing node token")
    
    if not node.node_metadata:
        node.node_metadata = {}
    
    if frp_status.get("connected") and frp_status.get("remote_port"):
        try:
            remote_port = int(frp_status.get("remote_port"))
            if not (1 <= remote_port <= 65535):
                raise HTTPException(status_code=400, detail="remote_port must be between 1 and 65535")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="remote_port must be a valid integer")
        
        node.node_metadata["frp_remote_port"] = remote_port
        node.node_metadata["frp_connected"] = True
        logger.info(f"[FRP] Node {node_id} FRP status updated: remote_port={remote_port}")
    else:
        node.node_metadata["frp_connected"] = False
        node.node_metadata.pop("frp_remote_port", None)
        logger.info(f"[FRP] Node {node_id} FRP status cleared")
    
    # Mark JSON column as modified so SQLAlchemy detects the change
    flag_modified(node, "node_metadata")
    
    await db.commit()
    await db.refresh(node)
    invalidate_node_status_cache(node_id)
    return {"status": "success"}


@router.delete("/{node_id}")
async def delete_node(node_id: str, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Cleanly delete a node:
    1. Cascade teardown: release ports and remove all linked tunnels on counterpart nodes.
    2. Decommission remote node: instruct remote agent to kill all tunnel processes,
       cancel registration loop, and cleanly terminate its container.
    3. Blacklist/tombstone: record fingerprint so this node cannot re-register automatically as a zombie.
    4. Delete node and linked tunnel records from database.
    """
    from app.models import Tunnel
    from app.routers.tunnels import extract_all_tunnel_ports
    import asyncio

    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    node_fingerprint = node.fingerprint
    node_name = node.name

    # 1. Find all linked tunnels
    tunnel_res = await db.execute(
        select(Tunnel).where(
            or_(
                Tunnel.iran_node_id == node_id,
                Tunnel.foreign_node_id == node_id,
                Tunnel.node_id == node_id
            )
        )
    )
    linked_tunnels = tunnel_res.scalars().all()
    client = NodeClient()

    # 2. Cascade cleanup of linked tunnels on counterpart nodes
    tunnels_cleaned = 0
    for tunnel in linked_tunnels:
        try:
            # Determine the counterpart node
            counterpart_id = None
            if tunnel.iran_node_id == node_id:
                counterpart_id = tunnel.foreign_node_id
            elif tunnel.foreign_node_id == node_id:
                counterpart_id = tunnel.iran_node_id

            # Extract ports to free on the counterpart node
            raw_spec = tunnel.spec or {}
            if isinstance(raw_spec, str):
                try:
                    import json
                    spec_dict = json.loads(raw_spec)
                except Exception:
                    spec_dict = {}
            else:
                spec_dict = raw_spec

            ports_info = extract_all_tunnel_ports(spec_dict)
            all_ports = list(ports_info.get("all_ports", set()))
            ctrl_port = spec_dict.get("control_port") or spec_dict.get("bind_port") or spec_dict.get("server_port")

            if counterpart_id and counterpart_id != node_id:
                remove_payload = {
                    "tunnel_id": tunnel.id,
                    "purge": True,
                    "core": tunnel.core,
                    "ports": all_ports,
                    "control_port": ctrl_port,
                }
                try:
                    cascade_resp = await asyncio.wait_for(
                        client.send_to_node(
                            node_id=counterpart_id,
                            endpoint="/api/agent/tunnels/remove",
                            data=remove_payload
                        ),
                        timeout=8.0
                    )
                    if isinstance(cascade_resp, dict) and cascade_resp.get("status") == "error":
                        raise RuntimeError(cascade_resp.get("message") or "node returned an error")
                    logger.info(f"[Cascade] Notified counterpart node {counterpart_id} to release ports for tunnel {tunnel.id}")
                except Exception as peer_err:
                    logger.warning(f"[Cascade] Failed to notify counterpart node {counterpart_id}: {peer_err}")
                    try:
                        from sqlalchemy.orm.attributes import flag_modified
                        s_res = await db.execute(select(Settings).where(Settings.key == "pending_tunnel_removals"))
                        s_row = s_res.scalar_one_or_none()
                        if not s_row:
                            s_row = Settings(key="pending_tunnel_removals", value={})
                            db.add(s_row)
                        current_pending = dict(s_row.value or {})
                        node_list = list(current_pending.get(counterpart_id, []))
                        if not any(item.get("tunnel_id") == tunnel.id for item in node_list):
                            node_list.append(remove_payload)
                        current_pending[counterpart_id] = node_list
                        s_row.value = current_pending
                        flag_modified(s_row, "value")
                    except Exception as s_err:
                        logger.warning(f"Could not queue pending removal for counterpart node {counterpart_id}: {s_err}")

            # Delete the tunnel record
            await db.delete(tunnel)
            tunnels_cleaned += 1
        except Exception as t_err:
            logger.error(f"[Cascade] Error cleaning tunnel {tunnel.id}: {t_err}")

    # 3. Instruct target node agent to decommission (kill all processes, stop FRP, stop container)
    decommissioned_remote = False
    try:
        decom_resp = await asyncio.wait_for(
            client.send_to_node(
                node_id=node_id,
                endpoint="/api/agent/decommission",
                data={"action": "decommission", "stop_container": True}
            ),
            timeout=5.0
        )
        if isinstance(decom_resp, dict) and decom_resp.get("status") in ["success", "decommissioned"]:
            decommissioned_remote = True
            logger.info(f"Node {node_id} ({node_name}) successfully decommissioned remotely: {decom_resp}")
    except Exception as d_err:
        logger.warning(f"Remote decommission request failed for node {node_id} (node may be offline/unreachable): {d_err}")

    # 4. Record fingerprint in tombstone setting to prevent zombie auto-registration
    try:
        from sqlalchemy.orm.attributes import flag_modified
        settings_res = await db.execute(select(Settings).where(Settings.key == "decommissioned_nodes"))
        decom_setting = settings_res.scalar_one_or_none()
        if not decom_setting:
            decom_setting = Settings(key="decommissioned_nodes", value={})
            db.add(decom_setting)
        
        current_decom = decom_setting.value.copy() if decom_setting.value else {}
        current_decom[node_fingerprint] = {
            "name": node_name,
            "decommissioned_at": utcnow().isoformat(),
            "tunnels_removed": tunnels_cleaned
        }
        decom_setting.value = current_decom
        flag_modified(decom_setting, "value")
    except Exception as s_err:
        logger.warning(f"Could not record tombstone for node {node_id}: {s_err}")

    # 5. Delete node from database
    await db.delete(node)
    await db.commit()
    invalidate_node_status_cache(node_id)

    logger.info(f"Node {node_id} ('{node_name}') deleted. Tunnels removed: {tunnels_cleaned}, remote decommissioned: {decommissioned_remote}")
    return {
        "status": "deleted",
        "node_id": node_id,
        "name": node_name,
        "tunnels_removed": tunnels_cleaned,
        "decommissioned_remote": decommissioned_remote
    }

