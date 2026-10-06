"""Agent API endpoints"""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, field_validator
from typing import Dict, Any, Optional, List
import hmac
import logging
import re
import os
import signal
import asyncio
from datetime import datetime

from app.config import settings

logger = logging.getLogger(__name__)

SAFE_TUNNEL_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def validate_tunnel_id(v: str) -> str:
    """Validate tunnel_id format against path traversal and dangerous characters"""
    if not isinstance(v, str) or not SAFE_TUNNEL_ID_REGEX.match(v):
        raise ValueError("Invalid tunnel_id: must be 1-64 alphanumeric characters, underscores, or hyphens.")
    return v


def verify_node_token(request: Request):
    """Optional shared-secret gate for the agent API.

    Enforced only when NODE_API_TOKEN is configured on the node. Older panels
    that do not send X-Node-Token keep working when the token is not set.
    """
    if not settings.node_api_token:
        return
    provided = request.headers.get("X-Node-Token", "")
    if not provided or not hmac.compare_digest(provided, settings.node_api_token):
        raise HTTPException(status_code=401, detail="Invalid node token")


router = APIRouter(dependencies=[Depends(verify_node_token)])



class TunnelApply(BaseModel):
    tunnel_id: str
    core: str
    type: str
    spec: Dict[str, Any]

    @field_validator("tunnel_id")
    @classmethod
    def check_tunnel_id(cls, v: str) -> str:
        return validate_tunnel_id(v)


class TunnelRemove(BaseModel):
    tunnel_id: str
    purge: Optional[bool] = False
    ports: Optional[List[Any]] = None
    control_port: Optional[int] = None
    core: Optional[str] = None

    @field_validator("tunnel_id")
    @classmethod
    def check_tunnel_id(cls, v: str) -> str:
        return validate_tunnel_id(v)


class TunnelVerify(BaseModel):
    tunnel_id: str
    core: Optional[str] = None
    mode: Optional[str] = "server"
    ports: Optional[List[Any]] = None
    control_port: Optional[int] = None
    proto: Optional[str] = "udp"

    @field_validator("tunnel_id")
    @classmethod
    def check_tunnel_id(cls, v: str) -> str:
        return validate_tunnel_id(v)


@router.post("/tunnels/verify")
async def verify_tunnel(data: TunnelVerify, request: Request):
    """Verify tunnel process health and listening sockets"""
    adapter_manager = request.app.state.adapter_manager
    try:
        health = await adapter_manager.inspect_tunnel_health(
            tunnel_id=data.tunnel_id,
            tunnel_core=data.core,
            mode=data.mode or "server",
            ports=data.ports,
            control_port=data.control_port,
            proto=data.proto or "udp"
        )
        return {"status": "success", "data": health}
    except Exception as e:
        logger.error(f"Failed to verify tunnel {data.tunnel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tunnels/apply")
async def apply_tunnel(data: TunnelApply, request: Request):
    """Apply tunnel configuration"""
    logger = logging.getLogger(__name__)
    adapter_manager = request.app.state.adapter_manager
    
    logger.info(f"Applying tunnel {data.tunnel_id}: core={data.core}, type={data.type}")
    try:
        await adapter_manager.apply_tunnel(
            tunnel_id=data.tunnel_id,
            tunnel_core=data.core,
            spec=data.spec
        )
        logger.info(f"Tunnel {data.tunnel_id} applied successfully")
        return {"status": "success", "message": "Tunnel applied"}
    except Exception as e:
        logger.error(f"Failed to apply tunnel {data.tunnel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tunnels/remove")
async def remove_tunnel(data: TunnelRemove, request: Request):
    """Remove tunnel"""
    adapter_manager = request.app.state.adapter_manager
    
    try:
        await adapter_manager.remove_tunnel(
            data.tunnel_id,
            purge=bool(data.purge),
            ports=data.ports,
            control_port=data.control_port,
            core=data.core
        )
        return {"status": "success", "message": "Tunnel removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tunnels/status")
async def get_tunnel_status(tunnel_id: str, request: Request):
    """Get tunnel status"""
    try:
        validate_tunnel_id(tunnel_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    adapter_manager = request.app.state.adapter_manager
    
    try:
        status = await adapter_manager.get_tunnel_status(tunnel_id)
        return {"status": "success", "data": status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_status(request: Request):
    """Get node status"""
    adapter_manager = request.app.state.adapter_manager
    
    return {
        "status": "ok",
        "active_tunnels": len(adapter_manager.active_tunnels),
        "tunnels": list(adapter_manager.active_tunnels.keys())
    }


async def _measure_precise_ping(ip_or_host: str, fallback_ports: list = None) -> int:
    """Measures true network layer-3 ICMP or layer-4 TCP handshake latency directly from this node"""
    if not ip_or_host:
        return None
    import os
    import re
    import subprocess
    import time
    import asyncio

    host = ip_or_host.strip()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif ":" in host and host.count(":") == 1:
        host = host.split(":")[0]

    # 1. Try ICMP ping first
    try:
        is_win = os.name == 'nt'
        cmd = ["ping", "-n", "1", "-w", "1000", host] if is_win else ["ping", "-c", "1", "-W", "1", host]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
        except (asyncio.TimeoutError, Exception):
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            stdout = b""
        out = stdout.decode('utf-8', errors='ignore')
        match = re.search(r'time[=<]\s*([0-9.]+)\s*ms', out, re.IGNORECASE)
        if not match:
            match = re.search(r'Average\s*=\s*([0-9]+)ms', out, re.IGNORECASE)
        if match:
            return max(1, round(float(match.group(1))))
    except Exception:
        pass

    # 2. Try fast TCP Handshake probe (Layer 4 true RTT)
    candidate_ports = fallback_ports or [443, 80, 22, 8080, 7000]
    for port in candidate_ports:
        t_start = time.perf_counter()
        try:
            conn = asyncio.open_connection(host, port)
            _, writer = await asyncio.wait_for(conn, timeout=1.0)
            elapsed = (time.perf_counter() - t_start) * 1000
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return max(1, round(elapsed))
        except (ConnectionRefusedError, ConnectionResetError):
            elapsed = (time.perf_counter() - t_start) * 1000
            if elapsed < 800:
                return max(1, round(elapsed))
        except Exception:
            continue

    return None


@router.get("/ping")
async def ping_target(target: str, port: int = None):
    """Measures precise ping/RTT from this node to target IP/host"""
    if not target or not re.match(r"^[a-zA-Z0-9.\-_:\[\]]+$", target.strip()):
        raise HTTPException(status_code=400, detail="Invalid target address")
    fallback_ports = []
    if port:
        fallback_ports.append(port)
    fallback_ports.extend([8888, 8889, 22, 443, 80, 8080, 7000])
    res = await _measure_precise_ping(target, fallback_ports)
    return {"status": "ok", "target": target, "latency_ms": res}


class DecommissionRequest(BaseModel):
    action: Optional[str] = "decommission"
    stop_container: Optional[bool] = True


@router.post("/decommission")
async def decommission_node(request: Request, payload: Optional[DecommissionRequest] = None):
    """Decommission this node: terminate all active tunnels, stop FRP, cancel registration, and self-terminate."""
    logger.warning("Received DECOMMISSION request from panel. Initiating clean teardown...")
    app = request.app
    app.state.decommissioned = True

    # 1. Cancel background registration task immediately so it will never re-register
    if hasattr(app.state, 'registration_task') and app.state.registration_task:
        try:
            app.state.registration_task.cancel()
            logger.info("Registration task cancelled.")
        except Exception as e:
            logger.debug(f"Error cancelling registration task: {e}")

    # 2. Touch persistent decommission marker files so restarted containers refuse to register
    for marker_path in ["/var/lib/smite-node/decommissioned", "/etc/smite-node/decommissioned"]:
        try:
            os.makedirs(os.path.dirname(marker_path), exist_ok=True)
            with open(marker_path, "w") as f:
                f.write(f"decommissioned_at={datetime.utcnow().isoformat()}\n")
            logger.info(f"Created persistent decommission marker: {marker_path}")
        except Exception as e:
            logger.debug(f"Could not write marker to {marker_path}: {e}")

    # 3. Cleanup all active tunnels with kill_processes=True (releases ports, kills gost/rathole/etc.)
    adapter_manager = getattr(app.state, 'adapter_manager', None)
    killed_tunnels = 0
    if adapter_manager:
        killed_tunnels = len(adapter_manager.active_tunnels)
        try:
            await adapter_manager.cleanup(kill_processes=True)
            logger.info(f"Adapter manager cleaned up: {killed_tunnels} tunnels terminated and ports released.")
        except Exception as e:
            logger.error(f"Error during adapter manager cleanup: {e}", exc_info=True)

    # 4. Stop FRP client if running
    try:
        from app.frp_comm_client import frp_comm_client
        if frp_comm_client.is_running():
            await frp_comm_client.stop()
            logger.info("FRP comm client stopped.")
    except Exception as e:
        logger.debug(f"Error stopping FRP comm client: {e}")

    # 5. Stop panel client if running
    if hasattr(app.state, 'h2_client') and app.state.h2_client:
        try:
            app.state.h2_client.decommissioned = True
            await app.state.h2_client.stop()
            logger.info("Panel client stopped.")
        except Exception as e:
            logger.debug(f"Error stopping panel client: {e}")

    # 6. Schedule graceful exit so Docker container stops cleanly
    should_stop = payload.stop_container if payload and payload.stop_container is not None else True
    if should_stop:
        async def delayed_exit():
            await asyncio.sleep(1.5)
            logger.warning("Decommission grace period completed. Stopping agent container process...")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass
            await asyncio.sleep(1.5)
            try:
                os._exit(0)
            except Exception:
                pass

        asyncio.create_task(delayed_exit())

    return {
        "status": "success",
        "message": "Node decommissioned successfully",
        "killed_tunnels": killed_tunnels,
        "container_stopping": should_stop
    }



