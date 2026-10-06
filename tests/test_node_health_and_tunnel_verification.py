import sys
from pathlib import Path

# Ensure repository root, panel, and node are in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
# Ensure repository root and panel are in sys.path
panel_dir = repo_root / "panel"
if str(panel_dir) not in sys.path:
    sys.path.insert(0, str(panel_dir))

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import httpx


@pytest.mark.asyncio
async def test_node_health_offline_no_fake_latency():
    """Verify that a node with stale last_seen and failed probe is strictly failed with None latency (no 45ms fake)"""
    from panel.app.routers.nodes import check_node_status, _node_status_cache
    _node_status_cache.clear()

    # Create mock node with last_seen 5 minutes ago and frp_connected: True
    mock_node = MagicMock()
    mock_node.id = "test-node-dead-1"
    mock_node.name = "IR FanAva-MT"
    mock_node.status = "active"
    mock_node.fingerprint = "fp123"
    mock_node.registered_at = datetime.utcnow() - timedelta(minutes=10)
    mock_node.last_seen = datetime.utcnow() - timedelta(minutes=5)
    mock_node.node_metadata = {
        "ip_address": "94.184.34.13",
        "api_port": 8888,
        "frp_connected": True,
        "role": "iran"
    }

    with patch("panel.app.routers.nodes.NodeClient") as MockClient:
        mock_client_instance = MockClient.return_value
        # Simulate connection error / timeout
        mock_client_instance.get_tunnel_status = AsyncMock(side_effect=httpx.ConnectError("Connection timed out"))

        resp = await check_node_status(mock_node)

        assert resp.metadata["connection_status"] == "failed"
        assert resp.metadata["latency_ms"] is None


@pytest.mark.asyncio
async def test_node_health_reconnecting_when_recent_heartbeat():
    """Verify that a node with recent last_seen (< 90s) gets reconnecting instead of fake connected on glitch"""
    from panel.app.routers.nodes import check_node_status, _node_status_cache
    _node_status_cache.clear()

    mock_node = MagicMock()
    mock_node.id = "test-node-glitch-1"
    mock_node.name = "TR-Node-1 Normal"
    mock_node.status = "active"
    mock_node.fingerprint = "fp456"
    mock_node.registered_at = datetime.utcnow() - timedelta(minutes=10)
    mock_node.last_seen = datetime.utcnow() - timedelta(seconds=30)
    mock_node.node_metadata = {
        "ip_address": "213.142.148.254",
        "api_port": 8888,
        "role": "foreign"
    }

    with patch("panel.app.routers.nodes.NodeClient") as MockClient:
        mock_client_instance = MockClient.return_value
        mock_client_instance.get_tunnel_status = AsyncMock(side_effect=asyncio.TimeoutError())

        resp = await check_node_status(mock_node)

        assert resp.metadata["connection_status"] == "reconnecting"
        assert resp.metadata["latency_ms"] is None


@pytest.mark.asyncio
async def test_node_health_live_connected_returns_real_rtt():
    """Verify that an online node returns connected with real measured elapsed RTT"""
    from panel.app.routers.nodes import check_node_status, _node_status_cache
    _node_status_cache.clear()

    mock_node = MagicMock()
    mock_node.id = "test-node-healthy-1"
    mock_node.name = "FN-Node-Hetz"
    mock_node.status = "active"
    mock_node.fingerprint = "fp789"
    mock_node.registered_at = datetime.utcnow() - timedelta(minutes=10)
    mock_node.last_seen = datetime.utcnow() - timedelta(seconds=10)
    mock_node.node_metadata = {
        "ip_address": "65.109.191.129",
        "api_port": 8889,
        "role": "foreign"
    }

    with patch("panel.app.routers.nodes.NodeClient") as MockClient:
        mock_client_instance = MockClient.return_value
        mock_client_instance.get_tunnel_status = AsyncMock(return_value={"status": "ok", "active_tunnels": 2})

        resp = await check_node_status(mock_node)

        assert resp.metadata["connection_status"] == "connected"
        assert isinstance(resp.metadata["latency_ms"], int)
        assert resp.metadata["latency_ms"] >= 1


@pytest.mark.asyncio
async def test_tunnel_probe_endpoint_in_agent():
    """Verify probe_tunnel logic handles both listening ingress ports and client mode"""
    from node.app.routers.agent import probe_tunnel
    from unittest.mock import MagicMock

    mock_request = MagicMock()
    mock_adapter_mgr = MagicMock()
    mock_request.app.state.adapter_manager = mock_adapter_mgr

    # Mock tunnel status with no listening ports (client side)
    mock_adapter_mgr.get_tunnel_status = AsyncMock(return_value={
        "healthy": True,
        "process_running": True,
        "core": "gost",
        "listening_ports": [],
        "missing_ports": []
    })

    resp = await probe_tunnel("tunnel-test-123", mock_request)
    assert resp["status"] == "success"
    assert resp["process_running"] is True
    assert resp["data_flow"] is True
    assert resp["core"] == "gost"


@pytest.mark.asyncio
async def test_tunnel_probe_endpoint_in_agent_udp():
    """Verify probe_tunnel properly handles UDP service ports without attempting invalid TCP handshakes"""
    from node.app.routers.agent import probe_tunnel
    from unittest.mock import MagicMock

    mock_request = MagicMock()
    mock_adapter_mgr = MagicMock()
    mock_request.app.state.adapter_manager = mock_adapter_mgr

    mock_adapter_mgr.get_tunnel_status = AsyncMock(return_value={
        "healthy": True,
        "process_running": True,
        "core": "rathole",
        "listening_ports": [{"port": 45500, "type": "service_udp"}],
        "missing_ports": []
    })

    resp = await probe_tunnel("tunnel-udp-123", mock_request)
    assert resp["status"] == "success"
    assert resp["process_running"] is True
    assert resp["ingress_port"] == 45500
    assert resp["ingress_port_open"] is True
    assert resp["data_flow"] is True


@pytest.mark.asyncio
async def test_node_client_probe_tunnel_fallback_unpacks_data():
    """Verify NodeClient.probe_tunnel fallback normalizes nested data payload for backward compatibility"""
    from panel.app.node_client import NodeClient
    client = NodeClient()

    mock_node = MagicMock()
    mock_node.id = "node-old-agent"
    mock_node.node_metadata = {
        "api_address": "http://10.0.0.1:8888",
        "api_port": 8888,
        "token": "test-token"
    }

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_node
    mock_session.execute.return_value = mock_res

    mock_session_ctx = MagicMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("panel.app.node_client.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch.object(client, "_get_node_address", AsyncMock(side_effect=Exception("Simulate probe network error"))), \
         patch.object(client, "get_tunnel_status", AsyncMock(return_value={
             "status": "success",
             "data": {
                 "process_running": True,
                 "healthy": True,
                 "core": "gost",
                 "listening_ports": []
             }
         })):
        
        resp = await client.probe_tunnel("node-old-agent", "tunnel-xyz")
        assert resp["status"] == "success"
        assert resp["process_running"] is True
        assert resp["healthy"] is True
        assert resp["core"] == "gost"

