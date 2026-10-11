"""Automatic per-node agent API token provisioning.

Older installs never configured NODE_API_TOKEN, which left every node agent API
(/api/agent/*, port 8888) open to the internet. This manager gives every node its
own random token without any manual step:

1. A token is generated and stored as ``agent_token_pending`` (persisted *before*
   it is sent, so a crash can never leave a node locked with a token the panel
   does not know).
2. It is pushed with POST /api/agent/auth/bootstrap. On success it becomes
   ``agent_token`` and the node rejects every request without it.
3. Provisioned nodes are re-checked periodically (idempotent) which also heals a
   node whose data volume was wiped.

Nodes running older agent versions answer 404 and are simply retried later, so
mixed-version fleets keep working during a rolling update.
"""
import asyncio
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.database import AsyncSessionLocal
from app.models import Node

logger = logging.getLogger(__name__)

PENDING_RETRY_SECONDS = 120
PROVISIONED_RECHECK_SECONDS = 900


class NodeAuthManager:
    def __init__(self) -> None:
        self.task: Optional[asyncio.Task] = None
        self._last_check: Dict[str, float] = {}
        self._warned: Dict[str, str] = {}
        self._pending: set = set()

    async def start(self) -> None:
        await self.stop()
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.ensure_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Node token provisioning loop error: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def ensure_all(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node.id))
            node_ids = list(result.scalars().all())
        now = time.time()
        for node_id in node_ids:
            last = self._last_check.get(node_id, 0.0)
            async with AsyncSessionLocal() as session:
                node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
            if not node:
                continue
            meta = node.node_metadata or {}
            interval = PROVISIONED_RECHECK_SECONDS if meta.get("agent_token") or meta.get("agent_token_mode") == "env" else PENDING_RETRY_SECONDS
            if now - last < interval:
                continue
            self._last_check[node_id] = now
            try:
                await self.ensure_node(node_id)
            except Exception as e:
                logger.debug(f"Token provisioning for node {node_id} failed: {e}")

    def schedule_soon(self, node_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Provision right after a node (re)registers instead of waiting for the loop.

        Keeps the window in which an updated node accepts a first token from someone
        else as short as possible.
        """
        meta = metadata or {}
        if meta.get("agent_token") or meta.get("agent_token_mode") == "env":
            return
        now = time.time()
        if now - self._last_check.get(node_id, 0.0) < 30:
            return
        self._last_check[node_id] = now

        async def _run() -> None:
            try:
                await self.ensure_node(node_id)
            except Exception as e:
                logger.debug(f"Immediate token provisioning for node {node_id} failed: {e}")

        try:
            task = asyncio.get_running_loop().create_task(_run())
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        except RuntimeError:
            pass

    async def _update_meta(self, node_id: str, updates: Dict[str, Any], removals: Tuple[str, ...] = ()) -> None:
        """Short read-modify-write so concurrent registrations are not overwritten."""
        async with AsyncSessionLocal() as session:
            node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
            if not node:
                return
            meta = dict(node.node_metadata or {})
            meta.update(updates)
            for key in removals:
                meta.pop(key, None)
            node.node_metadata = meta
            flag_modified(node, "node_metadata")
            await session.commit()

    async def ensure_node(self, node_id: str) -> str:
        """Provision (or re-confirm) the node's agent token. Returns a short status string."""
        from app.node_client import NodeClient

        async with AsyncSessionLocal() as session:
            node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if not node:
            return "missing"
        meta = node.node_metadata or {}
        if meta.get("agent_token_mode") == "env":
            return "env"

        token = meta.get("agent_token") or meta.get("agent_token_pending")
        if not token:
            token = secrets.token_urlsafe(32)
            await self._update_meta(node_id, {"agent_token_pending": token})
            async with AsyncSessionLocal() as session:
                node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
            if not node:
                return "missing"

        client = NodeClient()
        status_code, body = await client.bootstrap_agent_token(node, token)

        if status_code == 200:
            mode = (body or {}).get("mode")
            if mode == "env":
                await self._update_meta(node_id, {"agent_token_mode": "env"}, ("agent_token_pending", "agent_token_error"))
                return "env"
            first_time = meta.get("agent_token") != token
            await self._update_meta(node_id, {"agent_token": token}, ("agent_token_pending", "agent_token_error"))
            if first_time or (body or {}).get("changed"):
                logger.info(f"Agent API token provisioned for node {node.name} ({node_id}); its agent API is now authenticated")
            self._warned.pop(node_id, None)
            return "ok"
        if status_code == 409:
            await self._update_meta(node_id, {"agent_token_mode": "env"}, ("agent_token_pending", "agent_token_error"))
            logger.info(f"Node {node.name} ({node_id}) uses a static NODE_API_TOKEN; the panel's global NODE_API_TOKEN will be used for it")
            return "env"
        if status_code in (404, 405):
            return "unsupported"  # node agent not updated yet
        if status_code == 401:
            msg = "Node already has a different agent token. Run `smite-node reset-token` on the node to re-pair it."
            if self._warned.get(node_id) != msg:
                logger.warning(f"Node {node.name} ({node_id}): {msg}")
                self._warned[node_id] = msg
                await self._update_meta(node_id, {"agent_token_error": msg})
            return "conflict"
        return "unreachable"


node_auth_manager = NodeAuthManager()


async def bootstrap_request(url: str, token: str, verify: Any, timeout: float = 8.0) -> Tuple[int, Optional[dict]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=4.0), verify=verify) as client:
        resp = await client.post(url, json={"token": token}, headers={"X-Node-Token": token})
        body = None
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body
