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
    """Manages automatic tunnel reapplication"""
    
    def __init__(self):
        self.task: Optional[asyncio.Task] = None
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
        """Start auto reapply task"""
        await self.stop()
        await self.load_settings()
        
        if self.enabled:
            self.task = asyncio.create_task(self._reapply_loop())
            logger.info(f"Tunnel auto reapply task started: interval={self.interval} {self.interval_unit}")
    
    async def stop(self):
        """Stop auto reapply task"""
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            logger.info("Tunnel auto reapply task stopped")
    
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
        """Reapply all tunnels"""
        from app.routers.tunnels import prepare_frp_spec_for_node
        from app.models import Node
        from fastapi import Request
        from starlette.requests import Request as StarletteRequest
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            if not tunnels:
                logger.debug("No active tunnels to reapply")
                return
            
            client = NodeClient()
            applied = 0
            failed = 0
            
            from starlette.requests import Request as StarletteRequest
            from starlette.datastructures import Headers
            
            fake_request = StarletteRequest(
                scope={
                    "type": "http",
                    "method": "POST",
                    "path": "/api/tunnels/reapply",
                    "headers": Headers({}).raw,
                    "query_string": b"",
                }
            )
            
            for tunnel in tunnels:
                try:
                    is_reverse_tunnel = tunnel.core in {"rathole", "backhaul", "chisel", "frp"} or (tunnel.core == "gost" and (tunnel.foreign_node_id or tunnel.iran_node_id))
                    
                    if is_reverse_tunnel:
                        iran_node_id = tunnel.iran_node_id or tunnel.node_id
                        if not iran_node_id:
                            continue
                            
                        result = await session.execute(select(Node).where(Node.id == iran_node_id))
                        iran_node = result.scalar_one_or_none()
                        if not iran_node:
                            continue
                        
                        result = await session.execute(select(Node))
                        all_nodes = result.scalars().all()
                        foreign_nodes = [n for n in all_nodes if n.node_metadata and n.node_metadata.get("role") == "foreign"]
                        if not foreign_nodes:
                            continue
                        
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
                                failed += 1
                                continue
                            
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
                                failed += 1
                                continue
                            
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
                                failed += 1
                                continue
                            
                            if server_response.get("status") == "success" and client_response.get("status") == "success":
                                applied += 1
                                logger.info(f"Successfully reapplied tunnel {tunnel.id} ({tunnel.core})")
                            else:
                                failed += 1
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
                                    continue
                                
                                control_port = server_spec.get("control_port")
                                if not control_port:
                                    remote_addr = server_spec.get("remote_addr", "")
                                    from app.utils import parse_address_port
                                    _, control_port, _ = parse_address_port(remote_addr) if remote_addr else (None, None, None)
                                import hashlib
                                port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                                assigned_control_port = 25000 + (port_hash % 25000)

                                if not control_port or int(control_port) < 24000:
                                    control_port = assigned_control_port
                                    tunnel.spec["control_port"] = control_port
                                    from sqlalchemy.orm.attributes import flag_modified
                                    flag_modified(tunnel, "spec")
                                
                                server_spec["mode"] = "server"
                                server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
                                server_spec["ports"] = ports
                                server_spec["transport"] = transport
                                server_spec["transport_type"] = transport
                                server_spec["token"] = token
                                
                                if transport.lower() == "noise":
                                    server_priv = tunnel.spec.get("server_private_key") or tunnel.spec.get("local_private_key")
                                    client_pub = tunnel.spec.get("client_public_key") or tunnel.spec.get("remote_public_key")
                                    client_priv = tunnel.spec.get("client_private_key") or tunnel.spec.get("local_private_key")
                                    server_pub = tunnel.spec.get("server_public_key") or tunnel.spec.get("remote_public_key")
                                    if server_priv and client_pub:
                                        server_spec["local_private_key"] = server_priv
                                        server_spec["remote_public_key"] = client_pub
                                    if client_priv and server_pub:
                                        client_spec["local_private_key"] = client_priv
                                        client_spec["remote_public_key"] = server_pub
                                
                                iran_node_ip = iran_node.node_metadata.get("ip_address")
                                if not iran_node_ip:
                                    continue
                                transport_lower = transport.lower()
                                if transport_lower in ("websocket", "ws"):
                                    use_tls = bool(server_spec.get("websocket_tls") or server_spec.get("tls"))
                                    protocol = "wss://" if use_tls else "ws://"
                                    client_spec["remote_addr"] = f"{protocol}{iran_node_ip}:{control_port}"
                                else:
                                    client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                                client_spec["mode"] = "client"
                                client_spec["ports"] = ports
                                client_spec["transport"] = transport
                                client_spec["transport_type"] = transport
                                client_spec["token"] = token
                            
                            elif tunnel.core == "gost":
                                from app.routers.tunnels import build_gost_node_specs
                                control_port = server_spec.get("control_port")
                                auth_token = server_spec.get("auth_token") or server_spec.get("token")
                                ports = server_spec.get("ports", [])
                                iran_node_ip = iran_node.node_metadata.get("ip_address")
                                foreign_node_ip = foreign_node.node_metadata.get("ip_address")
                                if not iran_node_ip or not foreign_node_ip:
                                    continue
                                server_spec, client_spec = build_gost_node_specs(
                                    tunnel, iran_node_ip, foreign_node_ip, control_port, auth_token, ports
                                )

                            elif tunnel.core == "backhaul":
                                transport = server_spec.get("transport") or server_spec.get("type") or "tcp"
                                control_port = server_spec.get("control_port") or server_spec.get("public_port") or server_spec.get("listen_port") or 3080
                                public_port = server_spec.get("public_port") or server_spec.get("listen_port") or control_port
                                target_host = server_spec.get("target_host", "127.0.0.1")
                                token = server_spec.get("token")
                                
                                server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
                                server_spec["control_port"] = control_port
                                server_spec["public_port"] = public_port
                                server_spec["listen_port"] = public_port
                                ports = server_spec.get("ports", [])
                                if ports:
                                    server_spec["ports"] = ports
                                if token:
                                    server_spec["token"] = token
                                
                                iran_node_ip = iran_node.node_metadata.get("ip_address")
                                if not iran_node_ip:
                                    continue
                                transport_lower = transport.lower()
                                if transport_lower in ("ws", "wsmux"):
                                    use_tls = bool(server_spec.get("tls_cert") or server_spec.get("server_options", {}).get("tls_cert"))
                                    protocol = "wss://" if use_tls else "ws://"
                                    client_spec["remote_addr"] = f"{protocol}{iran_node_ip}:{control_port}"
                                else:
                                    client_spec["remote_addr"] = f"{iran_node_ip}:{control_port}"
                                client_spec["transport"] = transport
                                if token:
                                    client_spec["token"] = token
                            
                            elif tunnel.core == "chisel":
                                listen_port = server_spec.get("listen_port") or server_spec.get("remote_port")
                                if not listen_port:
                                    continue
                                
                                import hashlib
                                port_hash = int(hashlib.md5(tunnel.id.encode()).hexdigest()[:8], 16)
                                server_control_port = server_spec.get("control_port") or (int(listen_port) + 10000 + (port_hash % 1000))
                                server_spec["mode"] = "server"
                                server_spec["server_port"] = server_control_port
                                server_spec["reverse_port"] = listen_port
                                
                                iran_node_ip = iran_node.node_metadata.get("ip_address")
                                if not iran_node_ip:
                                    continue
                                from app.utils import is_valid_ipv6_address
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
                                failed += 1
                                continue
                            
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
                                failed += 1
                                continue
                            
                            if server_response.get("status") == "success" and client_response.get("status") == "success":
                                applied += 1
                                logger.info(f"Successfully reapplied tunnel {tunnel.id} ({tunnel.core})")
                            else:
                                failed += 1
                    else:
                        result = await session.execute(select(Node).where(Node.id == tunnel.node_id))
                        node = result.scalar_one_or_none()
                        if not node:
                            continue
                        
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
                        
                        if response.get("status") == "success":
                            applied += 1
                            logger.info(f"Successfully reapplied tunnel {tunnel.id} ({tunnel.core})")
                        else:
                            failed += 1
                            logger.error(f"Failed to reapply tunnel {tunnel.id}: {response.get('message')}")
                except Exception as e:
                    logger.error(f"Error reapplying tunnel {tunnel.id}: {e}", exc_info=True)
                    failed += 1
            
            logger.info(f"Auto reapply completed: {applied} applied, {failed} failed")
    
    def set_request(self, request: Request):
        """Set request object for reapply operations"""
        self.request = request


tunnel_reapply_manager = TunnelReapplyManager()

