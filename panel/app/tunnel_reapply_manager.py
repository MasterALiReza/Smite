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
                await asyncio.sleep(0.15)
            
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
            
            iran_node_ip = iran_node.node_metadata.get("ip_address")
            if not iran_node_ip:
                logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                return False
            
            foreign_node_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
            
            from app.spec_builder import build_tunnel_node_specs
            try:
                server_spec, client_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip or iran_node_ip)
            except Exception as e:
                logger.error(f"Spec builder failed for tunnel {tunnel.id}: {e}")
                return False
            
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
            
            ok = server_response.get("status") == "success" and client_response.get("status") == "success"
            if ok and tunnel.spec and "_pending_reapply" in tunnel.spec:
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tunnel, "spec")
                await session.commit()
            return ok
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
            
            ok = response.get("status") == "success"
            if ok and tunnel.spec and "_pending_reapply" in tunnel.spec:
                tunnel.spec.pop("_pending_reapply", None)
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tunnel, "spec")
                await session.commit()
            return ok
    
    def set_request(self, request: Request):
        """Set request object for reapply operations"""
        self.request = request


tunnel_reapply_manager = TunnelReapplyManager()

