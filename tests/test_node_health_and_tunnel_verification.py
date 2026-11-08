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
from datetime import datetime, timedelta, timezone
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
    mock_node.registered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    mock_node.last_seen = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
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
    mock_node.registered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    mock_node.last_seen = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
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
    mock_node.registered_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    mock_node.last_seen = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
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


@pytest.mark.asyncio
async def test_agent_check_ports_listening_endpoint():
    """Verify agent /ports/check-listening reports listening and missing ports"""
    from node.app.routers.agent import check_ports_listening, CheckPortsRequest

    mock_request = MagicMock()
    with patch("node.app.core_adapters.is_port_listening_locally", side_effect=lambda p, proto='tcp': p == 8080):
        req = CheckPortsRequest(ports=[8080, 9999], proto="tcp")
        resp = await check_ports_listening(req, mock_request)
        assert resp["status"] == "success"
        assert resp["ports"][8080]["listening"] is True
        assert resp["ports"][9999]["listening"] is False


@pytest.mark.asyncio
async def test_test_tunnel_config_destination_port_warning_and_gost_direct():
    """Verify test_tunnel_config reports warning when foreign destination port is closed and when GOST direct is chosen"""
    from app.routers.tunnels import test_tunnel_config
    from app.models import Node

    iran_node = Node(id="ir-1", name="IR Node", status="active", node_metadata={"role": "iran", "ip_address": "1.1.1.1"})
    fo_node = Node(id="fo-1", name="FO Node", status="active", node_metadata={"role": "foreign", "ip_address": "2.2.2.2"})

    mock_db = AsyncMock()
    mock_res_ir = MagicMock()
    mock_res_ir.scalar_one_or_none.return_value = iran_node
    mock_res_fo = MagicMock()
    mock_res_fo.scalar_one_or_none.return_value = fo_node
    mock_res_tuns = MagicMock()
    mock_res_tuns.scalars.return_value.all.return_value = []

    mock_db.execute = AsyncMock(side_effect=[mock_res_ir, mock_res_fo, mock_res_tuns])

    # Direct mode GOST: is_reverse=False
    payload = {
        "core": "gost",
        "iran_node_id": "ir-1",
        "foreign_node_id": "fo-1",
        "ports": "1080",
        "transport": "tcp",
        "is_reverse": False,
    }

    with patch("app.routers.tunnels.measure_node_latency", AsyncMock(return_value=(True, 30, "online"))), \
         patch("app.routers.tunnels.NodeClient.check_node_listening_ports", AsyncMock(return_value={1080: {"listening": False}})):

        res = await test_tunnel_config(payload, db=mock_db, current_user=MagicMock())
        assert res["valid"] is True  # Non-blocking warning keeps valid=True

        check_names = {c["name"]: c for c in res["checks"]}
        assert "destination_ports" in check_names
        assert check_names["destination_ports"]["status"] == "warning"
        assert "1080 not listening" in check_names["destination_ports"]["detail"]

        assert "gost_direction" in check_names
        assert check_names["gost_direction"]["status"] == "warning"
        assert "Reverse mode + TLS is strongly recommended" in check_names["gost_direction"]["detail"]


@pytest.mark.asyncio
async def test_inspect_tunnel_health_detects_log_errors_and_missing_estab(tmp_path, monkeypatch):
    """inspect_tunnel_health maps log error patterns and missing reverse control ESTAB to degraded status."""
    import node.app.core_adapters as ca
    from node.app.core_adapters import AdapterManager

    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"
    tunnel_id = "test-degraded-tunnel"
    manager.tunnel_configs[tunnel_id] = {
        "core": "gost",
        "spec": {
            "mode": "server",
            "is_reverse": True,
            "control_port": 35000,
            "ports": [8080]
        }
    }

    # Simulate process alive, ports listening, but control port has NO established connection
    monkeypatch.setattr(ca, "_is_tunnel_pid_alive", lambda tid, core=None: True)
    monkeypatch.setattr(ca, "is_port_listening_locally", lambda p, proto='tcp': True)
    monkeypatch.setattr(ca, "_has_established_connection_on_port", lambda p: False)
    monkeypatch.setattr(ca, "_check_core_log_for_errors", lambda tid, core=None: (False, None))

    st = await manager.inspect_tunnel_health(tunnel_id)
    assert st["healthy"] is False
    assert st["health_state"] == "degraded"
    assert "No established connection on control port 35000" in st["degraded_reason"]

    # Now simulate log error pattern "os error 98"
    monkeypatch.setattr(ca, "_has_established_connection_on_port", lambda p: True)
    monkeypatch.setattr(ca, "_check_core_log_for_errors", lambda tid, core=None: (True, "Recent error in core log: 'os error 98'"))

    st2 = await manager.inspect_tunnel_health(tunnel_id)
    assert st2["healthy"] is False
    assert st2["health_state"] == "degraded"
    assert "os error 98" in st2["degraded_reason"]


@pytest.mark.asyncio
async def test_inspect_tunnel_health_idle_reverse_tunnel_stays_healthy(tmp_path, monkeypatch):
    """An idle reverse tunnel with established control connection and no log errors remains healthy."""
    import node.app.core_adapters as ca
    from node.app.core_adapters import AdapterManager

    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"
    tunnel_id = "test-idle-tunnel"
    manager.tunnel_configs[tunnel_id] = {
        "core": "gost",
        "spec": {
            "mode": "server",
            "is_reverse": True,
            "control_port": 35001,
            "ports": [8080]
        }
    }

    monkeypatch.setattr(ca, "_is_tunnel_pid_alive", lambda tid, core=None: True)
    monkeypatch.setattr(ca, "is_port_listening_locally", lambda p, proto='tcp': True)
    monkeypatch.setattr(ca, "_has_established_connection_on_port", lambda p: True)
    monkeypatch.setattr(ca, "_check_core_log_for_errors", lambda tid, core=None: (False, None))

    st = await manager.inspect_tunnel_health(tunnel_id)
    assert st["healthy"] is True
    assert st["health_state"] == "healthy"
    assert st["degraded_reason"] is None


@pytest.mark.asyncio
async def test_test_active_tunnel_marks_degraded_status():
    """test_active_tunnel marks tunnel as degraded when node reports degraded health."""
    from app.routers.tunnels import test_active_tunnel
    from app.models import Tunnel, Node

    tunnel = Tunnel(
        id="tun-deg-1",
        name="Degraded Tunnel",
        core="gost",
        type="reverse",
        iran_node_id="ir-1",
        foreign_node_id="fo-1",
        spec={"is_reverse": True, "control_port": 35000}
    )
    ir_node = Node(id="ir-1", name="Iran Node", node_metadata={"ip_address": "1.1.1.1"})
    fo_node = Node(id="fo-1", name="Foreign Node", node_metadata={"ip_address": "2.2.2.2"})

    mock_db = AsyncMock()
    mock_res_tun = MagicMock()
    mock_res_tun.scalar_one_or_none.return_value = tunnel
    mock_res_ir = MagicMock()
    mock_res_ir.scalar_one_or_none.return_value = ir_node
    mock_res_fo = MagicMock()
    mock_res_fo.scalar_one_or_none.return_value = fo_node

    mock_db.execute = AsyncMock(side_effect=[mock_res_tun, mock_res_ir, mock_res_fo])

    iran_probe_resp = {
        "status": "success",
        "process_running": True,
        "healthy": False,
        "health_state": "degraded",
        "degraded_reason": "No established connection on control port 35000",
        "tunnel_rtt_ms": 45
    }
    fo_probe_resp = {
        "status": "success",
        "process_running": True,
        "healthy": True,
        "health_state": "healthy"
    }

    with patch("app.routers.tunnels.NodeClient.probe_tunnel", AsyncMock(side_effect=[iran_probe_resp, fo_probe_resp])), \
         patch("app.routers.tunnels.NodeClient.probe_ping", AsyncMock(return_value=45)):
        res = await test_active_tunnel("tun-deg-1", db=mock_db, current_user=MagicMock())
        assert res["status"] == "degraded"
        assert "No established connection on control port" in res["message"]
        assert tunnel.spec.get("health_state") == "degraded"



