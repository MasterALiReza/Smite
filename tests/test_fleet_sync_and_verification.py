import sys
from unittest.mock import MagicMock

# Ensure repository root and panel are in sys.path
from pathlib import Path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
panel_dir = repo_root / "panel"
if str(panel_dir) not in sys.path:
    sys.path.insert(0, str(panel_dir))

import pytest
import socket
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from node.app.core_adapters import is_port_listening_locally, AdapterManager


def test_is_port_listening_locally_tcp():
    """Test TCP port listening detector with an ephemeral bound socket"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)
    bound_port = srv.getsockname()[1]
    
    try:
        assert is_port_listening_locally(bound_port, proto="tcp") is True
        assert is_port_listening_locally(bound_port, proto="any") is True
    finally:
        srv.close()
    
    # After close, port should not be listening
    assert is_port_listening_locally(bound_port, proto="tcp") is False


def test_is_port_listening_locally_udp():
    """Test UDP port listening detector with an ephemeral bound socket"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', 0))
    bound_port = srv.getsockname()[1]
    
    try:
        assert is_port_listening_locally(bound_port, proto="udp") is True
        assert is_port_listening_locally(bound_port, proto="any") is True
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_inspect_tunnel_health_server_mode():
    """Test AdapterManager.inspect_tunnel_health in server mode"""
    manager = AdapterManager()
    
    # Mock adapter
    mock_adapter = MagicMock()
    mock_adapter.status.return_value = {"process_running": True, "active": True}
    manager.active_tunnels["test-tunnel-1"] = mock_adapter
    
    # Bind an ephemeral UDP socket to simulate active service port
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(('127.0.0.1', 0))
    active_port = srv.getsockname()[1]
    
    # Choose an unbound port
    inactive_port = 59998
    
    try:
        # 1. Test when port is active
        res = await manager.inspect_tunnel_health(
            tunnel_id="test-tunnel-1",
            tunnel_core="rathole",
            mode="server",
            ports=[active_port],
            proto="udp"
        )
        assert res["process_running"] is True
        assert res["healthy"] is True
        assert len(res["listening_ports"]) == 1
        assert res["listening_ports"][0]["port"] == active_port
        assert len(res["missing_ports"]) == 0
        
        # 2. Test when port is missing
        res_missing = await manager.inspect_tunnel_health(
            tunnel_id="test-tunnel-1",
            tunnel_core="rathole",
            mode="server",
            ports=[inactive_port],
            proto="udp"
        )
        assert res_missing["process_running"] is True
        assert res_missing["healthy"] is False
        assert len(res_missing["missing_ports"]) == 1
        assert res_missing["missing_ports"][0]["port"] == inactive_port
    finally:
        srv.close()


@pytest.mark.asyncio
async def test_fleet_ghost_purge_exclusion_logic():
    """Test that active nodes are preserved and only obsolete nodes receive ghost cleanup"""
    class MockNode:
        def __init__(self, node_id, name):
            self.id = node_id
            self.name = name

    all_nodes = [
        MockNode("iran-1", "Iran Node 1"),
        MockNode("turkey-2", "Turkey Node 2 (Active)"),
        MockNode("turkey-1", "Turkey Node 1 (Ghost)"),
        MockNode("germany-1", "Germany Node 1 (Ghost)")
    ]

    iran_node = all_nodes[0]
    foreign_node = all_nodes[1]

    # Active nodes
    active_node_ids = {iran_node.id, foreign_node.id}
    other_nodes = [n for n in all_nodes if n.id not in active_node_ids]

    assert len(other_nodes) == 2
    assert {n.id for n in other_nodes} == {"turkey-1", "germany-1"}
    assert "iran-1" not in {n.id for n in other_nodes}
    assert "turkey-2" not in {n.id for n in other_nodes}


@pytest.mark.asyncio
async def test_node_client_verify_tunnel():
    """Test NodeClient.verify_tunnel_on_node method call"""
    from panel.app.node_client import NodeClient
    client = NodeClient()
    client.send_to_node = AsyncMock(return_value={"status": "success", "data": {"healthy": True}})
    
    result = await client.verify_tunnel_on_node(
        node_id="node-123",
        tunnel_id="tunnel-456",
        core="rathole",
        mode="server",
        ports=[45500],
        control_port=36379,
        proto="udp"
    )
    
    assert result["status"] == "success"
    client.send_to_node.assert_called_once_with(
        "node-123",
        "/api/agent/tunnels/verify",
        {
            "tunnel_id": "tunnel-456",
            "core": "rathole",
            "mode": "server",
            "ports": [45500],
            "control_port": 36379,
            "proto": "udp"
        }
    )


@pytest.mark.asyncio
async def test_node_client_get_tunnel_status_endpoints():
    """Test NodeClient.get_tunnel_status routes to tunnel-specific status endpoint when tunnel_id is provided"""
    from panel.app.node_client import NodeClient
    client = NodeClient()
    
    mock_node = MagicMock()
    mock_node.id = "node-1"
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
         patch.object(client, "_get_node_address", AsyncMock(return_value=("http://10.0.0.1:8888", False))), \
         patch("httpx.AsyncClient") as mock_client_cls:
        
        mock_http = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success", "data": {"active": True}}
        mock_http.get.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_http
        
        # 1. With tunnel_id: should target /api/agent/tunnels/status?tunnel_id=...
        res = await client.get_tunnel_status("node-1", tunnel_id="tunnel-gost-1")
        assert res["status"] == "success"
        called_url = mock_http.get.call_args[0][0]
        assert called_url == "http://10.0.0.1:8888/api/agent/tunnels/status?tunnel_id=tunnel-gost-1"
        
        # 2. Without tunnel_id: should target general node status
        await client.get_tunnel_status("node-1")
        called_url_general = mock_http.get.call_args[0][0]
        assert called_url_general == "http://10.0.0.1:8888/api/agent/status"


@pytest.mark.asyncio
async def test_gost_adapter_apply_validation_preserves_running_process():
    """Test GostAdapter.apply validates spec before killing existing running process"""
    from node.app.core_adapters import GostAdapter
    adapter = GostAdapter()
    adapter.remove = AsyncMock()
    
    dummy_proc = MagicMock()
    adapter.processes["tunnel-gost-1"] = dummy_proc
    
    # 1. Missing control_port should raise ValueError and NOT remove running process
    with pytest.raises(ValueError, match="GOST requires 'control_port'"):
        await adapter.apply("tunnel-gost-1", {"mode": "client"})
    adapter.remove.assert_not_called()
    assert adapter.processes["tunnel-gost-1"] is dummy_proc
    
    # 2. Client missing server_ip should raise ValueError and NOT remove running process
    with pytest.raises(ValueError, match="GOST client requires 'server_ip'"):
        await adapter.apply("tunnel-gost-1", {"mode": "client", "control_port": 1234})
    adapter.remove.assert_not_called()
    assert adapter.processes["tunnel-gost-1"] is dummy_proc
    
    # 3. Client missing ports array/listen_port should raise ValueError and NOT remove running process
    with pytest.raises(ValueError, match="GOST client requires 'ports' array"):
        await adapter.apply("tunnel-gost-1", {"mode": "client", "control_port": 1234, "server_ip": "1.2.3.4"})
    adapter.remove.assert_not_called()
    assert adapter.processes["tunnel-gost-1"] is dummy_proc


@pytest.mark.asyncio
async def test_adapter_manager_get_tunnel_status_adopts_persisted_or_pid():
    """Test AdapterManager.get_tunnel_status recovers and adopts live status when not already in active_tunnels"""
    from node.app.core_adapters import AdapterManager
    manager = AdapterManager()
    
    # 1. Unregistered tunnel -> active False
    res = await manager.get_tunnel_status("unknown-tunnel")
    assert res["active"] is False
    
    # 2. Persisted in tunnel_configs with living adapter status -> auto-adopts into active_tunnels
    mock_adapter = MagicMock()
    mock_adapter.status.return_value = {"active": True, "type": "gost", "process_running": True}
    manager.adapters["gost"] = mock_adapter
    manager.tunnel_configs["persisted-tunnel"] = {"core": "gost", "spec": {}}
    
    res = await manager.get_tunnel_status("persisted-tunnel")
    assert res["active"] is True
    assert "persisted-tunnel" in manager.active_tunnels


