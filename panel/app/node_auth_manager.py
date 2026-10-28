"""Automatic per-node agent API token provisioning.

Older installs never configured NODE_API_TOKEN, which left every node agent API
(/api/agent/*, port 8888) open to the internet. This manager gives every node its
own random token without any manual step:

1. A token is generated and stored as ``agent_token_pending`` (persisted *before*
   it is sent, so a crash can never leave a node locked with a token the panel
   does not know).
2. It is pushed with POST /api/agent/auth/bootstrap. On success it becomes
   ``agent_token`` and the node rejects every request without it.
3. An updated agent is then asked to persist an enforcement flag
   (POST /api/agent/auth/enforce) so a later lost token file cannot silently
   reopen the API. Older agents answer 404/405 and stay permissive until they
   are updated; mixed-version fleets keep working.
4. Provisioned nodes are re-checked periodically (idempotent) which also heals a
   node whose data volume was wiped (token and enforcement flag are re-applied).

A node that answers 401 already has a different token; the panel never rotates
it automatically and never resets it on an authentication failure. Local
recovery is ``smite-node reset-token``.
"""
import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone
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
        from app.mutation_guard import mutation_guard

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node.id))
            node_ids = list(result.scalars().all())
        now = time.time()
        for node_id in node_ids:
            if mutation_guard.is_engaged():
                logger.info("Mutation guard engaged: skipping automatic agent token provisioning")
                return
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
        from app.mutation_guard import mutation_guard

        if mutation_guard.is_engaged():
            logger.info("Mutation guard engaged: not scheduling agent token provisioning")
            return
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
        """Prepare, verify and enforce a node token without sending it over HTTP."""
        from app.config import settings
        from app.node_client import NodeClient

        async with AsyncSessionLocal() as session:
            node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if not node:
            return "missing"
        meta = node.node_metadata or {}
        client = NodeClient()

        if meta.get("agent_token_mode") == "env" and meta.get("agent_transport_mode") == "pinned_https":
            return "env"

        token = meta.get("agent_token")
        if token and meta.get("agent_transport_mode") == "pinned_https":
            if await client.verify_agent_token(node, token):
                enforced = await self._enforce_node_auth(node_id, node, token)
                return "ok" if enforced else "verified"
            if not self._pending_proof_ready(meta):
                return "conflict"
            # The node can no longer authenticate with the pinned token but has
            # presented a fresh local proof through a trusted registration (for
            # example a restored data volume). Re-enroll over the new identity
            # instead of leaving it permanently locked out.
            logger.warning(
                f"Node {node.name} ({node_id}) failed pinned token verification; "
                "re-enrolling with its fresh local bootstrap proof"
            )

        proof = str(meta.get("agent_bootstrap_proof") or "")
        pending_address = str(meta.get("agent_pending_api_address") or "")
        pending_certificate = meta.get("agent_pending_tls_certificate")
        if not proof or not pending_address.startswith("https://") or not pending_certificate:
            return "awaiting_secure_transport"
        if int(meta.get("agent_bootstrap_expires_at") or 0) <= int(time.time()):
            return "awaiting_fresh_proof"

        token = token or meta.get("agent_token_pending")
        if not token:
            token = secrets.token_urlsafe(32)
            await self._update_meta(node_id, {"agent_token_pending": token})
            async with AsyncSessionLocal() as session:
                node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
            if not node:
                return "missing"
            meta = node.node_metadata or {}

        status_code, body = await client.bootstrap_agent_token(node, token, proof)

        if status_code == 200:
            mode = (body or {}).get("mode")
            verify_token = settings.node_api_token if mode == "env" else token
            if not verify_token or not await client.verify_agent_token(node, verify_token, pending=True):
                await self._update_meta(node_id, {"agent_token_error": "Token bootstrap completed but verification failed; enforcement was not enabled."})
                return "verification_failed"
            updates = self._promoted_transport(meta)
            if mode == "env":
                updates["agent_token_mode"] = "env"
            else:
                updates["agent_token"] = token
            await self._update_meta(
                node_id,
                updates,
                (
                    "agent_token_pending",
                    "agent_token_error",
                    "agent_pending_api_address",
                    "agent_pending_tls_certificate",
                    "agent_pending_tls_fingerprint",
                    "agent_bootstrap_proof",
                    "agent_bootstrap_expires_at",
                ),
            )
            if mode == "env":
                logger.info(f"Node {node.name} ({node_id}) uses the configured static NODE_API_TOKEN over pinned HTTPS")
                return "env"
            first_time = meta.get("agent_token") != token
            enforced = await self._enforce_node_auth(node_id, node, token)
            if first_time or (body or {}).get("changed"):
                logger.info(
                    f"Agent API token provisioned for node {node.name} ({node_id}); "
                    f"its pinned HTTPS API is now authenticated{' and locked' if enforced else ''}"
                )
            if enforced:
                self._warned.pop(node_id, None)
            return "ok"
        if status_code == 409:
            verify_token = settings.node_api_token or ""
            if verify_token and await client.verify_agent_token(node, verify_token, pending=True):
                updates = self._promoted_transport(meta)
                updates["agent_token_mode"] = "env"
                await self._update_meta(
                    node_id,
                    updates,
                    (
                        "agent_token_pending",
                        "agent_token_error",
                        "agent_pending_api_address",
                        "agent_pending_tls_certificate",
                        "agent_pending_tls_fingerprint",
                        "agent_bootstrap_proof",
                        "agent_bootstrap_expires_at",
                    ),
                )
                return "env"
            await self._update_meta(node_id, {"agent_token_error": "Node uses a static token that the panel could not verify."})
            return "conflict"
        if status_code in (404, 405, 428):
            return "unsupported"
        if status_code in (401, 403):
            msg = "Node rejected its one-time bootstrap proof or already has a different token; no automatic reset was attempted."
            if self._warned.get(node_id) != msg:
                logger.warning(f"Node {node.name} ({node_id}): {msg}")
                self._warned[node_id] = msg
                await self._update_meta(node_id, {"agent_token_error": msg})
            return "conflict"
        return "unreachable"

    @staticmethod
    def _pending_proof_ready(meta: Dict[str, Any]) -> bool:
        return bool(
            str(meta.get("agent_bootstrap_proof") or "")
            and str(meta.get("agent_pending_api_address") or "").startswith("https://")
            and meta.get("agent_pending_tls_certificate")
            and int(meta.get("agent_bootstrap_expires_at") or 0) > int(time.time())
        )

    @staticmethod
    def _promoted_transport(meta: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "api_address": meta["agent_pending_api_address"],
            "agent_tls_certificate": meta["agent_pending_tls_certificate"],
            "agent_tls_fingerprint": meta["agent_pending_tls_fingerprint"],
            "agent_transport_mode": "pinned_https",
        }

    async def _enforce_node_auth(self, node_id: str, node: Node, token: str) -> bool:
        """Persist the node-side enforcement flag only after HTTPS token verification."""
        from app.node_client import NodeClient

        async with AsyncSessionLocal() as session:
            refreshed = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if refreshed is not None:
            node = refreshed
        status_code, body = await NodeClient().enforce_agent_auth(node, token)
        if status_code == 200:
            await self._update_meta(
                node_id,
                {
                    "agent_auth_enforced": True,
                    "agent_auth_enforced_at": datetime.now(timezone.utc).isoformat(),
                },
                ("agent_auth_error",),
            )
            return True
        if status_code in (404, 405):
            logger.info(
                f"Node {node.name} ({node_id}) agent does not support durable auth enforcement yet; "
                "it stays permissive until the agent is updated"
            )
            return False
        if status_code == 401:
            msg = "Node rejected the provisioned token while enabling enforcement; no automatic reset was attempted."
        elif status_code == 0:
            detail = (body or {}).get("message") if isinstance(body, dict) else None
            msg = detail or "Node was unreachable while enabling auth enforcement; it will be retried."
        else:
            msg = f"Node agent enforcement returned HTTP {status_code}; it will be retried."
        if self._warned.get(node_id) != msg:
            logger.warning(f"Node {node.name} ({node_id}): {msg}")
            self._warned[node_id] = msg
            await self._update_meta(node_id, {"agent_auth_error": msg})
        return False


node_auth_manager = NodeAuthManager()


async def bootstrap_request(url: str, token: str, proof: str, verify: Any, timeout: float = 8.0) -> Tuple[int, Optional[dict]]:
    headers = {"X-Node-Token": token, "X-Bootstrap-Proof": proof}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=4.0), verify=verify) as client:
        resp = await client.post(url, json={"token": token}, headers=headers)
        body = None
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body
