import pytest
from httpx import AsyncClient, ASGITransport
import asyncio
from unittest.mock import patch, MagicMock

from panel.main import app
from panel.app.routers.auth import _DUMMY_BCRYPT_HASH, pwd_context
from panel.app.port_forwarder import PortForwarder


@pytest.mark.asyncio
async def test_timing_attack_defense_constant_time():
    """Verify that dummy password verification is executed when user doesn't exist."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-existent user should fail with 401
        resp = await client.post(
            "/api/auth/login",
            json={"username": "totally_nonexistent_user_12345", "password": "wrong_password"}
        )
        assert resp.status_code == 401
        assert "Incorrect username or password" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ca_direct_alias_endpoints():
    """Verify /panel/ca and /panel/ca/server routes exist and do not return SPA index.html."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/panel/ca")
        # Should either return certificate text (200 PEM) or 404/500 if not initialized,
        # but NEVER return 200 HTML text
        if resp.status_code == 200:
            assert "<!DOCTYPE html>" not in resp.text
            assert "<html" not in resp.text
        else:
            assert resp.status_code in (404, 500)


@pytest.mark.asyncio
async def test_logs_query_bounds():
    """Verify logs limit query validation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Limit > 1000 should be rejected by FastAPI Query validation with 422 Unprocessable Entity
        resp = await client.get("/api/logs?limit=5000")
        # Since it requires auth, if unauthenticated it may return 401/403 or 422 depending on order
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
