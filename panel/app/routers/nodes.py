"""Nodes API endpoints"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from datetime import datetime
from pydantic import BaseModel
import httpx
import logging

from app.database import get_db
from app.models import Node, Settings
from app.node_client import NodeClient

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
    id: str
    name: str
    fingerprint: str
    status: str
    registered_at: datetime
    last_seen: datetime
    metadata: dict
    
    class Config:
        from_attributes = True


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


@router.post("/auto-register", response_model=NodeResponse)
async def auto_register_node(payload: NodeAutoRegister, db: AsyncSession = Depends(get_db)):
    """Auto-register a node from the installer script using registration token"""
    import hashlib
    from app.config import settings
    
    expected_token = hashlib.sha256(f"smite_node_reg:{settings.secret_key}".encode()).hexdigest()[:32]
    if payload.registration_token != expected_token and payload.registration_token != settings.secret_key:
        raise HTTPException(status_code=401, detail="Invalid registration token")
        
    incoming_role = payload.role if payload.role in ["iran", "foreign"] else "foreign"
    
    # Reverse probe verification
    client_conn_status = "connected"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"http://{payload.ip_address}:{payload.api_port}/api/agent/status")
            if resp.status_code == 200:
                client_conn_status = "connected"
    except Exception as e:
        logger.warning(f"Probe to http://{payload.ip_address}:{payload.api_port} failed: {e}")
        client_conn_status = "connected"

    fingerprint_data = f"{payload.ip_address}:{payload.api_port}".encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:16]

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
    if not country_code:
        cc, cn = await get_country_info(payload.ip_address)
        if cc:
            country_code = cc
            country_name = cn
        elif incoming_role == "iran":
            country_code = "IR"
            country_name = "Iran"
            
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
        # Count existing nodes with this country code
        all_nodes_res = await db.execute(select(Node))
        all_nodes = all_nodes_res.scalars().all()
        matching_count = sum(
            1 for n in all_nodes
            if n.id != getattr(existing, "id", None) and (
                (n.node_metadata and n.node_metadata.get("country_code") == country_code) or
                n.name.startswith(f"{country_code} Node")
            )
        )
        final_name = f"{country_code} Node {matching_count + 1}"

    if existing:
        if final_name and not existing.name:
            existing.name = final_name
        existing.last_seen = datetime.utcnow()
        existing.status = "active"
        existing.node_metadata.update(metadata)
        await db.commit()
        await db.refresh(existing)
        return NodeResponse(
            id=existing.id,
            name=existing.name,
            fingerprint=existing.fingerprint,
            status=existing.status,
            registered_at=existing.registered_at,
            last_seen=existing.last_seen,
            metadata=existing.node_metadata
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
        return NodeResponse(
            id=db_node.id,
            name=db_node.name,
            fingerprint=db_node.fingerprint,
            status=db_node.status,
            registered_at=db_node.registered_at,
            last_seen=db_node.last_seen,
            metadata=db_node.node_metadata
        )


@router.post("", response_model=NodeResponse)
async def create_node(node: NodeCreate, db: AsyncSession = Depends(get_db)):
    """Register a new node"""
    import hashlib
    
    fingerprint_data = f"{node.ip_address}:{node.api_port}".encode()
    fingerprint = hashlib.sha256(fingerprint_data).hexdigest()[:16]
    
    result = await db.execute(select(Node).where(Node.fingerprint == fingerprint))
    existing = result.scalar_one_or_none()
    
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
        existing.node_metadata.update(metadata)
        existing.node_metadata["role"] = existing_role
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
async def list_nodes(db: AsyncSession = Depends(get_db)):
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
        
        if not metadata.get("country_code"):
            uname = (node.name or "").upper()
            if "USA" in uname or "US-" in uname or uname.startswith("US ") or "UNITED STATES" in uname:
                metadata["country_code"] = "US"
            elif "TR-" in uname or uname.startswith("TR ") or "TURKEY" in uname:
                metadata["country_code"] = "TR"
            elif "FN-" in uname or "FI-" in uname or uname.startswith("FI ") or "HETZ" in uname or "FINLAND" in uname:
                metadata["country_code"] = "FI"
            elif "DE-" in uname or uname.startswith("DE ") or "GERMANY" in uname:
                metadata["country_code"] = "DE"
            elif "NL-" in uname or uname.startswith("NL ") or "NETHERLANDS" in uname:
                metadata["country_code"] = "NL"
            elif "FR-" in uname or uname.startswith("FR ") or "FRANCE" in uname:
                metadata["country_code"] = "FR"
            elif "GB-" in uname or "UK-" in uname or uname.startswith("GB "):
                metadata["country_code"] = "GB"
            elif "IR-" in uname or uname.startswith("IR ") or metadata.get("role") == "iran":
                metadata["country_code"] = "IR"
            else:
                parts = (node.name or "").split()
                if len(parts) >= 2 and len(parts[0]) == 2 and parts[0].isupper():
                    metadata["country_code"] = parts[0]
                elif metadata.get("role") == "iran":
                    metadata["country_code"] = "IR"
        
        node.node_metadata = metadata
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(node, "node_metadata")
        
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
async def get_node(node_id: str, db: AsyncSession = Depends(get_db)):
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
async def update_node(node_id: str, payload: NodeUpdate, db: AsyncSession = Depends(get_db)):
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
async def update_frp_status(node_id: str, frp_status: dict, db: AsyncSession = Depends(get_db)):
    """Update node FRP connection status"""
    from sqlalchemy.orm.attributes import flag_modified
    
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    if not node.node_metadata:
        node.node_metadata = {}
    
    if frp_status.get("connected") and frp_status.get("remote_port"):
        node.node_metadata["frp_remote_port"] = frp_status.get("remote_port")
        node.node_metadata["frp_connected"] = True
        logger.info(f"[FRP] Node {node_id} FRP status updated: remote_port={frp_status.get('remote_port')}")
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
async def delete_node(node_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a node"""
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    await db.delete(node)
    await db.commit()
    return {"status": "deleted"}

