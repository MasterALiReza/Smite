"""Agent API endpoints"""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, field_validator
from typing import Dict, Any, Optional, List
import logging
import re
import os
import signal
import asyncio
import time
from datetime import datetime

from .. import agent_auth
from ..config import settings

logger = logging.getLogger(__name__)

SAFE_TUNNEL_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Bumped whenever the agent gains capabilities the panel/CLI need to detect.
AGENT_API_VERSION = 6
# Feature flags the panel checks before sending options an older agent would
# silently ignore (options that must be identical on both ends of a tunnel).
AGENT_CAPABILITIES = [
    "hot_reload",
    "token_bootstrap",
    "bootstrap_proof_v1",
    "pinned_https",
    "apply_authoritative_ids",
    "remove_report",
    "frp_tcp_mux_option",
    "udp_low_latency",
    # hot reload re-execs from sys.orig_argv, so repeated reloads are safe
    "safe_reexec",
    "drained_reexec",
    "release_identity",
    # durable lockout of the agent API even if the token file disappears
    "auth_enforce",
]
# Changes on every (re)start of the agent process; lets the CLI confirm a hot reload.
AGENT_STARTED_AT = time.time()


def _read_agent_release(root=None):
    import hashlib
    import json
    from pathlib import Path

    root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    try:
        content = (root / "app/.smite-release.json").read_bytes()
        manifest = json.loads(content)
        if manifest.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("revision", "")):
            return None
        sources = {"main.py"} | {p.relative_to(root).as_posix() for p in (root / "app").rglob("*.py")}
        if set(manifest["files"]) != sources:
            return None
        for name, digest in manifest["files"].items():
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                return None
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                return None
        return {"revision": manifest["revision"], "manifest_sha256": hashlib.sha256(content).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


AGENT_RELEASE = _read_agent_release()


def validate_tunnel_id(v: str) -> str:
    """Validate tunnel_id format against path traversal and dangerous characters"""
    if not isinstance(v, str) or not SAFE_TUNNEL_ID_REGEX.match(v):
        raise ValueError("Invalid tunnel_id: must be 1-64 alphanumeric characters, underscores, or hyphens.")
    return v


def verify_node_token(request: Request):
    """Shared-secret gate for the agent API.

    Enforced when a token is configured on the node: either NODE_API_TOKEN in .env
    or the per-node token provisioned by the panel (/api/agent/auth/bootstrap).
    After the panel has verified provisioning it sets a durable enforcement flag;
    from then on a missing token keeps the API closed instead of falling open.
    Read-only GET /status from the node itself (Docker healthcheck, smite-node CLI)
    is allowed without a token.
    """
    expected = agent_auth.get_effective_token()
    is_status = request.method == "GET" and request.url.path.rstrip("/").endswith("/api/agent/status")
    if is_status and agent_auth.is_loopback(request.client.host if request.client else None):
        return
    if not expected:
        if not agent_auth.is_enforced():
            return
        raise HTTPException(status_code=401, detail="Agent token not provisioned; run `smite-node reset-token` locally to re-pair")
    provided = request.headers.get("X-Node-Token", "")
    if not agent_auth.tokens_match(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid node token")


router = APIRouter(dependencies=[Depends(verify_node_token)])

# Token provisioning is authorized inside the handler (it must work before a token exists)
auth_router = APIRouter()


class TokenBootstrap(BaseModel):
    token: str


@auth_router.post("/auth/bootstrap")
async def bootstrap_token(data: TokenBootstrap, request: Request):
    """Provision or rotate the per-node agent token."""
    new_token = (data.token or "").strip()
    if not agent_auth.is_valid_token_format(new_token):
        raise HTTPException(status_code=400, detail="Invalid token format")

    if agent_auth.get_env_token():
        provided = request.headers.get("X-Node-Token", "")
        if agent_auth.tokens_match(provided, agent_auth.get_env_token()):
            return {"status": "success", "mode": "env", "enforced": True}
        raise HTTPException(status_code=409, detail="Node uses a static NODE_API_TOKEN from .env")

    current = agent_auth.get_file_token()
    if current:
        provided = request.headers.get("X-Node-Token", "")
        if not agent_auth.tokens_match(provided, current):
            raise HTTPException(status_code=401, detail="Node token already provisioned")
        if agent_auth.tokens_match(new_token, current):
            return {"status": "success", "mode": "panel", "enforced": True, "changed": False, "auth_mode": agent_auth.auth_mode()}
        try:
            agent_auth.set_file_token(new_token)
        except Exception as error:
            logger.error(f"Failed to rotate agent token: {error}")
            raise HTTPException(status_code=500, detail="Failed to persist token")
    else:
        if agent_auth.is_enforced():
            raise HTTPException(
                status_code=409,
                detail="Agent auth is enforced and no token is configured; unlock locally with `smite-node reset-token`",
            )
        proof = request.headers.get("X-Bootstrap-Proof", "") or request.headers.get("X-Bootstrap-Secret", "")
        try:
            agent_auth.provision_file_token(new_token, proof)
        except agent_auth.BootstrapProofError as error:
            raise HTTPException(status_code=403, detail=str(error))
        except Exception as error:
            logger.error(f"Failed to persist agent token: {error}")
            raise HTTPException(status_code=500, detail="Failed to persist token")

    client_host = request.client.host if request.client else "unknown"
    logger.warning(f"Agent API token {'rotated' if current else 'provisioned'} by {client_host}; unauthenticated agent API access is now disabled")
    return {"status": "success", "mode": "panel", "enforced": True, "changed": True, "auth_mode": agent_auth.auth_mode()}


@auth_router.post("/auth/enforce")
async def enforce_agent_auth(request: Request):
    """Persist the panel's enforcement decision (requires the current token).

    This is deliberately separate from bootstrap: the panel first proves it can
    authenticate with the provisioned token, then locks the node so a lost token
    file cannot reopen the API.
    """
    expected = agent_auth.get_effective_token()
    if not expected:
        raise HTTPException(status_code=409, detail="No agent token configured; refusing to enforce")
    provided = request.headers.get("X-Node-Token", "")
    if not agent_auth.tokens_match(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid node token")
    try:
        agent_auth.set_enforced()
    except Exception as e:
        logger.error(f"Failed to persist agent auth enforcement flag: {e}")
        raise HTTPException(status_code=500, detail="Failed to persist enforcement flag")
    return {
        "status": "success",
        "mode": agent_auth.token_source(),
        "enforced": True,
        "auth_mode": agent_auth.auth_mode(),
    }



class TunnelApply(BaseModel):
    tunnel_id: str
    core: str
    type: str
    spec: Dict[str, Any]
    # All tunnel ids the panel has for this node; lets the node drop leftovers
    # that occupy the ports of the tunnel being applied.
    authoritative_ids: Optional[List[str]] = None

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
        auth_ids = None
        if data.authoritative_ids is not None:
            auth_ids = [t for t in data.authoritative_ids if isinstance(t, str) and SAFE_TUNNEL_ID_REGEX.match(t)]
        await adapter_manager.apply_tunnel(
            tunnel_id=data.tunnel_id,
            tunnel_core=data.core,
            spec=data.spec,
            authoritative_ids=auth_ids,
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
        result = await adapter_manager.remove_tunnel(
            data.tunnel_id,
            purge=bool(data.purge),
            ports=data.ports,
            control_port=data.control_port,
            core=data.core
        )
        response = {"status": "success", "message": "Tunnel removed"}
        if isinstance(result, dict):
            response.update({
                "found": result.get("found", False),
                "killed_pids": result.get("killed_pids", []),
                "stuck_ports": result.get("stuck_ports", []),
            })
        return response
    except Exception as e:
        logger.error(f"Failed to remove tunnel {data.tunnel_id}: {e}", exc_info=True)
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


@router.get("/tunnels/{tunnel_id}/probe")
async def probe_tunnel(tunnel_id: str, request: Request):
    """
    Active live verification for a specific tunnel:
    1. Checks proxy process health and listening ports
    2. Probes local ingress socket (if listening locally) and measures RTT
    """
    try:
        validate_tunnel_id(tunnel_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    adapter_manager = getattr(request.app.state, 'adapter_manager', None)
    if not adapter_manager:
        raise HTTPException(status_code=503, detail="Adapter manager not initialized")

    try:
        status = await adapter_manager.get_tunnel_status(tunnel_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    proc_alive = status.get("process_running", False)
    listening_ports = status.get("listening_ports", [])
    core = status.get("core", "unknown")

    ingress_port = None
    ingress_port_open = False
    data_flow = False
    rtt_ms = None
    port_proto = "tcp"

    if listening_ports:
        for p in listening_ports:
            p_type = p.get("type", "")
            p_num = p.get("port")
            if p_num and isinstance(p_num, int):
                if "service" in p_type:
                    ingress_port = p_num
                    port_proto = "udp" if "udp" in p_type else "tcp"
                    break
                elif ingress_port is None:
                    ingress_port = p_num
                    port_proto = "udp" if "udp" in p_type else "tcp"

        if ingress_port:
            if port_proto == "udp":
                # For UDP, socket is already actively listening per inspect_tunnel_health
                ingress_port_open = True
                data_flow = proc_alive
                rtt_ms = 1
            else:
                t_start = time.perf_counter()
                try:
                    conn = asyncio.open_connection("127.0.0.1", ingress_port)
                    reader, writer = await asyncio.wait_for(conn, timeout=1.5)
                    elapsed = (time.perf_counter() - t_start) * 1000
                    ingress_port_open = True
                    rtt_ms = max(1, round(elapsed))

                    try:
                        writer.write(b"PING\r\n")
                        await asyncio.wait_for(writer.drain(), timeout=0.8)
                        data_flow = True
                    except Exception:
                        data_flow = True
                    finally:
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                except Exception:
                    ingress_port_open = False
                    data_flow = False
                    rtt_ms = None
    else:
        # Node has no local ingress listening port (e.g. forward client mode)
        ingress_port_open = proc_alive
        data_flow = proc_alive

    return {
        "status": "success",
        "tunnel_id": tunnel_id,
        "process_running": proc_alive,
        "core": core,
        "healthy": status.get("healthy", False),
        "ingress_port": ingress_port,
        "ingress_port_open": ingress_port_open,
        "data_flow": data_flow,
        "tunnel_rtt_ms": rtt_ms
    }


@router.get("/status")
async def get_status(request: Request):
    """Get node status"""
    adapter_manager = getattr(request.app.state, "adapter_manager", None)
    active = adapter_manager.active_tunnels if adapter_manager else {}

    return {
        "status": "ok",
        "active_tunnels": len(active),
        "tunnels": list(active.keys()),
        "api_version": AGENT_API_VERSION,
        "capabilities": AGENT_CAPABILITIES,
        "agent_started_at": AGENT_STARTED_AT,
        "release": AGENT_RELEASE,
        "hot_reload": True,
        "token_enforced": bool(agent_auth.get_effective_token()),
        "token_source": agent_auth.token_source(),
        "auth_mode": agent_auth.auth_mode(),
        "auth_enforced": agent_auth.is_enforced(),
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
    target = (target or "").strip()
    # Leading '-' would be parsed by ping(8) as an option (argument injection)
    if not target or target.startswith("-") or len(target) > 255 or not re.match(r"^[a-zA-Z0-9.\-_:\[\]]+$", target):
        raise HTTPException(status_code=400, detail="Invalid target address")
    if port is not None and not (1 <= int(port) <= 65535):
        raise HTTPException(status_code=400, detail="Invalid port")
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



