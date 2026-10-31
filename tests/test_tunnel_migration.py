"""Changing the Iran/foreign node or the type of an existing tunnel is staged, applied
atomically and rolled back on failure."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Node, Tunnel
from app.routers.tunnels import TunnelUpdate, update_tunnel, discard_pending_changes, get_pending_changes, apply_tunnel
from app.tunnel_ops import invalidate_derived_spec, tunnel_lock


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        for nid, role, ip in (
            ("ir-a", "iran", "10.0.0.1"),
            ("ir-b", "iran", "10.0.0.3"),
            ("fo-x", "foreign", "10.0.0.2"),
            ("fo-y", "foreign", "10.0.0.4"),
        ):
            session.add(Node(id=nid, name=nid, fingerprint=f"fp-{nid}", status="active",
                             node_metadata={"role": role, "ip_address": ip, "api_address": f"http://{ip}:8888"}))
        session.add(Tunnel(
            id="tun-1", name="t", core="gost", type="tcp", is_reverse=True,
            node_id="ir-a", iran_node_id="ir-a", foreign_node_id="fo-x",
            spec={"ports": [8080], "control_port": 30001, "token": "old-token", "tls_cert_pem": "old-cert", "latency_ms": 9},
            status="active",
        ))
        await session.commit()
        yield session
    await engine.dispose()


class FakeClient:
    """Records node calls; failing/offline nodes are configured per test."""
    fail_apply_on: set = set()
    offline: set = set()
    calls: list = []

    def __init__(self):
        pass

    async def send_to_node(self, node_id, endpoint, data=None):
        FakeClient.calls.append((node_id, endpoint, data))
        if endpoint.endswith("/tunnels/apply") and node_id in FakeClient.fail_apply_on:
            return {"status": "error", "message": f"boom on {node_id}"}
        return {"status": "success"}

    async def get_tunnel_status(self, node_id, tunnel_id=""):
        return {"status": "error"} if node_id in FakeClient.offline else {"status": "ok"}

    async def verify_tunnel_on_node(self, **kwargs):
        return {"data": {}}

    async def probe_ping(self, *args, **kwargs):
        return 5

    async def nodes_support(self, *args, **kwargs):
        return False


@pytest.fixture(autouse=True)
def fake_env():
    FakeClient.fail_apply_on = set()
    FakeClient.offline = set()
    FakeClient.calls = []
    seen = []

    def fake_builder(tunnel, iran_ip, foreign_ip):
        seen.append({"type": tunnel.type, "iran": tunnel.iran_node_id, "foreign": tunnel.foreign_node_id,
                     "node_id": tunnel.node_id, "spec": dict(tunnel.spec or {}), "iran_ip": iran_ip, "foreign_ip": foreign_ip})
        # like the real builders: regenerate what was dropped
        if tunnel.spec is not None and "token" not in tunnel.spec:
            tunnel.spec["token"] = "fresh-token"
        return {"mode": "server"}, {"mode": "client"}

    with patch("app.routers.tunnels.NodeClient", FakeClient), \
         patch("app.spec_builder.build_tunnel_node_specs", side_effect=fake_builder), \
         patch("app.tunnel_planning.prepare_tunnel_for_apply", new_callable=AsyncMock), \
         patch("asyncio.sleep", AsyncMock()):
        FakeClient.seen = seen
        yield


def _req():
    return MagicMock()


async def _tunnel(db):
    res = await db.execute(select(Tunnel).where(Tunnel.id == "tun-1"))
    t = res.scalar_one()
    await db.refresh(t)
    return t


async def test_node_change_is_staged_and_live_config_untouched(db):
    out = await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y", iran_node_id="ir-a"), _req(), db=db, current_user=MagicMock())
    assert out.foreign_node_id == "fo-x"           # live column unchanged
    assert out.pending_changes == {"foreign_node_id": "fo-y"}
    assert not out.spec.get("_pending_reapply")     # background sweeps must not pick it up
    assert FakeClient.calls == []                   # nothing was sent to any node


async def test_reselecting_the_live_node_clears_the_staged_change(db):
    await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y"), _req(), db=db, current_user=MagicMock())
    out = await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-x"), _req(), db=db, current_user=MagicMock())
    assert not out.pending_changes


@pytest.mark.parametrize("payload,status", [
    ({"foreign_node_id": "ir-b"}, 400),            # wrong role
    ({"iran_node_id": "fo-x"}, 400),               # wrong role / same as foreign
    ({"foreign_node_id": "nope"}, 404),            # unknown node
])
async def test_invalid_node_selection_is_rejected(db, payload, status):
    with pytest.raises(HTTPException) as exc:
        await update_tunnel("tun-1", TunnelUpdate(**payload), _req(), db=db, current_user=MagicMock())
    assert exc.value.status_code == status


async def test_apply_switches_foreign_node_rotates_secrets_and_cleans_up(db):
    out = await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y"), _req(), apply=True, db=db, current_user=MagicMock())
    assert out.foreign_node_id == "fo-y" and out.iran_node_id == "ir-a" and out.node_id == "ir-a"
    assert not out.pending_changes
    assert out.revision >= 2
    seen = FakeClient.seen[-1]
    assert seen["foreign"] == "fo-y" and seen["foreign_ip"] == "10.0.0.4"
    assert "old-token" not in str(seen["spec"].values()) and "old-cert" not in str(seen["spec"].values())
    assert out.spec["token"] == "fresh-token"      # old node no longer knows the live token
    applied_to = {c[0] for c in FakeClient.calls if c[1].endswith("/tunnels/apply")}
    assert applied_to == {"ir-a", "fo-y"}


async def test_iran_node_change_reissues_certificate_inputs(db):
    out = await update_tunnel("tun-1", TunnelUpdate(iran_node_id="ir-b"), _req(), apply=True, db=db, current_user=MagicMock())
    assert out.iran_node_id == "ir-b" and out.node_id == "ir-b"
    assert "tls_cert_pem" not in out.spec
    assert FakeClient.seen[-1]["iran_ip"] == "10.0.0.3"


async def test_failed_apply_restores_previous_configuration(db):
    FakeClient.fail_apply_on = {"fo-y"}
    with pytest.raises(HTTPException) as exc:
        await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y"), _req(), apply=True, db=db, current_user=MagicMock())
    assert exc.value.status_code == 409 and "restored" in exc.value.detail
    t = await _tunnel(db)
    assert (t.iran_node_id, t.foreign_node_id, t.node_id) == ("ir-a", "fo-x", "ir-a")
    assert t.spec["token"] == "old-token" and t.spec["tls_cert_pem"] == "old-cert"   # secrets restored
    assert t.pending_changes == {"foreign_node_id": "fo-y"}                          # user can fix and retry
    assert t.status == "active"
    # the previous nodes were configured again after the failure
    applied = [c[0] for c in FakeClient.calls if c[1].endswith("/tunnels/apply")]
    assert applied[-2:] == ["ir-a", "fo-x"]


async def test_unreachable_new_node_changes_nothing(db):
    FakeClient.offline = {"fo-y"}
    with pytest.raises(HTTPException) as exc:
        await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y"), _req(), apply=True, db=db, current_user=MagicMock())
    assert exc.value.status_code == 409
    t = await _tunnel(db)
    assert t.foreign_node_id == "fo-x" and t.spec["token"] == "old-token"
    assert FakeClient.calls == []


async def test_type_change_is_staged_then_applied(db):
    out = await update_tunnel("tun-1", TunnelUpdate(type="udp"), _req(), db=db, current_user=MagicMock())
    assert out.type == "tcp" and out.pending_changes == {"type": "udp"}
    assert FakeClient.calls == []
    res = await apply_tunnel("tun-1", _req(), db=db, current_user=MagicMock())
    assert res["status"] == "success"
    t = await _tunnel(db)
    assert t.type == "udp" and t.spec["type"] == "udp" and t.spec["tunnel_type"] == "udp"
    assert not t.pending_changes
    assert FakeClient.seen[-1]["type"] == "udp"


async def test_unsupported_type_is_rejected(db):
    with pytest.raises(HTTPException) as exc:
        await update_tunnel("tun-1", TunnelUpdate(type="socks5"), _req(), db=db, current_user=MagicMock())
    assert exc.value.status_code == 400


async def test_staged_type_is_checked_for_port_conflicts_on_the_target_nodes(db):
    db.add(Tunnel(id="tun-2", name="other", core="gost", type="udp", is_reverse=True, node_id="ir-a",
                  iran_node_id="ir-a", foreign_node_id="fo-y", spec={"ports": [8080], "control_port": 30002}, status="active"))
    await db.commit()
    # tcp+udp on 8080 would now collide with the UDP tunnel on the same Iran node
    with pytest.raises(HTTPException) as exc:
        await update_tunnel("tun-1", TunnelUpdate(type="tcp+udp"), _req(), db=db, current_user=MagicMock())
    assert exc.value.status_code == 400


async def test_discard_pending_leaves_tunnel_running(db):
    await update_tunnel("tun-1", TunnelUpdate(foreign_node_id="fo-y"), _req(), db=db, current_user=MagicMock())
    plan = await get_pending_changes("tun-1", db=db, current_user=MagicMock())
    assert plan["plan"]["nodes_changed"] and plan["plan"]["changes"][0]["to"] == "fo-y"
    assert (await discard_pending_changes("tun-1", db=db, current_user=MagicMock()))["status"] == "discarded"
    t = await _tunnel(db)
    assert not t.pending_changes and t.foreign_node_id == "fo-x"


async def test_background_reapply_skips_a_tunnel_being_applied(db):
    from app.tunnel_reapply_manager import TunnelReapplyManager
    t = await _tunnel(db)
    async with tunnel_lock("tun-1"):
        assert await TunnelReapplyManager()._reapply_tunnel_safe(t, db, FakeClient()) is False
    assert FakeClient.calls == []


def test_invalidate_derived_spec():
    spec = {"token": "t", "auth": "a", "tls_cert_pem": "c", "remote_addr": "x", "accept_udp": True,
            "latency_profile": "gaming-v1", "ports": [1], "control_port": 5}
    nodes = invalidate_derived_spec(spec, nodes_changed=True, type_changed=False)
    assert set(nodes) == {"accept_udp", "latency_profile", "ports", "control_port"}
    typed = invalidate_derived_spec(spec, nodes_changed=False, type_changed=True)
    assert set(typed) == {"token", "auth", "tls_cert_pem", "remote_addr", "ports", "control_port"}
    assert spec["token"] == "t"                      # input is not mutated
