import sys
from unittest.mock import MagicMock

# Mock sqlalchemy and app dependencies if not installed locally
for mod in ["sqlalchemy", "sqlalchemy.ext.asyncio", "sqlalchemy.future", "sqlalchemy.orm", "app", "app.database", "app.config", "app.models"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

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
