"""Core Health and Reset API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel
import logging
import asyncio
import time
import httpx

from app.database import get_db
from app.models import Tunnel, Node, CoreResetConfig, Admin
from app.node_client import NodeClient
from app.routers.auth import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

CORES = ["backhaul", "rathole", "chisel", "frp", "gost"]
_LAST_MANUAL_RESET: Dict[str, float] = {}
RESET_COOLDOWN_SECONDS = 30


class CoreHealthResponse(BaseModel):
    core: str
    nodes_status: Dict[str, Dict[str, Any]]  # Iran nodes
    servers_status: Dict[str, Dict[str, Any]]  # Foreign servers


class ResetConfigResponse(BaseModel):
    core: str
    enabled: bool
    interval_minutes: int
    last_reset: datetime | None
    next_reset: datetime | None


class ResetConfigUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None


@router.get("/health", response_model=List[CoreHealthResponse])
async def get_core_health(request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get health status for all cores"""
    health_data = []
    
    result = await db.execute(select(Node))
    all_nodes = result.scalars().all()
    
    iran_nodes_all = {n.id: n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"}
    foreign_nodes_all = {n.id: n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"}
    
    for core in CORES:
        result = await db.execute(select(Tunnel).where(Tunnel.core == core, Tunnel.status == "active"))
        active_tunnels = result.scalars().all()
        
        node_ids = set(t.node_id for t in active_tunnels if t.node_id)
        
        for tunnel in active_tunnels:
            if tunnel.spec and tunnel.spec.get("foreign_node_id"):
                node_ids.add(tunnel.spec.get("foreign_node_id"))
        
        iran_nodes = {}
        foreign_nodes = {}
        
        client = NodeClient()
        
        async def check_iran_node(node_id, node):
            connection_status = {
                "status": "failed",
                "error_message": None
            }
            
            try:
                response = await client.get_tunnel_status(node_id, "")
                if response and response.get("status") == "ok":
                    connection_status["status"] = "connected"
                else:
                    error_msg = response.get("message", "Node disconnected") if response else "Node not responding"
                    if "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                        connection_status["status"] = "reconnecting"
                    else:
                        connection_status["status"] = "failed"
                    connection_status["error_message"] = error_msg
            except httpx.ConnectError:
                connection_status["status"] = "connecting"
                connection_status["error_message"] = "Connecting to node..."
            except httpx.TimeoutException:
                connection_status["status"] = "reconnecting"
                connection_status["error_message"] = "Connection timeout"
            except Exception as e:
                logger.error(f"Error checking {core} node {node_id} health: {e}")
                connection_status["status"] = "failed"
                connection_status["error_message"] = str(e)
            
            return {
                "id": node_id,
                "name": node.name,
                "role": "iran",
                **connection_status
            }
        
        async def check_foreign_node(node_id, node):
            connection_status = {
                "status": "failed",
                "error_message": None
            }
            
            try:
                response = await client.get_tunnel_status(node_id, "")
                if response and response.get("status") == "ok":
                    connection_status["status"] = "connected"
                else:
                    error_msg = response.get("message", "Node disconnected") if response else "Node not responding"
                    if "timeout" in error_msg.lower() or "connection" in error_msg.lower():
                        connection_status["status"] = "reconnecting"
                    else:
                        connection_status["status"] = "failed"
                    connection_status["error_message"] = error_msg
            except httpx.ConnectError:
                connection_status["status"] = "connecting"
                connection_status["error_message"] = "Connecting to node..."
            except httpx.TimeoutException:
                connection_status["status"] = "reconnecting"
                connection_status["error_message"] = "Connection timeout"
            except Exception as e:
                logger.error(f"Error checking {core} node {node_id} health: {e}")
                connection_status["status"] = "failed"
                connection_status["error_message"] = str(e)
            
            return {
                "id": node_id,
                "name": node.name,
                "role": "foreign",
                **connection_status
            }
        
        iran_tasks = [check_iran_node(node_id, node) for node_id, node in iran_nodes_all.items()]
        foreign_tasks = [check_foreign_node(node_id, node) for node_id, node in foreign_nodes_all.items()]
        
        iran_results = await asyncio.gather(*iran_tasks, return_exceptions=True)
        foreign_results = await asyncio.gather(*foreign_tasks, return_exceptions=True)
        
        for result in iran_results:
            if isinstance(result, Exception):
                continue
            iran_nodes[result["id"]] = result
        
        for result in foreign_results:
            if isinstance(result, Exception):
                continue
            foreign_nodes[result["id"]] = result
        
        health_data.append(CoreHealthResponse(
            core=core,
            nodes_status=iran_nodes,
            servers_status=foreign_nodes
        ))
    
    return health_data


@router.get("/reset-config", response_model=List[ResetConfigResponse])
async def get_reset_configs(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get reset timer configuration for all cores"""
    configs = []
    
    for core in CORES:
        result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.core == core))
        config = result.scalar_one_or_none()
        
        if not config:
            config = CoreResetConfig(
                core=core,
                enabled=False,
                interval_minutes=10
            )
            db.add(config)
            await db.commit()
            await db.refresh(config)
        
        configs.append(ResetConfigResponse(
            core=config.core,
            enabled=config.enabled,
            interval_minutes=config.interval_minutes,
            last_reset=config.last_reset,
            next_reset=config.next_reset
        ))
    
    return configs


@router.put("/reset-config/{core}", response_model=ResetConfigResponse)
async def update_reset_config(
    core: str,
    config_update: ResetConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Admin = Depends(get_current_user)
):
    """Update reset timer configuration for a core"""
    if core not in CORES:
        raise HTTPException(status_code=400, detail=f"Invalid core: {core}")
    
    result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.core == core))
    config = result.scalar_one_or_none()
    
    if not config:
        config = CoreResetConfig(core=core, enabled=False, interval_minutes=10)
        db.add(config)
    
    if config_update.enabled is not None:
        config.enabled = config_update.enabled
    
    if config_update.interval_minutes is not None:
        if config_update.interval_minutes < 1:
            raise HTTPException(status_code=400, detail="Interval must be at least 1 minute")
        config.interval_minutes = config_update.interval_minutes
    
        if config.enabled and config.interval_minutes:
            now = datetime.utcnow()
            if config.last_reset:
                calculated_next = config.last_reset + timedelta(minutes=config.interval_minutes)
                if calculated_next > now:
                    config.next_reset = calculated_next
                else:
                    config.next_reset = now + timedelta(minutes=config.interval_minutes)
            else:
                config.next_reset = now + timedelta(minutes=config.interval_minutes)
    else:
        config.next_reset = None
    
    config.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(config)
    
    return ResetConfigResponse(
        core=config.core,
        enabled=config.enabled,
        interval_minutes=config.interval_minutes,
        last_reset=config.last_reset,
        next_reset=config.next_reset
    )


@router.post("/reset/{core}")
async def manual_reset_core(core: str, request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Manually reset a core (restart servers and clients)"""
    if core not in CORES:
        raise HTTPException(status_code=400, detail=f"Invalid core: {core}")
    
    now_ts = time.time()
    last_ts = _LAST_MANUAL_RESET.get(core, 0.0)
    if (now_ts - last_ts) < RESET_COOLDOWN_SECONDS:
        remaining = max(1, int(RESET_COOLDOWN_SECONDS - (now_ts - last_ts)))
        raise HTTPException(
            status_code=429,
            detail=f"Core '{core}' was reset recently. Please wait {remaining} seconds before resetting again."
        )
    _LAST_MANUAL_RESET[core] = now_ts
    
    try:
        result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.core == core))
        config = result.scalar_one_or_none()
        
        reset_time = datetime.utcnow()
        
        if not config:
            config = CoreResetConfig(core=core, enabled=False, interval_minutes=10)
            db.add(config)
        
        config.last_reset = reset_time
        if config.enabled and config.interval_minutes:
            config.next_reset = reset_time + timedelta(minutes=config.interval_minutes)
        await db.commit()
        await db.refresh(config)
        
        await _reset_core(core, request, db)
        
        return {
            "status": "success",
            "message": f"{core} reset successfully",
            "last_reset": config.last_reset.isoformat() if config.last_reset else None
        }
    except Exception as e:
        logger.error(f"Error resetting {core}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _reset_core(core: str, app_or_request, db: AsyncSession):
    """Internal function to reset a core - handles both foreign and iran nodes"""
    if hasattr(app_or_request, 'app'):
        app = app_or_request.app
    else:
        app = app_or_request
    
    result = await db.execute(select(Tunnel).where(Tunnel.core == core, Tunnel.status == "active"))
    active_tunnels = result.scalars().all()
    
    client = NodeClient()
    
    for tunnel in active_tunnels:
        try:
            iran_node = None
            foreign_node = None
            
            result_all = await db.execute(select(Node))
            all_nodes = result_all.scalars().all()
            
            iran_node_id = getattr(tunnel, "iran_node_id", None) or tunnel.node_id
            if iran_node_id:
                matched_iran = [n for n in all_nodes if n.id == iran_node_id]
                if matched_iran:
                    node_obj = matched_iran[0]
                    if node_obj.node_metadata and node_obj.node_metadata.get("role") != "iran":
                        foreign_node = node_obj
                    else:
                        iran_node = node_obj
            
            foreign_node_id = getattr(tunnel, "foreign_node_id", None)
            if foreign_node_id and not foreign_node:
                matched_foreign = [n for n in all_nodes if n.id == foreign_node_id]
                if matched_foreign:
                    foreign_node = matched_foreign[0]
            
            if not foreign_node:
                foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
                if foreign_nodes:
                    foreign_node = foreign_nodes[0]
            
            if not iran_node:
                iran_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
                if iran_nodes:
                    iran_node = iran_nodes[0]
            
            if not foreign_node or not iran_node:
                logger.warning(f"Tunnel {tunnel.id}: Missing foreign or iran node, skipping reset")
                continue
            
            iran_node_ip = iran_node.node_metadata.get("ip_address")
            if not iran_node_ip:
                logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping reset")
                continue
            
            foreign_node_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
            
            from app.spec_builder import build_tunnel_node_specs
            try:
                server_spec, client_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip or iran_node_ip)
            except Exception as e:
                logger.error(f"Spec builder failed for tunnel {tunnel.id} during reset: {e}")
                continue
            
            if not iran_node.node_metadata.get("api_address"):
                iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                await db.commit()
            
            logger.info(f"Restarting tunnel {tunnel.id}: applying server config to iran node {iran_node.id}")
            server_response = await client.send_to_node(
                node_id=iran_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": core,
                    "type": tunnel.type,
                    "spec": server_spec
                }
            )
            
            if server_response.get("status") == "error":
                error_msg = server_response.get("message", "Unknown error from iran node")
                logger.error(f"Failed to restart tunnel {tunnel.id} on iran node {iran_node.id}: {error_msg}")
                continue
            
            if not foreign_node.node_metadata.get("api_address"):
                foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                await db.commit()
            
            logger.info(f"Restarting tunnel {tunnel.id}: applying client config to foreign node {foreign_node.id}")
            client_response = await client.send_to_node(
                node_id=foreign_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": core,
                    "type": tunnel.type,
                    "spec": client_spec
                }
            )
            
            if client_response.get("status") == "error":
                error_msg = client_response.get("message", "Unknown error from foreign node")
                logger.error(f"Failed to restart tunnel {tunnel.id} on foreign node {foreign_node.id}: {error_msg}")
            else:
                logger.info(f"Successfully restarted tunnel {tunnel.id} on both nodes")
            
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to restart tunnel {tunnel.id}: {e}", exc_info=True)

