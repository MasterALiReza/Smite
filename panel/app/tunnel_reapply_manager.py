"""Tunnel auto reapply manager"""
import asyncio
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Settings, Tunnel
from app.node_client import NodeClient
from fastapi import Request

logger = logging.getLogger(__name__)


class TunnelReapplyManager:
    """Manages automatic tunnel reapplication and self-healing"""
    
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
        self.heal_task: Optional[asyncio.Task] = None
        self.enabled = False
        self.interval = 60
        self.interval_unit = "minutes"
        self.request: Optional[Request] = None
    
    async def load_settings(self):
        """Load settings from database"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Settings).where(Settings.key == "tunnel"))
            setting = result.scalar_one_or_none()
            if setting and setting.value:
                self.enabled = setting.value.get("auto_reapply_enabled", False)
                self.interval = setting.value.get("auto_reapply_interval", 60)
                self.interval_unit = setting.value.get("auto_reapply_interval_unit", "minutes")
            else:
                self.enabled = False
                self.interval = 60
                self.interval_unit = "minutes"
    
    async def start(self):
        """Start auto reapply and autonomous self-healing tasks"""
        await self.stop()
        await self.load_settings()
        
        # Self-healing watchdog runs 24/7 in the background
        self.heal_task = asyncio.create_task(self._auto_heal_loop())
        logger.info("Tunnel self-healing watchdog task started (45s interval)")
        
        if self.enabled:
            self.task = asyncio.create_task(self._reapply_loop())
            logger.info(f"Tunnel auto reapply task started: interval={self.interval} {self.interval_unit}")
    
    async def stop(self):
        """Stop auto reapply and auto-healing tasks"""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            
        if self.heal_task:
            self.heal_task.cancel()
            try:
                await self.heal_task
            except asyncio.CancelledError:
                pass
            self.heal_task = None
            
        logger.info("Tunnel reapply and self-healing tasks stopped")
    
    async def _auto_heal_loop(self):
        """
        Autonomous Self-Healing Watchdog:
        Continuously monitors active and degraded tunnels. If a node went down and recovered,
        it automatically re-synchronizes the tunnel configuration to restore traffic immediately.
        """
        logger.info("Self-healing watchdog loop active")
        while True:
            try:
                await asyncio.sleep(45)
                async with AsyncSessionLocal() as session:
                    res = await session.execute(
                        select(Tunnel).where(
                            (Tunnel.status == "error") | (Tunnel.status == "active")
                        )
                    )
                    tunnels = res.scalars().all()
                    if not tunnels:
                        continue
                        
                    client = NodeClient()
                    for tunnel in tunnels:
                        try:
                            # If tunnel is in error status, check if its nodes are reachable
                            if tunnel.status == "error":
                                iran_id = tunnel.iran_node_id or tunnel.node_id
                                foreign_id = tunnel.foreign_node_id
                                
                                ir_ok = False
                                fn_ok = True
                                
                                if iran_id:
                                    try:
                                        resp = await asyncio.wait_for(client.get_tunnel_status(iran_id, ""), timeout=2.0)
                                        ir_ok = bool(resp and resp.get("status") == "ok")
                                    except Exception:
                                        ir_ok = False
                                        
                                if foreign_id:
                                    try:
                                        resp = await asyncio.wait_for(client.get_tunnel_status(foreign_id, ""), timeout=2.0)
                                        fn_ok = bool(resp and resp.get("status") == "ok")
                                    except Exception:
                                        fn_ok = False
                                        
                                if ir_ok and fn_ok:
                                    logger.info(f"Self-Healing: Nodes for tunnel '{tunnel.name}' ({tunnel.core}) are back online. Auto-reapplying...")
                                    applied_ok = await self._reapply_tunnel_safe(tunnel, session, client)
                                    if applied_ok:
                                        tunnel.status = "active"
                                        tunnel.error_message = None
                                        from sqlalchemy.orm.attributes import flag_modified
                                        flag_modified(tunnel, "spec")
                                        await session.commit()
                                        logger.info(f"Self-Healing: Successfully revived and restored tunnel '{tunnel.name}'")
                        except Exception as t_err:
                            logger.debug(f"Self-healing check error for tunnel {tunnel.id}: {t_err}")
            except asyncio.CancelledError:
                logger.info("Self-healing watchdog loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in self-healing watchdog loop: {e}", exc_info=True)
    
    async def _reapply_loop(self):
        """Background task for automatic tunnel reapplication"""
        try:
            while True:
                await self.load_settings()
                
                if not self.enabled:
                    await asyncio.sleep(60)
                    continue
                
                if self.interval_unit == "hours":
                    sleep_seconds = self.interval * 3600
                else:
                    sleep_seconds = self.interval * 60
                
                await asyncio.sleep(sleep_seconds)
                
                if not self.enabled:
                    continue
                
                try:
                    await self._reapply_all_tunnels()
                except Exception as e:
                    logger.error(f"Error in automatic tunnel reapply: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Tunnel reapply loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Tunnel reapply loop error: {e}", exc_info=True)
    
    async def _reapply_all_tunnels(self):
        """Reapply all active tunnels according to schedule"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            if not tunnels:
                logger.debug("No active tunnels to reapply")
                return
            
            client = NodeClient()
            applied = 0
            failed = 0
            
            for tunnel in tunnels:
                try:
                    success = await self._reapply_tunnel_safe(tunnel, session, client)
                    if success:
                        applied += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(f"Error reapplying tunnel {tunnel.id}: {e}", exc_info=True)
                    failed += 1
            
            logger.info(f"Auto reapply completed: {applied} applied, {failed} failed")

    async def _reapply_tunnel_safe(self, tunnel: Tunnel, session: AsyncSession, client: NodeClient) -> bool:
        """Safely re-apply a single tunnel to its node(s)"""
        from app.routers.tunnels import prepare_frp_spec_for_node
        from app.models import Node
        from starlette.requests import Request as StarletteRequest
        from starlette.datastructures import Headers
        import hashlib
        from app.utils import is_valid_ipv6_address, parse_address_port

        fake_request = StarletteRequest(
            scope={
                "type": "http",
                "method": "POST",
                "path": "/api/tunnels/reapply",
                "headers": Headers({}).raw,
                "query_string": b"",
            }
        )

        is_reverse_tunnel = tunnel.core in {"rathole", "backhaul", "chisel", "frp"} or (tunnel.core == "gost" and (tunnel.foreign_node_id or tunnel.iran_node_id))
        
        if is_reverse_tunnel:
            iran_node_id = tunnel.iran_node_id or tunnel.node_id
            if not iran_node_id:
                return False
                
            result = await session.execute(select(Node).where(Node.id == iran_node_id))
            iran_node = result.scalar_one_or_none()
            if not iran_node:
                return False
            
            result = await session.execute(select(Node))
            all_nodes = result.scalars().all()
            foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
            if not foreign_nodes:
                return False
            
            foreign_node = None
            if tunnel.foreign_node_id:
                matched = [n for n in all_nodes if n.id == tunnel.foreign_node_id]
                if matched:
                    foreign_node = matched[0]
            if not foreign_node:
                foreign_node = foreign_nodes[0]
            
            spec = tunnel.spec.copy() if tunnel.spec else {}
            
            if tunnel.core == "frp":
                bind_port = spec.get("bind_port", 7000)
                token = spec.get("token")
                
                iran_node_ip = iran_node.node_metadata.get("ip_address")
                if not iran_node_ip:
                    logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                    return False
                
                transport_type = getattr(tunnel, "transport_type", None) or spec.get("transport_type") or spec.get("transport") or "tcp"
                security_type = getattr(tunnel, "security_type", None) or spec.get("security_type") or "tls"
                custom_sni = getattr(tunnel, "custom_sni", None) or getattr(tunnel, "stealth_domain", None) or spec.get("custom_sni") or spec.get("stealth_domain")
                use_encryption = spec.get("use_encryption", True)
                use_compression = spec.get("use_compression", True)
                
                spec_for_iran = spec.copy()
                spec_for_iran["mode"] = "server"
                spec_for_iran["bind_port"] = bind_port
                spec_for_iran["transport_type"] = transport_type
                spec_for_iran["security_type"] = security_type
                if token:
                    spec_for_iran["token"] = token
                
                spec_for_foreign = spec.copy()
                spec_for_foreign["mode"] = "client"
                spec_for_foreign["server_addr"] = iran_node_ip
                spec_for_foreign["server_port"] = bind_port
                spec_for_foreign["transport_type"] = transport_type
                spec_for_foreign["security_type"] = security_type
                spec_for_foreign["custom_sni"] = custom_sni
                spec_for_foreign["use_encryption"] = use_encryption
                spec_for_foreign["use_compression"] = use_compression
                if token:
                    spec_for_foreign["token"] = token
                tunnel_type = tunnel.type.lower() if tunnel.type else "tcp"
                if tunnel_type not in ["tcp", "udp"]:
                    tunnel_type = "tcp"
                spec_for_foreign["type"] = tunnel_type
                local_ip = spec.get("local_ip") or "127.0.0.1"
                spec_for_foreign["local_ip"] = local_ip
                
                ports = spec.get("ports", [])
                if not ports:
                    local_port = spec.get("local_port")
                    remote_port = spec.get("remote_port") or spec.get("listen_port")
                    if remote_port and local_port:
                        spec_for_foreign["ports"] = [{"local": int(local_port), "remote": int(remote_port)}]
                    elif remote_port:
                        spec_for_foreign["ports"] = [{"local": int(remote_port), "remote": int(remote_port)}]
                    elif local_port:
                        spec_for_foreign["ports"] = [{"local": int(local_port), "remote": int(local_port)}]
                else:
                    spec_for_foreign["ports"] = ports
                
                server_response = await client.send_to_node(
                    node_id=iran_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": tunnel.id,
                        "core": tunnel.core,
                        "type": tunnel.type,
                        "spec": spec_for_iran
                    }
                )
                
                if server_response.get("status") == "error":
                    logger.error(f"Failed to reapply tunnel {tunnel.id} to iran node: {server_response.get('message')}")
                    return False
                
                client_response = await client.send_to_node(
                    node_id=foreign_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": tunnel.id,
                        "core": tunnel.core,
                        "type": tunnel.type,
                        "spec": spec_for_foreign
                    }
                )
                
                if client_response.get("status") == "error":
                    logger.error(f"Failed to reapply tunnel {tunnel.id} to foreign node: {client_response.get('message')}")
                    return False
                
                return server_response.get("status") == "success" and client_response.get("status") == "success"
            else:
                server_spec = spec.copy()
                server_spec["mode"] = "server"
                client_spec = spec.copy()
                client_spec["mode"] = "client"
                
                if tunnel.core == "rathole":
                    transport = server_spec.get("transport") or server_spec.get("type") or "tcp"
                    token = server_spec.get("token")
                    
                    ports = server_spec.get("ports") or []
                    if not ports:
                        proxy_port = server_spec.get("remote_port") or server_spec.get("listen_port")
                        if proxy_port:
                            ports = [int(proxy_port) if isinstance(proxy_port, (int, str)) and str(proxy_port).isdigit() else proxy_port]
                    
                    if not ports or not token:
                        return False
                    
                    control_port = server_spec.get("control_port")
                    if not control_port:
                        remote_addr = server_spec.get("remote_addr", "")
                        _, control_port, _ = parse_address_port(remote_addr) if remote_addr else (None, None, None)
                    port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                    assigned_control_port = 25000 + (port_hash % 25000)

                    if not control_port or int(control_port) < 24000:
                        control_port = assigned_control_port
                        tunnel.spec["control_port"] = control_port

                    use_noise = server_spec.get("noise") or server_spec.get("use_noise", False)
                    if use_noise:
                        from app.utils import generate_noise_keys
                        server_keys = generate_noise_keys()
                        client_keys = generate_noise_keys()
                        server_spec["server_private_key"] = server_keys["private_key"]
                        server_spec["server_public_key"] = server_keys["public_key"]
                        server_spec["client_public_key"] = client_keys["public_key"]
                        client_spec["client_private_key"] = client_keys["private_key"]
                        client_spec["client_public_key"] = client_keys["public_key"]
                        client_spec["server_public_key"] = server_keys["public_key"]
                    
                    server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
                    server_spec["control_port"] = control_port
                    server_spec["token"] = token
                    server_spec["transport"] = transport
                    server_spec["ports"] = ports
                    
                    iran_node_ip = iran_node.node_metadata.get("ip_address")
                    if not iran_node_ip:
                        return False
                    if is_valid_ipv6_address(iran_node_ip):
                        client_spec["remote_addr"] = f"[{iran_node_ip}]:{control_port}"
                    else:
                        client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                    client_spec["token"] = token
                    client_spec["transport"] = transport
                    client_spec["ports"] = ports
                
                elif tunnel.core == "backhaul":
                    transport = server_spec.get("transport") or server_spec.get("transport_type") or "tcp"
                    token = server_spec.get("token")
                    control_port = server_spec.get("control_port") or server_spec.get("public_port")
                    if not control_port:
                        return False
                    
                    ports = server_spec.get("ports") or []
                    if not ports:
                        return False
                    
                    server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
                    server_spec["control_port"] = control_port
                    server_spec["transport"] = transport
                    server_spec["ports"] = ports
                    if token:
                        server_spec["token"] = token
                    
                    iran_node_ip = iran_node.node_metadata.get("ip_address")
                    if not iran_node_ip:
                        return False
                    if is_valid_ipv6_address(iran_node_ip):
                        client_spec["remote_addr"] = f"[{iran_node_ip}]:{control_port}"
                    else:
                        client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                    client_spec["control_port"] = control_port
                    client_spec["transport"] = transport
                    client_spec["ports"] = ports
                    if token:
                        client_spec["token"] = token
                
                elif tunnel.core == "chisel":
                    listen_port = server_spec.get("reverse_port") or server_spec.get("listen_port")
                    if not listen_port:
                        return False
                    
                    port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                    server_control_port = server_spec.get("control_port") or (int(listen_port) + 10000 + (port_hash % 1000))
                    server_spec["mode"] = "server"
                    server_spec["server_port"] = server_control_port
                    server_spec["reverse_port"] = listen_port
                    
                    iran_node_ip = iran_node.node_metadata.get("ip_address")
                    if not iran_node_ip:
                        return False
                    if is_valid_ipv6_address(iran_node_ip):
                        client_spec["server_url"] = f"http://[{iran_node_ip}]:{server_control_port}"
                    else:
                        client_spec["server_url"] = f"http://{iran_node_ip}:{server_control_port}"
                    client_spec["mode"] = "client"
                    client_spec["reverse_port"] = listen_port
                
                server_response = await client.send_to_node(
                    node_id=iran_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": tunnel.id,
                        "core": tunnel.core,
                        "type": tunnel.type,
                        "spec": server_spec
                    }
                )
                
                if server_response.get("status") == "error":
                    logger.error(f"Failed to reapply tunnel {tunnel.id} to iran node: {server_response.get('message')}")
                    return False
                
                client_response = await client.send_to_node(
                    node_id=foreign_node.id,
                    endpoint="/api/agent/tunnels/apply",
                    data={
                        "tunnel_id": tunnel.id,
                        "core": tunnel.core,
                        "type": tunnel.type,
                        "spec": client_spec
                    }
                )
                
                if client_response.get("status") == "error":
                    logger.error(f"Failed to reapply tunnel {tunnel.id} to foreign node: {client_response.get('message')}")
                    return False
                
                return server_response.get("status") == "success" and client_response.get("status") == "success"
        else:
            result = await session.execute(select(Node).where(Node.id == tunnel.node_id))
            node = result.scalar_one_or_none()
            if not node:
                return False
            
            spec = tunnel.spec.copy() if tunnel.spec else {}
            
            if tunnel.core == "gost":
                spec["type"] = tunnel.type
            
            if tunnel.core == "frp":
                spec = prepare_frp_spec_for_node(spec, node, fake_request)
            
            response = await client.send_to_node(
                node_id=node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": tunnel.core,
                    "type": tunnel.type,
                    "spec": spec
                }
            )
            
            return response.get("status") == "success"
    
    def set_request(self, request: Request):
        """Set request object for reapply operations"""
        self.request = request


tunnel_reapply_manager = TunnelReapplyManager()

