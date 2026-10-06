"""Nodes API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from pydantic import BaseModel, ConfigDict
import httpx
import logging
import re

from app.database import get_db
from app.models import Node, Settings, Admin, Tunnel
from app.node_client import NodeClient
from app.routers.auth import get_current_user, get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter()


class NodeCreate(BaseModel):
    name: str
    ip_address: str
    api_port: int = 8888
    metadata: dict = {}


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
    enriched = metadata.copy() if metadata else {}
    
    # 1. Authoritative active tunnels
    try:
        t_res = await db.execute(
            select(Tunnel.id).where(
                or_(
                    Tunnel.node_id == node_id,
                    Tunnel.foreign_node_id == node_id,
                    Tunnel.iran_node_id == node_id
                ),
                Tunnel.status.in_(["active", "pending", "stopped", "running"])
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
    
    # Reverse probe verification with retry (gives starting containers time to accept traffic)
    import asyncio
    client_conn_status = "disconnected"
    for probe_attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"http://{payload.ip_address}:{payload.api_port}/api/agent/status")
                if resp.status_code == 200:
                    client_conn_status = "connected"
                    break
        except Exception as e:
            if probe_attempt < 2:
                await asyncio.sleep(1.0)
            else:
                logger.info(f"Probe to http://{payload.ip_address}:{payload.api_port} attempt {probe_attempt+1} failed: {e}")

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

    metadata = payload.metadata.copy() if payload.metadata else {}
    metadata["api_address"] = f"http://{payload.ip_address}:{payload.api_port}"
    metadata["ip_address"] = payload.ip_address
    metadata["api_port"] = payload.api_port
    metadata["role"] = incoming_role
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
        existing.last_seen = datetime.utcnow()
        existing.status = "active"
        existing.node_metadata.update(metadata)
        await db.commit()
        await db.refresh(existing)
        resp_meta = await enrich_node_response_metadata(db, existing.id, existing.node_metadata)
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
        resp_meta = await enrich_node_response_metadata(db, db_node.id, db_node.node_metadata)
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
    
    metadata = node.metadata.copy() if node.metadata else {}
    metadata["api_address"] = f"http://{node.ip_address}:{node.api_port}"
    metadata["ip_address"] = node.ip_address
    metadata["api_port"] = node.api_port
    
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
        
        existing.last_seen = datetime.utcnow()
        existing.status = "active"
        if current_user is not None:
            existing.node_metadata.update(metadata)
            existing.node_metadata["role"] = existing_role
        else:
            # Unauthenticated re-registration (legacy node): keep existing
            # metadata to prevent node-hijack via fingerprint overwrite.
            logger.debug(f"Unauthenticated re-registration for node {existing.id}, metadata update skipped (hijack protection)")
        await db.commit()
        await db.refresh(existing)
        
        response_metadata = existing.node_metadata.copy() if existing.node_metadata else {}
        
        result = await db.execute(select(Settings).where(Settings.key == "frp"))
        frp_setting = result.scalar_one_or_none()
        if frp_setting and frp_setting.value and frp_setting.value.get("enabled"):
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
        
        response_metadata = await enrich_node_response_metadata(db, existing.id, response_metadata)
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
    
    response_metadata = db_node.node_metadata.copy() if db_node.node_metadata else {}
    
    result = await db.execute(select(Settings).where(Settings.key == "frp"))
    frp_setting = result.scalar_one_or_none()
    if frp_setting and frp_setting.value and frp_setting.value.get("enabled"):
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
    
    response_metadata = await enrich_node_response_metadata(db, db_node.id, response_metadata)
    return NodeResponse(
        id=db_node.id,
        name=db_node.name,
        fingerprint=db_node.fingerprint,
        status=db_node.status,
        registered_at=db_node.registered_at,
        last_seen=db_node.last_seen,
        metadata=response_metadata
    )


@router.get("", response_model=List[NodeResponse])
async def list_nodes(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """List all nodes with connection state and real-time latency"""
    import asyncio
    import time
    result = await db.execute(select(Node))
    nodes = result.scalars().all()
    
    client = NodeClient()
    node_responses = []
    
    async def check_node_status(node):
        connection_status = "failed"
        latency_ms = None
        t_start = time.perf_counter()
        try:
            response = await client.get_tunnel_status(node.id, "")
            elapsed = int((time.perf_counter() - t_start) * 1000)
            if response and response.get("status") == "ok":
                connection_status = "connected"
                node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                from app.utils import measure_precise_ping
                ping_res = await measure_precise_ping(node_ip)
                latency_ms = ping_res if ping_res is not None else max(1, elapsed)
            else:
                error_msg = response.get("message", "Node disconnected") if response else "Node not responding"
                if "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                    if node.node_metadata and node.node_metadata.get("frp_connected"):
                        connection_status = "connected"
                        node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                        from app.utils import measure_precise_ping
                        ping_res = await measure_precise_ping(node_ip)
                        latency_ms = ping_res if ping_res is not None else max(1, elapsed)
                    else:
                        connection_status = "reconnecting"
                else:
                    connection_status = "failed"
        except httpx.ConnectError:
            if node.node_metadata and node.node_metadata.get("frp_connected"):
                connection_status = "connected"
                node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                from app.utils import measure_precise_ping
                ping_res = await measure_precise_ping(node_ip)
                latency_ms = ping_res if ping_res is not None else 40
            else:
                connection_status = "connecting"
        except httpx.TimeoutException:
            if node.node_metadata and node.node_metadata.get("frp_connected"):
                connection_status = "connected"
                node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                from app.utils import measure_precise_ping
                ping_res = await measure_precise_ping(node_ip)
                latency_ms = ping_res if ping_res is not None else 50
            else:
                connection_status = "reconnecting"
        except Exception:
            if node.node_metadata and node.node_metadata.get("frp_connected"):
                connection_status = "connected"
                node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
                from app.utils import measure_precise_ping
                ping_res = await measure_precise_ping(node_ip)
                latency_ms = ping_res if ping_res is not None else 45
            else:
                connection_status = "failed"
        
        metadata = node.node_metadata.copy() if node.node_metadata else {}
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
    
    tasks = [check_node_status(node) for node in nodes]
    node_responses = await asyncio.gather(*tasks, return_exceptions=True)
    
    results = []
    for i, response in enumerate(node_responses):
        if isinstance(response, Exception):
            node = nodes[i]
            metadata = node.node_metadata.copy() if node.node_metadata else {}
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
        metadata=node.node_metadata or {}
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
        if not node.node_metadata:
            node.node_metadata = {}
        node.node_metadata.update(payload.metadata)
        flag_modified(node, "node_metadata")
        
    await db.commit()
    await db.refresh(node)
    
    return NodeResponse(
        id=node.id,
        name=node.name,
        fingerprint=node.fingerprint,
        status=node.status,
        registered_at=node.registered_at,
        last_seen=node.last_seen,
        metadata=node.node_metadata or {}
    )


@router.put("/{node_id}/frp-status")
async def update_frp_status(node_id: str, frp_status: dict, request: Request, db: AsyncSession = Depends(get_db)):
    """Update node FRP connection status with authentication & input validation"""
    import hmac
    from app.config import settings
    from sqlalchemy.orm.attributes import flag_modified
    
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Security check: Validate caller if NODE_API_TOKEN is configured
    if settings.node_api_token:
        provided_token = request.headers.get("X-Node-Token")
        if provided_token:
            if not hmac.compare_digest(provided_token, settings.node_api_token):
                raise HTTPException(status_code=403, detail="Invalid node token")
        else:
            # Fallback for collocated or registered IP
            client_ip = request.client.host if request.client else ""
            allowed_ips = {"127.0.0.1", "::1", "localhost"}
            if node.ip_address:
                allowed_ips.add(node.ip_address)
            if client_ip not in allowed_ips:
                logger.warning(f"[FRP] Unauthenticated FRP status update rejected for node {node_id} from {client_ip}")
                raise HTTPException(status_code=401, detail="X-Node-Token header required")
    
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
                    await asyncio.wait_for(
                        client.send_to_node(
                            node_id=counterpart_id,
                            endpoint="/api/agent/tunnels/remove",
                            data=remove_payload
                        ),
                        timeout=5.0
                    )
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
            "decommissioned_at": datetime.utcnow().isoformat(),
            "tunnels_removed": tunnels_cleaned
        }
        decom_setting.value = current_decom
        flag_modified(decom_setting, "value")
    except Exception as s_err:
        logger.warning(f"Could not record tombstone for node {node_id}: {s_err}")

    # 5. Delete node from database
    await db.delete(node)
    await db.commit()

    logger.info(f"Node {node_id} ('{node_name}') deleted. Tunnels removed: {tunnels_cleaned}, remote decommissioned: {decommissioned_remote}")
    return {
        "status": "deleted",
        "node_id": node_id,
        "name": node_name,
        "tunnels_removed": tunnels_cleaned,
        "decommissioned_remote": decommissioned_remote
    }

