"""Tunnel auto reapply manager"""
import copy
import asyncio
import logging
import random
import time
from datetime import datetime, timezone
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
        # consecutive 'process dead' observations per tunnel (debounce for active tunnels)
        self._dead_strikes: dict = {}
        # Observability + restart budget (memory only; never contains secrets)
        self._retry_at: dict = {}
        self._failures: dict = {}
        self._alerted_at: dict = {}
        self.last_success_at: Optional[str] = None
        self.last_failure_at: Optional[str] = None
        self.last_error: Optional[str] = None
        self.restarts_total = 0

    def runtime_status(self) -> dict:
        now = time.time()
        return {
            "healer_running": bool(self.heal_task and not self.heal_task.done()),
            "periodic_running": bool(self.task and not self.task.done()),
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "restarts_total": self.restarts_total,
            "pending_retries": sum(1 for t in self._retry_at.values() if t > now),
            "last_error": self.last_error,
        }

    def _record_success(self, tunnel_id: str) -> None:
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        self.restarts_total += 1
        self._failures.pop(tunnel_id, None)
        self._retry_at.pop(tunnel_id, None)
        self._alerted_at.pop(tunnel_id, None)

    def _record_failure(self, tunnel_id: str, tunnel_name: str, error: str) -> None:
        now = time.time()
        failures = self._failures.get(tunnel_id, 0) + 1
        self._failures[tunnel_id] = failures
        self.last_failure_at = datetime.now(timezone.utc).isoformat()
        self.last_error = str(error)[:200]
        # Exponential backoff (30s..15min) so a failing node is not hammered.
        delay = min(30 * (2 ** min(failures - 1, 5)), 900)
        self._retry_at[tunnel_id] = now + delay * random.uniform(0.9, 1.1)
        if failures >= 3 and now - self._alerted_at.get(tunnel_id, 0.0) > 1800:
            self._alerted_at[tunnel_id] = now
            try:
                from app.telegram_bot import telegram_bot

                asyncio.create_task(
                    telegram_bot.send_admin_alert(
                        f"⚠️ Smite: reapply for tunnel '{tunnel_name}' failed "
                        f"{failures} times in a row. Check panel/node logs."
                    )
                )
            except Exception:
                pass
    
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
        """Start the self-healing watchdog and sync the optional periodic sweep"""
        await self._ensure_healer()
        await self.load_settings()
        await self._sync_periodic_task(restart=False)

    async def apply_settings(self):
        """Apply automation settings without touching the always-on watchdog"""
        await self._ensure_healer()
        await self.load_settings()
        await self._sync_periodic_task(restart=True)

    async def _ensure_healer(self):
        """Start the watchdog if it is not already running"""
        if self.heal_task is None or self.heal_task.done():
            self.heal_task = asyncio.create_task(self._auto_heal_loop())
            logger.info("Tunnel self-healing watchdog task started (45s interval)")

    async def _cancel_periodic_task(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _sync_periodic_task(self, restart: bool):
        """Start/stop the periodic sweep; the watchdog is intentionally unaffected"""
        if restart:
            await self._cancel_periodic_task()
        if not self.enabled:
            await self._cancel_periodic_task()
            return
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._reapply_loop())
            logger.info(f"Tunnel periodic health sweep started: interval={self.interval} {self.interval_unit}")

    async def stop(self):
        """Stop both background tasks (panel shutdown only)"""
        await self._cancel_periodic_task()

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
                await asyncio.sleep(45 + random.uniform(-5.0, 5.0))
                from app.mutation_guard import mutation_guard
                if mutation_guard.is_engaged():
                    logger.debug("Mutation guard engaged: skipping self-healing pass")
                    continue
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
                        if self._retry_at.get(tunnel.id, 0.0) > time.time():
                            continue
                        try:
                            from app.mutation_guard import mutation_guard
                            if mutation_guard.is_engaged():
                                logger.info("Mutation guard engaged: aborting self-healing pass")
                                break
                            # If tunnel is in error status, check if its nodes are reachable
                            if tunnel.status == "error":
                                iran_id = tunnel.iran_node_id or tunnel.node_id
                                foreign_id = tunnel.foreign_node_id
                                
                                ir_ok = False
                                fn_ok = True
                                
                                if iran_id:
                                    try:
                                        resp = await asyncio.wait_for(client.get_tunnel_status(iran_id, ""), timeout=3.5)
                                        ir_ok = bool(resp and resp.get("status") in ("ok", "success"))
                                    except Exception:
                                        ir_ok = False
                                        
                                if foreign_id:
                                    try:
                                        resp = await asyncio.wait_for(client.get_tunnel_status(foreign_id, ""), timeout=3.5)
                                        fn_ok = bool(resp and resp.get("status") in ("ok", "success"))
                                    except Exception:
                                        fn_ok = False
                                        
                                if ir_ok and fn_ok:
                                    logger.info(f"Self-Healing: Nodes for tunnel '{tunnel.name}' ({tunnel.core}) are back online. Auto-reapplying...")
                                    applied_ok = await self._reapply_tunnel_safe(tunnel, session, client)
                                    if applied_ok:
                                        self._record_success(tunnel.id)
                                        tunnel.status = "active"
                                        tunnel.error_message = None
                                        from sqlalchemy.orm.attributes import flag_modified
                                        flag_modified(tunnel, "spec")
                                        await session.commit()
                                        logger.info(f"Self-Healing: Successfully revived and restored tunnel '{tunnel.name}'")
                                    else:
                                        self._record_failure(tunnel.id, tunnel.name, "node did not confirm reapply")
                            elif tunnel.status == "active":
                                # Crash recovery: a node is up but the core process died
                                process_dead = await self._tunnel_process_dead(client, tunnel)
                                if not process_dead:
                                    self._dead_strikes.pop(tunnel.id, None)
                                else:
                                    strikes = self._dead_strikes.get(tunnel.id, 0) + 1
                                    self._dead_strikes[tunnel.id] = strikes
                                    if strikes >= 2:
                                        self._dead_strikes.pop(tunnel.id, None)
                                        logger.warning(f"Self-Healing: {tunnel.core.capitalize()} process for tunnel '{tunnel.name}' is not running; re-applying")
                                        if await self._reapply_tunnel_safe(tunnel, session, client):
                                            self._record_success(tunnel.id)
                                            logger.info(f"Self-Healing: {tunnel.core.capitalize()} tunnel '{tunnel.name}' restarted")
                                        else:
                                            self._record_failure(tunnel.id, tunnel.name, "node did not confirm restart")
                        except Exception as t_err:
                            logger.debug(f"Self-healing check error for tunnel {tunnel.id}: {t_err}")
            except asyncio.CancelledError:
                logger.info("Self-healing watchdog loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in self-healing watchdog loop: {e}", exc_info=True)

    def _tunnel_node_ids(self, tunnel: Tunnel) -> list:
        node_ids = []
        for nid in (tunnel.iran_node_id or tunnel.node_id, tunnel.foreign_node_id):
            if nid and nid not in node_ids:
                node_ids.append(nid)
        return node_ids

    async def _tunnel_process_dead(self, client: NodeClient, tunnel: Tunnel) -> bool:
        """True only when a reachable node positively reports the tunnel process is dead"""
        for nid in self._tunnel_node_ids(tunnel):
            try:
                resp = await asyncio.wait_for(client.get_tunnel_status(nid, tunnel.id), timeout=3.5)
            except Exception:
                continue  # unreachable node: node health owns that failure
            data = resp.get("data") if isinstance(resp, dict) else None
            if (
                isinstance(resp, dict)
                and resp.get("status") == "success"
                and isinstance(data, dict)
                and data.get("process_running") is False
            ):
                return True
        return False

    async def _reapply_loop(self):
        """Background task for the optional periodic health sweep"""
        try:
            while True:
                try:
                    await self.load_settings()

                    from app.mutation_guard import mutation_guard
                    if mutation_guard.is_engaged():
                        logger.debug("Mutation guard engaged: skipping scheduled tunnel sweep")
                        await asyncio.sleep(60)
                        continue

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

                    await self._reapply_all_tunnels()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error in scheduled tunnel health sweep: {e}", exc_info=True)
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Tunnel periodic health sweep cancelled")
            raise
    
    async def _reapply_all_tunnels(self):
        """Periodic safety sweep: reapply only tunnels with positive fault evidence"""
        from app.mutation_guard import mutation_guard

        if mutation_guard.is_engaged():
            logger.info("Mutation guard engaged: skipping scheduled tunnel health sweep")
            return
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Tunnel).where(Tunnel.status == "active"))
            tunnels = result.scalars().all()
            
            if not tunnels:
                logger.debug("No active tunnels to check")
                return
            
            client = NodeClient()
            healthy = 0
            reapplied = 0
            failed = 0
            
            for tunnel in tunnels:
                try:
                    if mutation_guard.is_engaged():
                        logger.info("Mutation guard engaged: aborting scheduled health sweep")
                        break
                    needs_reapply = bool(tunnel.spec and tunnel.spec.get("_pending_reapply"))
                    if not needs_reapply:
                        if not await self._tunnel_process_dead(client, tunnel):
                            healthy += 1
                            continue
                        # Confirm before restarting: a single probe can race a core restart
                        await asyncio.sleep(2.0)
                        if not await self._tunnel_process_dead(client, tunnel):
                            healthy += 1
                            continue
                        logger.warning(
                            f"Scheduled health sweep: process for tunnel '{tunnel.name}' ({tunnel.core}) "
                            "is not running; re-applying"
                        )
                    success = await self._reapply_tunnel_safe(tunnel, session, client)
                    if success:
                        self._record_success(tunnel.id)
                        reapplied += 1
                    else:
                        self._record_failure(tunnel.id, tunnel.name, "node did not confirm reapply")
                        failed += 1
                except Exception as e:
                    logger.error(f"Error checking tunnel {tunnel.id} in scheduled sweep: {e}", exc_info=True)
                    self._record_failure(tunnel.id, tunnel.name, str(e))
                    failed += 1
                await asyncio.sleep(0.15)
            
            logger.info(
                f"Scheduled tunnel health sweep completed: {reapplied} re-applied, "
                f"{failed} failed, {healthy} healthy"
            )

    async def _reapply_tunnel_safe(self, tunnel: Tunnel, session: AsyncSession, client: NodeClient) -> bool:
        """Safely re-apply a single tunnel to its node(s)"""
        from app.mutation_guard import mutation_guard

        if mutation_guard.is_engaged():
            logger.info(f"Mutation guard engaged: not reapplying tunnel {tunnel.id}")
            return False
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

        is_multi_node_tunnel = tunnel.core in {"rathole", "backhaul", "chisel", "frp"} or (
            tunnel.core == "gost" and (
                bool(tunnel.foreign_node_id)
                or bool(tunnel.iran_node_id)
                or tunnel.is_reverse is not None
            )
        )
        if tunnel.core in {"rathole", "backhaul"}:
            is_reverse = True
        elif tunnel.core in {"gost", "chisel", "frp"}:
            if tunnel.is_reverse is not None:
                is_reverse = bool(tunnel.is_reverse)
            elif not (tunnel.spec or {}).get("force_direct") and (tunnel.foreign_node_id or tunnel.iran_node_id):
                is_reverse = True
            else:
                is_reverse = False
        else:
            is_reverse = bool(tunnel.is_reverse) if tunnel.is_reverse is not None else False
        
        if is_multi_node_tunnel:
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
                else:
                    logger.warning(f"Tunnel {tunnel.id}: Assigned foreign node {tunnel.foreign_node_id} not found in database, skipping auto-reapply to prevent node drift")
                    return False
            elif len(foreign_nodes) == 1:
                foreign_node = foreign_nodes[0]
            else:
                logger.warning(f"Tunnel {tunnel.id}: Multiple foreign nodes available and no foreign_node_id specified, skipping auto-reapply")
                return False
            
            iran_node_ip = iran_node.node_metadata.get("ip_address")
            if not iran_node_ip:
                logger.warning(f"Tunnel {tunnel.id}: Iran node has no IP address, skipping")
                return False
            
            foreign_node_ip = foreign_node.node_metadata.get("ip_address") if foreign_node.node_metadata else None
            
            from app.spec_builder import build_tunnel_node_specs, persist_generated_spec
            spec_snapshot = copy.deepcopy(tunnel.spec)
            try:
                iran_spec, foreign_spec = build_tunnel_node_specs(tunnel, iran_node_ip, foreign_node_ip or iran_node_ip)
            except Exception as e:
                logger.error(f"Spec builder failed for tunnel {tunnel.id}: {e}")
                return False
            await persist_generated_spec(session, tunnel, spec_snapshot)
            
            if iran_spec.get("mode") == "server":
                first_node, first_spec, first_role = iran_node, iran_spec, "iran node"
                second_node, second_spec, second_role = foreign_node, foreign_spec, "foreign node"
            else:
                first_node, first_spec, first_role = foreign_node, foreign_spec, "foreign node"
                second_node, second_spec, second_role = iran_node, iran_spec, "iran node"

            first_response = await client.send_to_node(
                node_id=first_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": tunnel.core,
                    "type": tunnel.type,
                    "spec": first_spec
                }
            )
            
            if first_response.get("status") == "error":
                logger.error(f"Failed to reapply tunnel {tunnel.id} to {first_role}: {first_response.get('message')}")
                return False
            
            await asyncio.sleep(1.0)

            second_response = await client.send_to_node(
                node_id=second_node.id,
                endpoint="/api/agent/tunnels/apply",
                data={
                    "tunnel_id": tunnel.id,
                    "core": tunnel.core,
                    "type": tunnel.type,
                    "spec": second_spec
                }
            )
            
            if second_response.get("status") == "error":
                logger.error(f"Failed to reapply tunnel {tunnel.id} to {second_role}: {second_response.get('message')}")
                return False
            
            ok = first_response.get("status") == "success" and second_response.get("status") == "success"
            if ok and tunnel.spec and "_pending_reapply" in tunnel.spec:
                tunnel.spec.pop("_pending_reapply", None)
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

