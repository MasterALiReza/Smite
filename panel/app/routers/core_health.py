"""Core Health and Reset API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any, Optional
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


_HEALTH_CACHE: Dict[str, Any] = {"timestamp": 0.0, "data": []}
HEALTH_CACHE_TTL = 4.0  # 4 seconds cache


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
    """Get health status for all cores with concurrent checking and fast response caching"""
    global _HEALTH_CACHE
    now = time.time()
    
    # Return warm cache if fresh
    if (now - _HEALTH_CACHE["timestamp"] < HEALTH_CACHE_TTL) and _HEALTH_CACHE["data"]:
        return _HEALTH_CACHE["data"]

    result = await db.execute(select(Node))
    all_nodes = result.scalars().all()
    
    iran_nodes_all = {n.id: n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"}
    foreign_nodes_all = {n.id: n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"}
    
    client = NodeClient()
    
    async def probe_node(node_id: str, timeout_sec: float) -> tuple[bool, str, Optional[str]]:
        try:
            response = await asyncio.wait_for(client.get_tunnel_status(node_id, ""), timeout=timeout_sec)
            if response and response.get("status") in ("ok", "success"):
                return True, "connected", None
            
            error_msg = response.get("message", "Node disconnected") if response else "Node not responding"
            low_msg = error_msg.lower()
            if "timeout" in low_msg or "connection" in low_msg:
                return False, "reconnecting", error_msg
            return False, "failed", error_msg
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return False, "reconnecting", "Connection timeout"
        except httpx.ConnectError:
            return False, "reconnecting", "Connecting to node..."
        except Exception as e:
            return False, "failed", str(e)

    async def check_single_node(node_id: str, node: Node, role: str):
        # 1. Primary probe with 3.5s timeout (accommodates international route latency and DPI jitter)
        success, status, error_message = await probe_node(node_id, timeout_sec=3.5)
        
        # 2. Resilient debounce: if probe experienced a transient timeout or network error, do an immediate retry
        # This completely eliminates false-positive flapping caused by momentary packet drops
        if not success and status == "reconnecting":
            await asyncio.sleep(0.25)
            retry_success, retry_status, retry_err = await probe_node(node_id, timeout_sec=2.5)
            if retry_success:
                success, status, error_message = retry_success, retry_status, retry_err
        
        return {
            "id": node_id,
            "name": node.name,
            "role": role,
            "status": status,
            "error_message": error_message if not success else None
        }
    
    # Check all nodes once in parallel
    tasks = [
        check_single_node(nid, n, "iran") for nid, n in iran_nodes_all.items()
    ] + [
        check_single_node(nid, n, "foreign") for nid, n in foreign_nodes_all.items()
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    iran_nodes = {}
    foreign_nodes = {}
    
    for r in results:
        if isinstance(r, Exception) or not isinstance(r, dict):
            continue
        if r.get("role") == "iran":
            iran_nodes[r["id"]] = r
        else:
            foreign_nodes[r["id"]] = r
    
    health_data = [
        CoreHealthResponse(
            core=core,
            nodes_status=iran_nodes,
            servers_status=foreign_nodes
        )
        for core in CORES
    ]
    
    _HEALTH_CACHE = {
        "timestamp": now,
        "data": health_data
    }
    
    return health_data


@router.get("/reset-config", response_model=List[ResetConfigResponse])
async def get_reset_configs(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get reset timer configuration for all cores in a single batch query"""
    result = await db.execute(select(CoreResetConfig).where(CoreResetConfig.core.in_(CORES)))
    existing_configs = {c.core: c for c in result.scalars().all()}
    
    has_new = False
    for core in CORES:
        if core not in existing_configs:
            config = CoreResetConfig(
                core=core,
                enabled=False,
                interval_minutes=10
            )
            db.add(config)
            existing_configs[core] = config
            has_new = True
    
    if has_new:
        await db.commit()
        for core in CORES:
            await db.refresh(existing_configs[core])
    
    configs = [
        ResetConfigResponse(
            core=existing_configs[core].core,
            enabled=existing_configs[core].enabled,
            interval_minutes=existing_configs[core].interval_minutes,
            last_reset=existing_configs[core].last_reset,
            next_reset=existing_configs[core].next_reset
        )
        for core in CORES
        if core in existing_configs
    ]
    
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
        
        global _HEALTH_CACHE
        _HEALTH_CACHE["timestamp"] = 0.0
        
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
            
            foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
            foreign_node_id = getattr(tunnel, "foreign_node_id", None)
            if foreign_node_id:
                matched_foreign = [n for n in all_nodes if n.id == foreign_node_id]
                if matched_foreign:
                    foreign_node = matched_foreign[0]
                else:
                    logger.warning(f"Tunnel {tunnel.id}: Assigned foreign node {foreign_node_id} not found, skipping reset to prevent node drift")
                    continue
            elif len(foreign_nodes) == 1:
                foreign_node = foreign_nodes[0]
            elif len(foreign_nodes) > 1:
                logger.warning(f"Tunnel {tunnel.id}: Multiple foreign nodes available and no foreign_node_id specified, skipping reset")
                continue
            
            iran_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "iran"]
            if not iran_node:
                if len(iran_nodes) == 1:
                    iran_node = iran_nodes[0]
                else:
                    logger.warning(f"Tunnel {tunnel.id}: Multiple or zero iran nodes and no iran_node_id matched, skipping reset")
                    continue
            
            if not foreign_node or not iran_node:
                logger.warning(f"Tunnel {tunnel.id}: Missing foreign or iran node, skipping reset")
                continue
            
            iran_node_ip = iran_node.node_metadata.get("ip_address")
            if not iran_node_ip:
                logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping reset")
                continue
            
            foreign_node_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
            
            from app.spec_builder import build_tunnel_node_specs
            force_direct = bool((tunnel.spec or {}).get("force_direct", False)) if isinstance(tunnel.spec, dict) else False
            if force_direct:
                is_reverse = False
            elif tunnel.core in {"gost", "chisel"}:
                if tunnel.is_reverse is not None:
                    is_reverse = bool(tunnel.is_reverse)
                elif isinstance(tunnel.spec, dict) and tunnel.spec.get("is_reverse") is not None:
                    is_reverse = bool(tunnel.spec.get("is_reverse"))
                elif getattr(tunnel, "foreign_node_id", None) or getattr(tunnel, "iran_node_id", None):
                    is_reverse = True
                else:
                    is_reverse = False
            else:
                is_reverse = bool(tunnel.is_reverse) if tunnel.is_reverse is not None else True

            try:
                iran_spec, foreign_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip or iran_node_ip)
            except Exception as e:
                logger.error(f"Spec builder failed for tunnel {tunnel.id} during reset: {e}")
                continue
            
            if not iran_node.node_metadata.get("api_address"):
                iran_node.node_metadata["api_address"] = f"http://{iran_node.node_metadata.get('ip_address', iran_node.fingerprint)}:{iran_node.node_metadata.get('api_port', 8888)}"
                await db.commit()

            if not foreign_node.node_metadata.get("api_address"):
                foreign_node.node_metadata["api_address"] = f"http://{foreign_node.node_metadata.get('ip_address', foreign_node.fingerprint)}:{foreign_node.node_metadata.get('api_port', 8888)}"
                await db.commit()

            if iran_spec.get("mode") == "server":
                first_node, first_spec, first_role = iran_node, dict(iran_spec), f"iran node {iran_node.id} (server)"
                second_node, second_spec, second_role = foreign_node, dict(foreign_spec), f"foreign node {foreign_node.id} (client)"
            else:
                first_node, first_spec, first_role = foreign_node, dict(foreign_spec), f"foreign node {foreign_node.id} (server)"
                second_node, second_spec, second_role = iran_node, dict(iran_spec), f"iran node {iran_node.id} (client)"
            
            first_spec["force_restart"] = True
            logger.info(f"Restarting tunnel {tunnel.id}: applying config to {first_role}")
            first_response = await client.send_to_node(
                node_id=first_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": core,
                    "type": tunnel.type,
                    "spec": first_spec
                }
            )
            
            if first_response.get("status") == "error":
                error_msg = first_response.get("message", f"Unknown error from {first_role}")
                logger.error(f"Failed to restart tunnel {tunnel.id} on {first_role}: {error_msg}")
                continue

            second_spec["force_restart"] = True
            logger.info(f"Restarting tunnel {tunnel.id}: applying config to {second_role}")
            second_response = await client.send_to_node(
                node_id=second_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": core,
                    "type": tunnel.type,
                    "spec": second_spec
                }
            )
            
            if second_response.get("status") == "error":
                error_msg = second_response.get("message", f"Unknown error from {second_role}")
                logger.error(f"Failed to restart tunnel {tunnel.id} on {second_role}: {error_msg}")
            else:
                logger.info(f"Successfully restarted tunnel {tunnel.id} on both nodes")
            
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to restart tunnel {tunnel.id}: {e}", exc_info=True)

