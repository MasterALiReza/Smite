"""Client for panel to communicate with nodes"""
import httpx
import ssl
import logging
import asyncio
from typing import Dict, Any, Optional, Tuple
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Node, Settings
from app.config import settings

logger = logging.getLogger(__name__)


class NodeClient:
    """Client to send requests to nodes via HTTP/HTTPS or FRP"""
    
    def __init__(self):
        self.timeout = httpx.Timeout(30.0)
    
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
        frp_settings = await self._get_frp_settings()
        
        if frp_settings and frp_settings.get("enabled"):
            node_role = node.node_metadata.get("role") if node.node_metadata else None
            node_ip = node.node_metadata.get("ip_address") if node.node_metadata else None
            
            # Local or collocated Iran nodes should always use direct local connection
            if node_role == "iran" or node_ip in ["127.0.0.1", "localhost", "178.239.146.188"]:
                if node_ip in ["127.0.0.1", "localhost", "178.239.146.188"]:
                    return ("http://127.0.0.1:8888", False)
                node_address = node.node_metadata.get("api_address", "http://127.0.0.1:8888") if node.node_metadata else "http://127.0.0.1:8888"
                if not node_address.startswith("http"):
                    node_address = f"http://{node_address}"
                return (node_address, False)

            frp_remote_port = node.node_metadata.get("frp_remote_port") if node.node_metadata else None
            if frp_remote_port:
                from app.frp_comm_manager import frp_comm_manager
                if not frp_comm_manager.is_running():
                    logger.warning(f"[HTTP] FRP enabled but FRP server not running, falling back to HTTP for node {node.id}")
                else:
                    logger.info(f"[FRP] Using FRP tunnel to communicate with node {node.id} (remote_port={frp_remote_port})")
                    return (f"http://127.0.0.1:{frp_remote_port}", True)
            else:
                logger.warning(f"[HTTP] FRP enabled but node {node.id} has no frp_remote_port yet, temporarily using HTTP")
        
        # Direct HTTP
        if node.node_metadata and node.node_metadata.get("ip_address") in ["127.0.0.1", "localhost", "178.239.146.188"]:
            return ("http://127.0.0.1:8888", False)
        node_address = node.node_metadata.get("api_address", "http://127.0.0.1:8888") if node.node_metadata else "http://127.0.0.1:8888"
        if not node_address.startswith("http"):
            node_address = f"http://{node_address}"
        logger.info(f"[HTTP] Using direct HTTP to communicate with node {node.id} at {node_address}")
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
                        
                        verify_val = False
                        try:
                            cert_file = Path(settings.node_cert_path)
                            if not cert_file.is_absolute():
                                cert_file = Path.cwd() / cert_file
                            if cert_file.exists() and cert_file.stat().st_size > 0:
                                verify_val = str(cert_file)
                        except Exception:
                            verify_val = False

                        async with httpx.AsyncClient(
                            timeout=self.timeout, 
                            verify=verify_val,
                            limits=httpx.Limits(max_keepalive_connections=0 if using_frp else 5)  # Disable keep-alive for FRP
                        ) as client:
                            response = await client.post(url, json=data)
                            response.raise_for_status()
                            return response.json()
                    except httpx.RequestError as e:
                        last_error = e
                        if attempt < max_retries - 1:
                            if not using_frp:
                                await asyncio.sleep(0.5)
                            continue
                        elif using_frp and node.node_metadata:
                            # Fallback to direct HTTP if FRP connection fails
                            direct_addr = node.node_metadata.get("api_address") or f"http://{node.node_metadata.get('ip_address')}:{node.node_metadata.get('api_port', 8888)}"
                            if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                                logger.warning(f"[FRP->HTTP Fallback] FRP connection failed for node {node_id}, falling back to direct {direct_addr}")
                                try:
                                    direct_url = f"{direct_addr.rstrip('/')}{endpoint}"
                                    async with httpx.AsyncClient(timeout=self.timeout, verify=False) as direct_client:
                                        direct_resp = await direct_client.post(direct_url, json=data)
                                        direct_resp.raise_for_status()
                                        return direct_resp.json()
                                except Exception as fallback_err:
                                    logger.warning(f"[FRP->HTTP Fallback] Direct connection to {direct_addr} also failed: {fallback_err}")
                        
                        error_msg = f"Network error: {str(e)}"
                        if using_frp:
                            remote_port = url.split(":")[-1].split("/")[0] if ":" in url else "unknown"
                            error_msg += f" (FRP tunnel connection failed after {max_retries} attempts. The panel may not be able to reach FRP server on 127.0.0.1:{remote_port}. Check if panel and FRP server are in the same network namespace, or check FRP server logs.)"
                        return {"status": "error", "message": error_msg}
                
                # Should not reach here, but just in case
                return {"status": "error", "message": f"Network error: {str(last_error)}"}
            except httpx.HTTPStatusError as e:
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except:
                    error_detail = str(e)
                return {"status": "error", "message": f"Node error (HTTP {e.response.status_code}): {error_detail}"}
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
            url = f"{node_address.rstrip('/')}/api/agent/status"
            
            comm_type = "FRP" if using_frp else "HTTP"
            logger.debug(f"[{comm_type}] Getting tunnel status from node {node_id}")
            
            try:
                timeout = httpx.Timeout(3.0, connect=2.0)
                async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except httpx.RequestError as e:
                if using_frp and node.node_metadata:
                    direct_addr = node.node_metadata.get("api_address") or f"http://{node.node_metadata.get('ip_address')}:{node.node_metadata.get('api_port', 8888)}"
                    if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                        try:
                            direct_url = f"{direct_addr.rstrip('/')}/api/agent/status"
                            async with httpx.AsyncClient(timeout=timeout, verify=False) as direct_client:
                                direct_resp = await direct_client.get(direct_url)
                                direct_resp.raise_for_status()
                                return direct_resp.json()
                        except Exception:
                            pass
                return {"status": "error", "message": f"Network error: {str(e)}"}
            except httpx.HTTPStatusError as e:
                try:
                    error_detail = e.response.json().get("detail", str(e))
                except:
                    error_detail = str(e)
                return {"status": "error", "message": f"Node error (HTTP {e.response.status_code}): {error_detail}"}
            except Exception as e:
                return {"status": "error", "message": f"Error: {str(e)}"}
    
    async def apply_tunnel(self, node_id: str, tunnel_data: Dict[str, Any]) -> Dict[str, Any]:
        """Apply tunnel to node"""
        return await self.send_to_node(node_id, "/api/agent/tunnels/apply", tunnel_data)

    async def probe_ping(self, node_id: str, target_ip: str, port: Optional[int] = None) -> Optional[int]:
        """Ask node agent to measure precise layer-3/4 ping directly to target IP"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            if not node:
                return None
            
            node_address, using_frp = await self._get_node_address(node)
            port_param = f"&port={port}" if port else ""
            url = f"{node_address.rstrip('/')}/api/agent/ping?target={target_ip}{port_param}"
            
            try:
                timeout = httpx.Timeout(2.5, connect=1.5)
                async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        return data.get("latency_ms")
            except Exception:
                if using_frp and node.node_metadata:
                    direct_addr = node.node_metadata.get("api_address") or f"http://{node.node_metadata.get('ip_address')}:{node.node_metadata.get('api_port', 8888)}"
                    if direct_addr and not direct_addr.startswith("http://127.0.0.1"):
                        try:
                            direct_url = f"{direct_addr.rstrip('/')}/api/agent/ping?target={target_ip}{port_param}"
                            async with httpx.AsyncClient(timeout=timeout, verify=False) as direct_client:
                                resp = await direct_client.get(direct_url)
                                if resp.status_code == 200:
                                    return resp.json().get("latency_ms")
                        except Exception:
                            pass
        return None

