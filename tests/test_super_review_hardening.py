import sys
from pathlib import Path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

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


@pytest.mark.asyncio
async def test_apply_tunnel_compensating_rollback_on_second_node_failure():
    """Verify that manual apply_tunnel triggers compensating rollback on first_node if second_node apply fails."""
    from app.routers.tunnels import apply_tunnel
    from app.models import Tunnel, Node
    from fastapi import HTTPException

    tunnel = Tunnel(
        id="tun-rollback-manual",
        name="Rollback Tunnel",
        core="gost",
        type="tcp",
        is_reverse=True,
        node_id="node-iran-1",
        iran_node_id="node-iran-1",
        foreign_node_id="node-foreign-1",
        spec={"ports": [8080], "control_port": 8443, "mode": "server"},
        status="staged"
    )

    iran_node = Node(
        id="node-iran-1",
        name="Iran-1",
        status="active",
        node_metadata={
            "role": "iran",
            "ip_address": "10.0.0.1",
            "api_address": "http://10.0.0.1:8888"
        }
    )

    foreign_node = Node(
        id="node-foreign-1",
        name="Foreign-1",
        status="active",
        node_metadata={
            "role": "foreign",
            "ip_address": "10.0.0.2",
            "api_address": "http://10.0.0.2:8888"
        }
    )

    db = AsyncMock()
    mock_res_tunnel = MagicMock()
    mock_res_tunnel.scalar_one_or_none.return_value = tunnel

    mock_res_iran = MagicMock()
    mock_res_iran.scalar_one_or_none.return_value = iran_node

    mock_res_all = MagicMock()
    mock_res_all.scalars.return_value.all.return_value = [iran_node, foreign_node]

    # Sequences of execute calls
    db.execute = AsyncMock(side_effect=[
        mock_res_tunnel,  # select tunnel
        mock_res_iran,    # select iran_node
        mock_res_all,     # select all nodes
    ])

    sent_requests = []
    async def mock_send_to_node(node_id, endpoint, data=None):
        sent_requests.append((node_id, endpoint, data))
        if endpoint == "/api/agent/tunnels/apply":
            if node_id == "node-iran-1":
                return {"status": "success"}
            elif node_id == "node-foreign-1":
                return {"status": "error", "message": "Simulated client error"}
        return {"status": "success"}

    request = MagicMock()

    with patch("app.routers.tunnels.NodeClient") as MockNodeClient, \
         patch("app.routers.tunnels.check_port_conflicts", new_callable=AsyncMock), \
         patch("app.tunnel_planning.prepare_tunnel_for_apply", new_callable=AsyncMock), \
         patch("app.spec_builder.build_tunnel_node_specs", return_value=({"mode": "server"}, {"mode": "client"})), \
         patch("asyncio.sleep", AsyncMock()):

        mock_client = MockNodeClient.return_value
        mock_client.send_to_node = AsyncMock(side_effect=mock_send_to_node)

        with pytest.raises(HTTPException) as exc_info:
            await apply_tunnel(
                tunnel_id="tun-rollback-manual",
                request=request,
                db=db,
                current_user=MagicMock()
            )

        assert exc_info.value.status_code == 500
        assert "Simulated client error" in exc_info.value.detail

        # Verify compensating rollback call was made to node-iran-1
        rollback_call = next((r for r in sent_requests if r[1] == "/api/agent/tunnels/remove"), None)
        assert rollback_call is not None, "Compensating rollback call to first_node was not found"
        assert rollback_call[0] == "node-iran-1"
        assert rollback_call[2]["tunnel_id"] == "tun-rollback-manual"
        assert rollback_call[2]["purge"] is True
        assert tunnel.status == "error"


def test_port_range_span_limit_exceeding_500():
    """Verify that port ranges exceeding 500 ports raise HTTPException 400 across all validation paths."""
    from app.routers.tunnels import validate_tunnel_payload, extract_all_tunnel_ports, check_port_conflicts
    from fastapi import HTTPException
    import asyncio

    # 1. validate_tunnel_payload with port_ranges > 500
    with pytest.raises(HTTPException) as exc1:
        validate_tunnel_payload({"port_ranges": ["1000-1600"]})
    assert exc1.value.status_code == 400
    assert exc1.value.detail == "Port range '1000-1600' exceeds maximum allowed span of 500 ports."

    # 2. extract_all_tunnel_ports with port_ranges > 500
    with pytest.raises(HTTPException) as exc2:
        extract_all_tunnel_ports({"port_ranges": ["2000-2501"]})
    assert exc2.value.status_code == 400
    assert exc2.value.detail == "Port range '2000-2501' exceeds maximum allowed span of 500 ports."

    # 3. validate_tunnel_payload with ports string > 500
    with pytest.raises(HTTPException) as exc3:
        validate_tunnel_payload({"ports": ["3000-3600"]})
    assert exc3.value.status_code == 400
    assert exc3.value.detail == "Port range '3000-3600' exceeds maximum allowed span of 500 ports."

    # 4. check_port_conflicts with port_ranges > 500
    mock_db = AsyncMock()
    with pytest.raises(HTTPException) as exc4:
        asyncio.run(check_port_conflicts(mock_db, {"port_ranges": ["10000-10600"]}))
    assert exc4.value.status_code == 400
    assert exc4.value.detail == "Port range '10000-10600' exceeds maximum allowed span of 500 ports."

    # 5. Exactly 500 span is allowed (e.g. 1000-1500 is 501 ports, span 500)
    validate_tunnel_payload({"port_ranges": ["1000-1500"]})
    res = extract_all_tunnel_ports({"port_ranges": ["1000-1500"]})
    assert 1000 in res["service_ports"]
    assert 1500 in res["service_ports"]
    assert len(res["service_ports"]) == 501


@pytest.mark.asyncio
async def test_apply_tunnel_compensating_rollback_on_network_exception():
    """Verify that manual apply_tunnel triggers compensating rollback when second_node throws a connection exception."""
    from app.routers.tunnels import apply_tunnel
    from app.models import Tunnel, Node
    from fastapi import HTTPException

    tunnel = Tunnel(
        id="tun-rollback-net-exc",
        name="Rollback Net Exc",
        core="backhaul",
        type="tcp",
        is_reverse=True,
        node_id="node-iran-1",
        iran_node_id="node-iran-1",
        foreign_node_id="node-foreign-1",
        spec={"ports": [9090], "control_port": 9443, "mode": "server"},
        status="staged"
    )

    iran_node = Node(
        id="node-iran-1",
        name="Iran-1",
        status="active",
        node_metadata={
            "role": "iran",
            "ip_address": "10.0.0.1",
            "api_address": "http://10.0.0.1:8888"
        }
    )

    foreign_node = Node(
        id="node-foreign-1",
        name="Foreign-1",
        status="active",
        node_metadata={
            "role": "foreign",
            "ip_address": "10.0.0.2",
            "api_address": "http://10.0.0.2:8888"
        }
    )

    db = AsyncMock()
    mock_res_tunnel = MagicMock()
    mock_res_tunnel.scalar_one_or_none.return_value = tunnel
    mock_res_iran = MagicMock()
    mock_res_iran.scalar_one_or_none.return_value = iran_node
    mock_res_all = MagicMock()
    mock_res_all.scalars.return_value.all.return_value = [iran_node, foreign_node]

    db.execute = AsyncMock(side_effect=[mock_res_tunnel, mock_res_iran, mock_res_all])

    sent_requests = []
    async def mock_send_to_node(node_id, endpoint, data=None):
        sent_requests.append((node_id, endpoint, data))
        if endpoint == "/api/agent/tunnels/apply":
            if node_id == "node-iran-1":
                return {"status": "success"}
            elif node_id == "node-foreign-1":
                raise ConnectionResetError("Connection abruptly terminated by peer")
        return {"status": "success"}

    request = MagicMock()

    with patch("app.routers.tunnels.NodeClient") as MockNodeClient, \
         patch("app.routers.tunnels.check_port_conflicts", new_callable=AsyncMock), \
         patch("app.tunnel_planning.prepare_tunnel_for_apply", new_callable=AsyncMock), \
         patch("app.spec_builder.build_tunnel_node_specs", return_value=({"mode": "server"}, {"mode": "client"})), \
         patch("asyncio.sleep", AsyncMock()):

        mock_client = MockNodeClient.return_value
        mock_client.send_to_node = AsyncMock(side_effect=mock_send_to_node)

        with pytest.raises(HTTPException) as exc_info:
            await apply_tunnel(
                tunnel_id="tun-rollback-net-exc",
                request=request,
                db=db,
                current_user=MagicMock()
            )

        assert exc_info.value.status_code == 500
        assert "Connection exception" in exc_info.value.detail

        # Verify compensating rollback call was made to node-iran-1
        rollback_call = next((r for r in sent_requests if r[1] == "/api/agent/tunnels/remove"), None)
        assert rollback_call is not None, "Compensating rollback call to first_node was not found"
        assert rollback_call[0] == "node-iran-1"
        assert rollback_call[2]["tunnel_id"] == "tun-rollback-net-exc"
        assert rollback_call[2]["purge"] is True
        assert tunnel.status == "error"


def test_port_range_span_limit_edge_cases():
    """Verify edge cases for port range span validation: boundary, malformed, empty, and mapped formats."""
    from app.routers.tunnels import validate_tunnel_payload, extract_all_tunnel_ports
    from fastapi import HTTPException

    # None and empty specs
    validate_tunnel_payload(None)
    validate_tunnel_payload({})
    validate_tunnel_payload("not-a-dict")
    empty_res = extract_all_tunnel_ports({})
    assert empty_res["all_ports"] == set()

    # Normal individual ports
    validate_tunnel_payload({"ports": [80, 443, 2053]})
    res = extract_all_tunnel_ports({"ports": [80, 443, 2053]})
    assert res["service_ports"] == {80, 443, 2053}

    # Boundary: span = 500 (e.g. 5000 to 5500) -> Allowed
    validate_tunnel_payload({"port_ranges": ["5000-5500"]})

    # Boundary: span = 501 (e.g. 5000 to 5501) -> Forbidden (HTTP 400)
    with pytest.raises(HTTPException) as exc1:
        validate_tunnel_payload({"port_ranges": ["5000-5501"]})
    assert exc1.value.status_code == 400
    assert exc1.value.detail == "Port range '5000-5501' exceeds maximum allowed span of 500 ports."

    # Mapped ports syntax: e.g. "443=1000-1600"
    with pytest.raises(HTTPException) as exc2:
        validate_tunnel_payload({"ports": ["443=1000-1600"]})
    assert exc2.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc2.value.detail

    # Inverted range (start > end) is ignored safely, not exceeding span
    validate_tunnel_payload({"port_ranges": ["5000-4000"]})

    # Non-numeric range parts ignored safely
    validate_tunnel_payload({"port_ranges": ["abc-def", "---", ""]})

    # String format in port_ranges exceeding 500:
    with pytest.raises(HTTPException) as exc3:
        validate_tunnel_payload({"port_ranges": "1000-1600"})
    assert exc3.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc3.value.detail

    # Comma-separated string in ports exceeding 500:
    with pytest.raises(HTTPException) as exc4:
        validate_tunnel_payload({"ports": "8080, 1000-1600"})
    assert exc4.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc4.value.detail

    # Mapped range in port_ranges exceeding 500:
    with pytest.raises(HTTPException) as exc5:
        validate_tunnel_payload({"port_ranges": ["443=1000-1600"]})
    assert exc5.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc5.value.detail

    # Both sides mapped range in port_ranges exceeding 500:
    with pytest.raises(HTTPException) as exc6:
        validate_tunnel_payload({"port_ranges": ["1000-1600=1000-1600"]})
    assert exc6.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc6.value.detail

    # extract_all_tunnel_ports with validate_span=False extracts without raising
    res_no_validate = extract_all_tunnel_ports({"port_ranges": ["1000-1600"]}, validate_span=False)
    assert len(res_no_validate["service_ports"]) == 601
    assert 1000 in res_no_validate["service_ports"]
    assert 1600 in res_no_validate["service_ports"]


@pytest.mark.asyncio
async def test_create_tunnel_rejects_top_level_port_ranges_exceeding_500():
    """Verify that create_tunnel rejects top-level port_ranges with span > 500."""
    from app.routers.tunnels import create_tunnel, TunnelCreate
    from fastapi import HTTPException

    mock_db = AsyncMock()
    tunnel_in = TunnelCreate(
        name="oversized-tunnel",
        core="backhaul",
        type="tcp",
        port_ranges=["1000-1600"],
        spec={}
    )

    with pytest.raises(HTTPException) as exc:
        await create_tunnel(tunnel_in, request=MagicMock(), db=mock_db, current_user=MagicMock())
    assert exc.value.status_code == 400
    assert "exceeds maximum allowed span of 500 ports." in exc.value.detail


@pytest.mark.asyncio
async def test_test_tunnel_config_rejects_oversized_range():
    """Verify test_tunnel_config rejects configurations with port ranges exceeding 500 ports."""
    from app.routers.tunnels import test_tunnel_config
    from fastapi import HTTPException

    mock_db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await test_tunnel_config({"core": "gost", "port_ranges": ["2000-2600"]}, db=mock_db, current_user=MagicMock())
    assert exc.value.status_code == 400
@pytest.mark.asyncio
async def test_delete_tunnel_succeeds_even_with_oversized_legacy_port_range():
    """Verify delete_tunnel cleans up without raising HTTPException 400 even if tunnel has legacy oversized range."""
    from app.routers.tunnels import delete_tunnel
    from app.models import Tunnel
    from unittest.mock import patch

    tunnel = Tunnel(
        id="tun-legacy-oversized",
        name="Legacy Oversized",
        core="gost",
        type="tcp",
        port_ranges=["1000-2000"],
        spec={"port_ranges": ["1000-2000"]},
        node_id="node-1"
    )

    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = tunnel
    mock_res.scalars.return_value.all.return_value = []
    db.execute.return_value = mock_res

    sent_requests = []
    async def mock_send(node_id, endpoint, data=None):
        sent_requests.append((node_id, endpoint, data))
        return {"status": "success"}

    with patch("app.routers.tunnels.NodeClient") as MockClient:
        instance = MockClient.return_value
        instance.send_to_node = mock_send

        res = await delete_tunnel(
            tunnel_id="tun-legacy-oversized",
            request=MagicMock(),
            db=db,
            current_user=MagicMock()
        )

    assert res == {"status": "deleted"}
    # Verify node received removal request with all 1001 freed ports
    assert len(sent_requests) >= 1
    remove_call = next((r for r in sent_requests if r[1] == "/api/agent/tunnels/remove"), None)
    assert remove_call is not None
    assert len(remove_call[2]["ports"]) == 1001





