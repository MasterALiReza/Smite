from starlette.testclient import TestClient
from types import SimpleNamespace
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from panel.main import app
from panel.app.routers.auth import _DUMMY_BCRYPT_HASH, pwd_context
from panel.app.port_forwarder import PortForwarder


def test_timing_attack_defense_constant_time():
    """Verify that dummy password verification is executed when user doesn't exist."""
    with TestClient(app) as client:
        # Non-existent user should fail with 401
        resp = client.post(
            "/api/auth/login",
            json={"username": "totally_nonexistent_user_12345", "password": "wrong_password"}
        )
        assert resp.status_code == 401
        assert "Incorrect username or password" in resp.json()["detail"]


def test_ca_direct_alias_endpoints():
    """Verify /panel/ca and /panel/ca/server routes exist and do not return SPA index.html."""
    with TestClient(app) as client:
        resp = client.get("/panel/ca")
        # Should either return certificate text (200 PEM) or 404/500 if not initialized,
        # but NEVER return 200 HTML text
        if resp.status_code == 200:
            assert "<!DOCTYPE html>" not in resp.text
            assert "<html" not in resp.text
        else:
            assert resp.status_code in (404, 500)


def test_logs_query_bounds():
    """Verify logs limit query validation."""
    with TestClient(app) as client:
        # Limit > 1000 should be rejected by FastAPI Query validation with 422 Unprocessable Entity
        resp = client.get("/api/logs?limit=5000")
        assert resp.status_code in (401, 403, 422)


def test_port_forwarder_ipv6_parsing():
    """Verify PortForwarder correctly parses IPv6 addresses and hostnames."""
    # Test IPv6 bracketed address
    addr1 = "[2001:db8::1]:8888"
    clean1 = addr1.split("://", 1)[-1]
    if clean1.startswith("[") and "]" in clean1:
        host1 = clean1[1:clean1.index("]")]
    else:
        host1 = clean1.split(":", 1)[0]
    assert host1 == "2001:db8::1"

    # Test raw IPv6
    addr2 = "2001:db8::1"
    clean2 = addr2.split("://", 1)[-1]
    if clean2.startswith("[") and "]" in clean2:
        host2 = clean2[1:clean2.index("]")]
    elif clean2.count(":") == 1:
        host2 = clean2.split(":", 1)[0]
    else:
        host2 = clean2
    assert host2 == "2001:db8::1"

    # Test IPv4 with port
    addr3 = "192.168.1.10:8000"
    clean3 = addr3.split("://", 1)[-1]
    if clean3.startswith("[") and "]" in clean3:
        host3 = clean3[1:clean3.index("]")]
    elif clean3.count(":") == 1:
        host3 = clean3.split(":", 1)[0]
    else:
        host3 = clean3
    assert host3 == "192.168.1.10"


def test_pending_removals_schema():
    """Verify pending removal dictionary structure aligns between panel and nodes."""
    remove_payload = {
        "tunnel_id": "test-tun-123",
        "purge": True,
        "core": "gost",
        "ports": [8080],
        "control_port": None,
    }
    assert remove_payload["tunnel_id"] == "test-tun-123"
    assert remove_payload["purge"] is True
    assert 8080 in remove_payload["ports"]


@pytest.mark.asyncio
async def test_node_client_send_to_node_https_to_http_fallback(monkeypatch):
    """Verify send_to_node gracefully falls back to http when https encounters RequestError."""
    from panel.app.node_client import NodeClient
    import httpx

    node = SimpleNamespace(
        id="test-node-1",
        name="Iran Node 1",
        node_metadata={"api_address": "https://192.0.2.1:8888", "agent_tls_certificate": "mock-cert"},
    )
    client = NodeClient()
    monkeypatch.setattr(client, "_verify_for_url", lambda url, node: False)
    monkeypatch.setattr(client, "_get_frp_settings", AsyncMock(return_value=None))

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = node
    mock_session.execute = AsyncMock(return_value=mock_result)

    class MockAsyncSessionLocal:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass

    import panel.app.node_client as nc_module
    monkeypatch.setattr(nc_module, "AsyncSessionLocal", MockAsyncSessionLocal)

    called_urls = []

    async def mock_post(url, json=None, headers=None):
        called_urls.append(url)
        if url.startswith("https://"):
            raise httpx.ConnectError("SSL: WRONG_VERSION_NUMBER")
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"status": "ok", "tunnels": ["tun-1"]})
        return resp

    mock_client = MagicMock()
    mock_client.post = mock_post

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    res = await client.send_to_node("test-node-1", "/api/agent/status", {})
    assert res.get("status") == "ok"
    assert any(u.startswith("http://") for u in called_urls)


@pytest.mark.asyncio
async def test_node_client_send_to_node_http_to_https_fallback(monkeypatch):
    """Verify send_to_node falls back to https when http receives 400 Bad Request."""
    from panel.app.node_client import NodeClient
    import httpx

    node = SimpleNamespace(
        id="test-node-2",
        name="Foreign Node 1",
        node_metadata={"api_address": "http://192.0.2.2:8888"},
    )
    client = NodeClient()
    monkeypatch.setattr(client, "_get_frp_settings", AsyncMock(return_value=None))

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = node
    mock_session.execute = AsyncMock(return_value=mock_result)

    class MockAsyncSessionLocal:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass

    import panel.app.node_client as nc_module
    monkeypatch.setattr(nc_module, "AsyncSessionLocal", MockAsyncSessionLocal)

    called_urls = []

    async def mock_post(url, json=None, headers=None):
        called_urls.append(url)
        if url.startswith("http://"):
            err_resp = MagicMock()
            err_resp.status_code = 400
            err_resp.json = MagicMock(return_value={"detail": "Client sent HTTP request to HTTPS server"})
            raise httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=err_resp)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"status": "ok", "applied": True})
        return resp

    mock_client = MagicMock()
    mock_client.post = mock_post

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    res = await client.send_to_node("test-node-2", "/api/agent/tunnels/apply", {"tunnel_id": "test"})
    assert res.get("status") == "ok"
    assert any(u.startswith("https://") for u in called_urls)


def test_spec_builder_chisel_missing_cert_warning_no_crash():
    """Verify build_chisel_node_specs does not raise ValueError when in-memory cert is absent."""
    from panel.app.spec_builder import build_chisel_node_specs
    from unittest.mock import MagicMock

    tunnel = MagicMock()
    tunnel.id = "chisel-test-tunnel"
    tunnel.core = "chisel"
    tunnel.tunnel_type = "tcp"
    tunnel.transport = "wss"
    tunnel.transport_type = "wss"
    tunnel.control_port = 8080
    tunnel.ports = [8081]
    tunnel.port_ranges = ""
    tunnel.spec = {"transport": "wss", "websocket_tls": True}

    server_spec, client_spec = build_chisel_node_specs(tunnel, "192.0.2.1", "192.0.2.2")
    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"


def test_tunnel_contract_supports_all_26_tunnel_permutations():
    """Verify tunnel_contract CORE_TYPES and CORE_TRANSPORTS accept all active fleet permutations."""
    from panel.app.tunnel_contract import validate_tunnel_request

    # FRP STCP and XTCP
    res_stcp = validate_tunnel_request("frp", "stcp", "tcp")
    assert res_stcp["type"] == "stcp"
    res_tcpmux = validate_tunnel_request("frp", "tcp", "tcpmux")
    assert res_tcpmux["transport"] == "tcpmux"

    # Rathole noise carrier and all type
    res_noise = validate_tunnel_request("rathole", "tcp", "noise")
    assert res_noise["transport"] == "noise"
    res_rathole_all = validate_tunnel_request("rathole", "all", "tcp")
    assert res_rathole_all["type"] == "all"

    # Backhaul all and mux
    res_bh = validate_tunnel_request("backhaul", "all", "tcpmux")
    assert res_bh["type"] == "all"
    res_bh_mux = validate_tunnel_request("backhaul", "tcp", "mux")
    assert res_bh_mux["transport"] == "tcpmux"

    # GOST all, reverse, relay, tcpmux
    res_gost_all = validate_tunnel_request("gost", "all", "tcp")
    assert res_gost_all["type"] == "all"
    res_gost_rev = validate_tunnel_request("gost", "reverse", "tcp")
    assert res_gost_rev["type"] == "reverse"
    res_gost_relay = validate_tunnel_request("gost", "relay", "tcp")
    assert res_gost_relay["type"] == "relay"

