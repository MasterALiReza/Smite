"""Agent API endpoints"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
import logging

router = APIRouter()
logger = logging.getLogger(__name__)



class TunnelApply(BaseModel):
    tunnel_id: str
    core: str
    type: str
    spec: Dict[str, Any]


class TunnelRemove(BaseModel):
    tunnel_id: str


class TunnelVerify(BaseModel):
    tunnel_id: str
    core: Optional[str] = None
    mode: Optional[str] = "server"
    ports: Optional[List[int]] = None
    control_port: Optional[int] = None
    proto: Optional[str] = "udp"


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
        await adapter_manager.remove_tunnel(data.tunnel_id)
        return {"status": "success", "message": "Tunnel removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tunnels/status")
async def get_tunnel_status(tunnel_id: str, request: Request):
    """Get tunnel status"""
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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
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
    fallback_ports = []
    if port:
        fallback_ports.append(port)
    fallback_ports.extend([8888, 8889, 22, 443, 80, 8080, 7000])
    res = await _measure_precise_ping(target, fallback_ports)
    return {"status": "ok", "target": target, "latency_ms": res}


class AdapterSync(BaseModel):
    code: str


@router.post("/system/sync_adapters")
async def sync_adapters(data: AdapterSync):
    """Sync and hot-update core adapters from panel"""
    try:
        from pathlib import Path
        for target in ["/app/app/core_adapters.py", "/opt/smite-node/app/core_adapters.py", "app/core_adapters.py"]:
            p = Path(target)
            if p.parent.exists():
                p.write_text(data.code, encoding="utf-8")
                logger.info(f"Successfully updated adapters at {target}")
                return {"status": "success", "message": f"Adapters updated at {target}"}
        return {"status": "error", "message": "Target path not found"}
    except Exception as e:
        logger.error(f"Failed to sync adapters: {e}")
        raise HTTPException(status_code=500, detail=str(e))



