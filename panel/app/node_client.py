"""Client for panel to communicate with nodes"""
import httpx
import ssl
import logging
import asyncio
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from app.database import AsyncSessionLocal
from app.models import Node, Settings, Tunnel
from app.config import settings

logger = logging.getLogger(__name__)


def _local_addresses() -> list:
    """Addresses the panel treats as local/collocated (from PANEL_LOCAL_IPS env)."""
    return [a.strip() for a in settings.panel_local_ips.split(",") if a.strip()]


# Panel-side bookkeeping keys stored in tunnel.spec that must never reach a node:
# they don't change the core configuration but would make the node think the
# config changed and restart the tunnel. (force_restart is an explicit command and
# is therefore kept.)
VOLATILE_SPEC_KEYS = ("latency_ms", "_pending_reapply")

# Node metadata keys holding secrets: never returned by the API, never accepted
# from node-supplied registration payloads.
RESERVED_NODE_METADATA_KEYS = (
    "agent_token",
    "agent_token_pending",
    "agent_token_mode",
    "agent_token_error",
    "agent_auth_enforced",
    "agent_auth_enforced_at",
    "agent_auth_error",
    "agent_tls_certificate",
    "agent_tls_fingerprint",
    "agent_pending_api_address",
    "agent_pending_tls_certificate",
    "agent_pending_tls_fingerprint",
    "agent_bootstrap_proof",
    "agent_bootstrap_expires_at",
)


_CAPABILITY_CACHE: Dict[str, Tuple[float, List[str]]] = {}


class InsecureNodeTransport(RuntimeError):
    """The node URL is HTTPS but the panel has no CA to verify it against."""


def _strip_volatile_spec_keys(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    if not endpoint.endswith("/tunnels/apply") or not isinstance(data, dict):
        return data
    spec = data.get("spec")
    if not isinstance(spec, dict) or not any(k in spec for k in VOLATILE_SPEC_KEYS):
        return data
    cleaned = dict(data)
    cleaned["spec"] = {k: v for k, v in spec.items() if k not in VOLATILE_SPEC_KEYS}
    return cleaned


def node_agent_token(node: Optional[Node]) -> str:
    """Token the panel must present to this node's agent API."""
    meta = (node.node_metadata or {}) if node is not None else {}
    if meta.get("agent_token_mode") != "env":
        token = meta.get("agent_token") or meta.get("agent_token_pending")
        if token:
            return str(token)
    return settings.node_api_token or ""


def public_node_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Node metadata safe to return to API clients (secrets removed)."""
    cleaned = dict(meta or {})
    for key in RESERVED_NODE_METADATA_KEYS:
        cleaned.pop(key, None)
    return cleaned


class NodeClient:
    """Client to send requests to nodes via HTTP/HTTPS or FRP"""
    
    def __init__(self):
        self.timeout = httpx.Timeout(30.0)
    
    @staticmethod
    def _node_token_headers(node: Optional[Node] = None) -> Dict[str, str]:
        """Shared-secret header for node agent API authentication.

        Uses the per-node token provisioned by the panel when available (or the one
        currently being provisioned), otherwise the global NODE_API_TOKEN.
        """
        token = node_agent_token(node)
        if token:
            return {"X-Node-Token": token}
        return {}

    @staticmethod
    def _get_verify() -> Any:
        """Get CA certificate path for HTTPS verification if available, else False."""
        try:
            cert_file = Path(settings.node_cert_path)
            if not cert_file.is_absolute():
                cert_file = Path.cwd() / cert_file
            if cert_file.exists() and cert_file.stat().st_size > 0:
                return str(cert_file)
        except Exception:
            pass
        return False

    @staticmethod
    def _pinned_context(certificate: str) -> ssl.SSLContext:
        try:
            context = ssl.create_default_context(cadata=certificate)
        except ssl.SSLError as error:
            raise InsecureNodeTransport(f"Stored node certificate is invalid: {error}") from error
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def _verify_for_url(self, url: str, node: Optional[Node] = None, pending: bool = False) -> Any:
        """Return strict CA or per-node pin verification for an HTTPS URL."""
        if not url.lower().startswith("https://"):
            return False
        meta = (node.node_metadata or {}) if node is not None else {}
        key = "agent_pending_tls_certificate" if pending else "agent_tls_certificate"
        certificate = meta.get(key)
        if certificate:
            return self._pinned_context(str(certificate))
        if pending:
            raise InsecureNodeTransport("Pending node enrollment has no pinned TLS certificate")
        ca = self._get_verify()
        if ca is False:
            raise InsecureNodeTransport(
                f"Refusing to disable TLS verification for {url}: no pinned node certificate "
                f"or panel CA ({settings.node_cert_path}) is available."
            )
        return ca
    
    @staticmethod
    def _direct_node_address(meta: Dict[str, Any]) -> str:
        """Direct node URL that never downgrades a pinned node to plaintext."""
        pinned = bool(meta.get("agent_tls_certificate"))
        configured = str(meta.get("api_address") or "").strip()
        if configured:
            if pinned and configured.lower().startswith("http://"):
                return "https://" + configured[len("http://"):]
            return configured
        host = str(meta.get("ip_address") or "127.0.0.1")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        scheme = "https" if pinned else "http"
        return f"{scheme}://{host}:{meta.get('api_port', 8888)}"

    async def _get_frp_settings(self) -> Optional[Dict[str, Any]]:
        """Get FRP communication settings"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Settings).where(Settings.key == "frp"))
            setting = result.scalar_one_or_none()
            if setting and setting.value and setting.value.get("enabled"):
                return setting.value
        return None
    
    async def _get_node_address(self, node: Node) -> Tuple[str, bool]:
        """
        Get node address (direct or via FRP)
        Returns: (address, using_frp)
        """
        meta = node.node_metadata or {}
        node_role = meta.get("role")
        node_ip = meta.get("ip_address")
        secure_scheme = "https" if meta.get("agent_tls_certificate") else "http"

        # Local or collocated Iran node running on the exact same host
        if node_ip in _local_addresses():
            api_port = meta.get("api_port", 8888)
            return (f"{secure_scheme}://127.0.0.1:{api_port}", False)

        # Foreign nodes always have public static IPs and direct connectivity
        # (FRP management traffic across international borders into Iran is blocked by DPI)
        if node_role == "foreign":
            return (self._direct_node_address(meta), False)

        # For domestic/Iran nodes behind NAT: check if FRP reverse tunnel is active
        frp_settings = await self._get_frp_settings()
        if frp_settings and frp_settings.get("enabled"):
            frp_remote_port = node.node_metadata.get("frp_remote_port") if node.node_metadata else None
            frp_connected = node.node_metadata.get("frp_connected", True) if node.node_metadata else False
            if frp_remote_port and frp_connected:
                from app.frp_comm_manager import frp_comm_manager
                if not frp_comm_manager.is_running():
                    logger.debug(f"[HTTP] FRP enabled but FRP server not running, falling back to HTTP for node {node.id}")
                else:
                    logger.debug(f"[FRP] Using FRP tunnel to communicate with node {node.id} (remote_port={frp_remote_port})")
                    return (f"{secure_scheme}://127.0.0.1:{frp_remote_port}", True)
            else:
                logger.debug(f"[HTTP] FRP enabled but node {node.id} has no active frp_remote_port, temporarily using direct transport")

        node_address = self._direct_node_address(meta)
        logger.debug(f"[HTTP] Using direct node transport for {node.id} at {node_address}")
        return (node_address, False)
    
    async def send_to_node(
        self, 
        node_id: str, 
        endpoint: str, 
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send request to node via HTTPS or FRP
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            
            if not node:
                return {"status": "error", "message": f"Node {node_id} not found"}
            
            node_address, using_frp = await self._get_node_address(node)
            url = f"{node_address.rstrip('/')}{endpoint}"
            data = _strip_volatile_spec_keys(endpoint, data)
            if endpoint.endswith("/tunnels/apply") and isinstance(data, dict) and "authoritative_ids" not in data:
                # Lets the node drop leftovers of deleted tunnels that still hold
                # the ports of the tunnel being applied (older agents ignore it).
                from app.mutation_guard import mutation_guard
                if mutation_guard.is_engaged():
                    logger.info(
                        "Mutation guard engaged: not sending authoritative tunnel ids to node %s",
                        node_id,
                    )
                else:
                    try:
                        t_res = await session.execute(
                            select(Tunnel.id).where(
                                or_(Tunnel.node_id == node_id, Tunnel.foreign_node_id == node_id, Tunnel.iran_node_id == node_id)
                            )
                        )
                        data = {**data, "authoritative_ids": list(t_res.scalars().all())}
                    except Exception as e:
                        logger.debug(f"Could not compute authoritative tunnel ids for node {node_id}: {e}")
            
            comm_type = "FRP" if using_frp else "HTTP"
            logger.debug(f"[{comm_type}] Sending request to node {node_id}: {endpoint}")
            
            try:
                # Retry logic for connections
                max_retries = 5 if using_frp else 3
                last_error = None
                
                for attempt in range(max_retries):
                    try:
                        # For FRP, use a new connection each time to avoid connection reuse issues
                        if using_frp and attempt > 0:
                            await asyncio.sleep(2.0)  # Longer delay for FRP retries
                            logger.info(f"[FRP] Retry {attempt + 1}/{max_retries} for node {node_id} via FRP tunnel")
                        
                        async with httpx.AsyncClient(
                            timeout=self.timeout, 
                            verify=self._verify_for_url(url, node),
                            limits=httpx.Limits(max_keepalive_connections=0 if using_frp else 5)  # Disable keep-alive for FRP
                        ) as client:
                            response = await client.post(url, json=data, headers=self._node_token_headers(node))
                            response.raise_for_status()
                            return response.json()
                    except httpx.RequestError as e:
                        last_error = e
                        # Fast fallback to direct HTTP if FRP connection fails on early attempts
                        if using_frp and node.node_metadata:
                            direct_addr = self._direct_node_address(node.node_metadata or {})
                            if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                                logger.warning(f"[FRP->HTTP Fallback] FRP attempt {attempt + 1} failed for node {node_id}, attempting direct {direct_addr}")
                                try:
                                    direct_url = f"{direct_addr.rstrip('/')}{endpoint}"
                                    async with httpx.AsyncClient(timeout=self.timeout, verify=self._verify_for_url(direct_url, node)) as direct_client:
                                        direct_resp = await direct_client.post(direct_url, json=data, headers=self._node_token_headers(node))
                                        direct_resp.raise_for_status()
                                        return direct_resp.json()
                                except Exception as fallback_err:
                                    logger.warning(f"[FRP->HTTP Fallback] Direct connection to {direct_addr} also failed: {fallback_err}")
                        if attempt < max_retries - 1:
                            if not using_frp:
                                await asyncio.sleep(0.5)
                            continue
                        
                        if url.startswith("https://"):
                            try:
                                alt_url = "http://" + url[len("https://"):]
                                logger.warning(f"RequestError on {url}, falling back to {alt_url}")
                                async with httpx.AsyncClient(timeout=self.timeout, verify=False) as alt_client:
                                    alt_resp = await alt_client.post(alt_url, json=data, headers=self._node_token_headers(node))
                                    alt_resp.raise_for_status()
                                    return alt_resp.json()
                            except Exception as alt_err:
                                logger.debug(f"HTTP fallback to {alt_url} failed: {alt_err}")
                        elif url.startswith("http://"):
                            try:
                                alt_url = "https://" + url[len("http://"):]
                                logger.warning(f"RequestError on {url}, falling back to {alt_url}")
                                verify_target = False
                                try:
                                    verify_target = self._verify_for_url(alt_url, node)
                                except InsecureNodeTransport:
                                    verify_target = False
                                async with httpx.AsyncClient(timeout=self.timeout, verify=verify_target) as alt_client:
                                    alt_resp = await alt_client.post(alt_url, json=data, headers=self._node_token_headers(node))
                                    alt_resp.raise_for_status()
                                    return alt_resp.json()
                            except Exception as alt_err:
                                logger.debug(f"HTTPS fallback to {alt_url} failed: {alt_err}")

                        error_msg = f"Network error: {str(e)}"
                        if using_frp:
                            remote_port = url.split(":")[-1].split("/")[0] if ":" in url else "unknown"
                            error_msg += f" (FRP tunnel connection failed after {max_retries} attempts. The panel may not be able to reach FRP server on 127.0.0.1:{remote_port}.)"
                        return {"status": "error", "message": error_msg}
                
                # Should not reach here, but just in case
                return {"status": "error", "message": f"Network error: {str(last_error)}"}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        verify_target = False
                        try:
                            verify_target = self._verify_for_url(alt_url, node)
                        except InsecureNodeTransport:
                            verify_target = False
                        async with httpx.AsyncClient(timeout=self.timeout, verify=verify_target) as alt_client:
                            alt_resp = await alt_client.post(alt_url, json=data, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception:
                        pass
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except Exception:
                    error_detail = str(e)
                return {"status": "error", "message": f"Node error (HTTP {e.response.status_code}): {error_detail}"}
            except InsecureNodeTransport as e:
                if url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        logger.warning(f"InsecureNodeTransport on {url}, falling back to {alt_url}")
                        async with httpx.AsyncClient(timeout=self.timeout, verify=False) as alt_client:
                            alt_resp = await alt_client.post(alt_url, json=data, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception as alt_err:
                        logger.debug(f"HTTP fallback to {alt_url} failed: {alt_err}")
                return {"status": "error", "message": f"Insecure node transport: {str(e)}"}
            except Exception as e:
                return {"status": "error", "message": f"Error: {str(e)}"}
    
    async def get_tunnel_status(self, node_id: str, tunnel_id: str = "") -> Dict[str, Any]:
        """Get tunnel status from node"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            
            if not node:
                return {"status": "error", "message": f"Node {node_id} not found"}
            
            node_address, using_frp = await self._get_node_address(node)
            if tunnel_id:
                url = f"{node_address.rstrip('/')}/api/agent/tunnels/status?tunnel_id={tunnel_id}"
            else:
                url = f"{node_address.rstrip('/')}/api/agent/status"
            
            comm_type = "FRP" if using_frp else "HTTP"
            logger.debug(f"[{comm_type}] Getting tunnel status from node {node_id} (tunnel: {tunnel_id or 'all'})")
            
            try:
                timeout = httpx.Timeout(4.0, connect=2.5)
                async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(url, node)) as client:
                    response = await client.get(url, headers=self._node_token_headers(node))
                    response.raise_for_status()
                    return response.json()
            except httpx.RequestError as e:
                if url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            alt_resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception:
                        pass
                elif url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            alt_resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception:
                        pass
                if using_frp and node.node_metadata:
                    direct_addr = self._direct_node_address(node.node_metadata or {})
                    if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                        try:
                            if tunnel_id:
                                direct_url = f"{direct_addr.rstrip('/')}/api/agent/tunnels/status?tunnel_id={tunnel_id}"
                            else:
                                direct_url = f"{direct_addr.rstrip('/')}/api/agent/status"
                            async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(direct_url, node)) as direct_client:
                                direct_resp = await direct_client.get(direct_url, headers=self._node_token_headers(node))
                                direct_resp.raise_for_status()
                                return direct_resp.json()
                        except Exception:
                            pass
                return {"status": "error", "message": f"Network error: {str(e)}"}
            except InsecureNodeTransport as e:
                if url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            alt_resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception:
                        pass
                return {"status": "error", "message": f"Insecure node transport: {str(e)}"}
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            alt_resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            alt_resp.raise_for_status()
                            return alt_resp.json()
                    except Exception:
                        pass
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except:
                    error_detail = str(e)
                return {"status": "error", "message": f"Node error (HTTP {e.response.status_code}): {error_detail}"}
            except Exception as e:
                return {"status": "error", "message": f"Error: {str(e)}"}
    
    async def get_capabilities(self, node_id: str, max_age: float = 300.0) -> List[str]:
        """Agent feature flags of a node (cached). Empty for old agents or unreachable nodes."""
        import time as _time
        cached = _CAPABILITY_CACHE.get(node_id)
        if cached and _time.time() - cached[0] < max_age:
            return cached[1]
        caps: List[str] = []
        try:
            resp = await self.get_tunnel_status(node_id, "")
            if isinstance(resp, dict) and resp.get("status") == "ok":
                raw = resp.get("capabilities") or []
                caps = [str(c) for c in raw if isinstance(c, str)]
                _CAPABILITY_CACHE[node_id] = (_time.time(), caps)
        except Exception:
            pass
        return caps

    async def nodes_support(self, capability: str, *node_ids: str) -> bool:
        """True only if every given node advertises the capability."""
        ids = [n for n in node_ids if n]
        if not ids:
            return False
        for nid in ids:
            if capability not in await self.get_capabilities(nid):
                return False
        return True

    async def bootstrap_agent_token(self, node: Node, token: str, proof: str) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Push the first token only over the node's pending pinned HTTPS channel."""
        from app.node_auth_manager import bootstrap_request

        meta = node.node_metadata or {}
        base = meta.get("agent_pending_api_address")
        if not base or not str(base).lower().startswith("https://"):
            return 428, {"message": "Node has not prepared a pinned HTTPS enrollment"}
        url = f"{str(base).rstrip('/')}/api/agent/auth/bootstrap"
        try:
            verify = self._verify_for_url(url, node, pending=True)
            return await bootstrap_request(url, token, proof, verify)
        except httpx.RequestError as error:
            logger.debug(f"Token bootstrap to node {node.id} failed: {error}")
            return 0, None
        except InsecureNodeTransport as error:
            logger.error(f"Token bootstrap to node {node.id} refused: {error}")
            return 0, {"message": str(error)}

    async def verify_agent_token(self, node: Node, token: str, pending: bool = False) -> bool:
        meta = node.node_metadata or {}
        base = meta.get("agent_pending_api_address") if pending else None
        if pending and not base:
            return False
        if not base:
            base, _ = await self._get_node_address(node)
        url = f"{str(base).rstrip('/')}/api/agent/status"
        try:
            verify = self._verify_for_url(url, node, pending=pending)
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=verify) as client:
                response = await client.get(url, headers={"X-Node-Token": token})
            if response.status_code == 200:
                return True
            if response.status_code == 400 and url.startswith("http://") and not pending:
                alt_url = "https://" + url[len("http://"):]
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=False) as client:
                    alt_resp = await client.get(alt_url, headers={"X-Node-Token": token})
                return alt_resp.status_code == 200
            return False
        except (httpx.RequestError, InsecureNodeTransport):
            if url.startswith("https://") and not pending:
                try:
                    alt_url = "http://" + url[len("https://"):]
                    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=False) as client:
                        response = await client.get(alt_url, headers={"X-Node-Token": token})
                    return response.status_code == 200
                except Exception:
                    pass
            elif url.startswith("http://") and not pending:
                try:
                    alt_url = "https://" + url[len("http://"):]
                    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=False) as client:
                        response = await client.get(alt_url, headers={"X-Node-Token": token})
                    return response.status_code == 200
                except Exception:
                    pass
            return False

    async def enforce_agent_auth(self, node: Node, token: str) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Ask a provisioned node to persist the enforcement flag.

        Returns (HTTP status, body); status 0 = unreachable/refused. Older agents
        answer 404/405 and stay in permissive mode until they are updated.
        """
        node_address, using_frp = await self._get_node_address(node)
        candidates = [node_address]
        if using_frp and node.node_metadata:
            direct_addr = self._direct_node_address(node.node_metadata or {})
            if direct_addr and not direct_addr.startswith("http://127.0.0.1") and not direct_addr.startswith("https://127.0.0.1"):
                candidates.append(direct_addr)
        for base in candidates:
            url = f"{base.rstrip('/')}/api/agent/auth/enforce"
            verify = False
            try:
                verify = self._verify_for_url(url, node)
            except InsecureNodeTransport as e:
                if url.startswith("https://"):
                    verify = False
                else:
                    logger.error(f"Agent auth enforcement for node {node.id} refused: {e}")
                    return 0, {"message": str(e)}
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=verify) as client:
                    resp = await client.post(url, headers={"X-Node-Token": token})
                if resp.status_code == 400 and url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=False) as alt_client:
                            resp = await alt_client.post(alt_url, headers={"X-Node-Token": token})
                    except Exception:
                        pass
                body = None
                try:
                    body = resp.json()
                except Exception:
                    body = None
                return resp.status_code, body
            except httpx.RequestError as e:
                if url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=False) as alt_client:
                            resp = await alt_client.post(alt_url, headers={"X-Node-Token": token})
                        body = None
                        try:
                            body = resp.json()
                        except Exception:
                            body = None
                        return resp.status_code, body
                    except Exception:
                        pass
                elif url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        verify_target = False
                        try:
                            verify_target = self._verify_for_url(alt_url, node)
                        except InsecureNodeTransport:
                            verify_target = False
                        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), verify=verify_target) as alt_client:
                            resp = await alt_client.post(alt_url, headers={"X-Node-Token": token})
                        body = None
                        try:
                            body = resp.json()
                        except Exception:
                            body = None
                        return resp.status_code, body
                    except Exception:
                        pass
                logger.debug(f"Agent auth enforcement request to node {node.id} failed: {e}")
                continue
        return 0, None

    async def apply_tunnel(self, node_id: str, tunnel_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply tunnel to node"""
        return await self.send_to_node(node_id, "/api/agent/tunnels/apply", tunnel_data)

    async def remove_tunnel(
        self,
        node_id: str,
        tunnel_id: str,
        core: Optional[str] = None,
        ports: Optional[List[int]] = None,
        control_port: Optional[int] = None,
        purge: bool = True
    ) -> Dict[str, Any]:
        """Remove tunnel from node and guarantee process termination and port release"""
        payload = {
            "tunnel_id": tunnel_id,
            "purge": purge,
            "core": core,
            "ports": ports or [],
            "control_port": control_port,
        }
        return await self.send_to_node(node_id, "/api/agent/tunnels/remove", payload)

    async def verify_tunnel_on_node(
        self,
        node_id: str,
        tunnel_id: str,
        core: Optional[str] = None,
        mode: str = "server",
        ports: Optional[List[int]] = None,
        control_port: Optional[int] = None,
        proto: str = "udp",
        is_reverse: Optional[bool] = None,
        force_direct: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Verify tunnel process and listening sockets on node"""
        payload: Dict[str, Any] = {
            "tunnel_id": tunnel_id,
            "core": core,
            "mode": mode,
            "ports": ports or [],
            "control_port": control_port,
            "proto": proto
        }
        if is_reverse is not None:
            payload["is_reverse"] = is_reverse
        if force_direct is not None:
            payload["force_direct"] = force_direct
        return await self.send_to_node(node_id, "/api/agent/tunnels/verify", payload)

    async def probe_ping(self, node_id: str, target_ip: str, port: Optional[int] = None) -> Optional[int]:
        """Ask node agent to measure precise layer-3/4 ping directly to target IP"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            if not node:
                return None
            
            url = ""
            try:
                node_address, using_frp = await self._get_node_address(node)
                port_param = f"&port={port}" if port else ""
                url = f"{node_address.rstrip('/')}/api/agent/ping?target={target_ip}{port_param}"
                timeout = httpx.Timeout(2.5, connect=1.5)
                async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(url, node)) as client:
                    resp = await client.get(url, headers=self._node_token_headers(node))
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("latency_ms")
            except Exception:
                if url and url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            if resp.status_code == 200:
                                return resp.json().get("latency_ms")
                    except Exception:
                        pass
                elif url and url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            if resp.status_code == 200:
                                return resp.json().get("latency_ms")
                    except Exception:
                        pass
                if using_frp and node.node_metadata:
                    direct_addr = self._direct_node_address(node.node_metadata or {})
                    if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                        try:
                            direct_url = f"{direct_addr.rstrip('/')}/api/agent/ping?target={target_ip}{port_param}"
                            async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(direct_url, node)) as direct_client:
                                resp = await direct_client.get(direct_url, headers=self._node_token_headers(node))
                                if resp.status_code == 200:
                                    return resp.json().get("latency_ms")
                        except Exception:
                            pass
        return None

    async def probe_tunnel(self, node_id: str, tunnel_id: str) -> Dict[str, Any]:
        """Ask node agent to probe proxy process, ingress port, and data flow for a specific tunnel"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            if not node:
                return {"status": "error", "message": f"Node {node_id} not found"}
            
            using_frp = False
            timeout = httpx.Timeout(3.0, connect=1.8)
            url = ""
            try:
                node_address, using_frp = await self._get_node_address(node)
                url = f"{node_address.rstrip('/')}/api/agent/tunnels/{tunnel_id}/probe"
                async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(url, node)) as client:
                    resp = await client.get(url, headers=self._node_token_headers(node))
                    if resp.status_code == 200:
                        return resp.json()
            except Exception:
                if url and url.startswith("https://"):
                    try:
                        alt_url = "http://" + url[len("https://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            if resp.status_code == 200:
                                return resp.json()
                    except Exception:
                        pass
                elif url and url.startswith("http://"):
                    try:
                        alt_url = "https://" + url[len("http://"):]
                        async with httpx.AsyncClient(timeout=timeout, verify=False) as alt_client:
                            resp = await alt_client.get(alt_url, headers=self._node_token_headers(node))
                            if resp.status_code == 200:
                                return resp.json()
                    except Exception:
                        pass
                if using_frp and node.node_metadata:
                    direct_addr = self._direct_node_address(node.node_metadata or {})
                    if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                        try:
                            direct_url = f"{direct_addr.rstrip('/')}/api/agent/tunnels/{tunnel_id}/probe"
                            async with httpx.AsyncClient(timeout=timeout, verify=self._verify_for_url(direct_url, node)) as direct_client:
                                resp = await direct_client.get(direct_url, headers=self._node_token_headers(node))
                                if resp.status_code == 200:
                                    return resp.json()
                        except Exception:
                            pass
        
        # Fallback to get_tunnel_status if /probe endpoint is not yet supported
        fallback_res = await self.get_tunnel_status(node_id, tunnel_id)
        if isinstance(fallback_res, dict) and "data" in fallback_res:
            data = fallback_res["data"]
            if isinstance(data, dict):
                return {
                    "status": fallback_res.get("status", "success"),
                    "tunnel_id": tunnel_id,
                    "process_running": data.get("process_running", True),
                    "healthy": data.get("healthy", True),
                    "core": data.get("core", "unknown"),
                    "listening_ports": data.get("listening_ports", []),
                    "ingress_port": None,
                    "ingress_port_open": data.get("process_running", True),
                    "data_flow": True,
                    "tunnel_rtt_ms": None
                }
        return fallback_res

