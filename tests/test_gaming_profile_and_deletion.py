"""Regression tests for the low-latency (gaming/UDP) profile and robust tunnel deletion.

Covers:
- GOST UDP: channel keepalive in direct mode, long session TTL, large read buffers,
  KCP fast3 and small mux frames for latency-sensitive tunnels only
- FRP: tcp_mux option applied consistently (frps, frpc, visitor, provider) and the
  provider sending the auth scopes frps verifies (direct mode carried no traffic)
- spec builder: the gaming profile only changes specs that carry the marker
- panel planning: conflict-free control ports, capability-gated wire options
- deletion: de-configure first, no watchdog resurrection, tagged orphan cleanup,
  apply-time purge of leftovers holding the new tunnel's ports
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "node"))
sys.path.insert(0, str(repo_root / "panel"))

import node.app.core_adapters as ca
from node.app.core_adapters import AdapterManager, FrpAdapter, GostAdapter


def _proc(pid=4321):
    p = MagicMock()
    p.pid = pid
    p.returncode = None
    return p


async def _gost_config(tmp_path, monkeypatch, tunnel_id, spec):
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))
    monkeypatch.setattr(ca.asyncio, "sleep", AsyncMock())
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock, return_value=_proc()), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock):
        await adapter.apply(tunnel_id, spec)
    return json.loads((tmp_path / f"{tunnel_id}.json").read_text())


# ---------------------------------------------------------------------------
# GOST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gost_direct_udp_listener_keeps_channels_alive(tmp_path, monkeypatch):
    spec = {"mode": "client", "is_reverse": False, "server_ip": "1.2.3.4", "control_port": 30001,
            "ports": [27015], "type": "udp", "transport_type": "tcp"}
    cfg = await _gost_config(tmp_path, monkeypatch, "g-direct-udp", spec)
    udp = next(s for s in cfg["services"] if s["name"].startswith("udp-in-"))
    md = udp["listener"]["metadata"]
    assert md["keepalive"] is True, "without keepalive GOST closes the channel after one response"
    assert md["ttl"] == ca.UDP_SESSION_TTL
    assert md["readBufferSize"] == 4194304
    assert udp["handler"]["metadata"]["ttl"] == ca.UDP_SESSION_TTL


@pytest.mark.asyncio
async def test_gost_reverse_udp_session_ttl_and_small_mux_frames(tmp_path, monkeypatch):
    client_spec = {"mode": "client", "is_reverse": True, "server_ip": "5.6.7.8", "control_port": 30002,
                   "ports": [27015], "type": "udp", "transport_type": "tls", "security_type": "tls",
                   "gaming_mode": True, "multiplex": True}
    cfg = await _gost_config(tmp_path, monkeypatch, "g-rev-udp", client_spec)
    udp = next(s for s in cfg["services"] if s["name"].startswith("udp-in-"))
    assert udp["listener"]["type"] == "rudp"
    assert udp["listener"]["metadata"]["ttl"] == "45s"
    assert udp["handler"]["metadata"]["ttl"] == ca.UDP_SESSION_TTL
    node = cfg["chains"][0]["hops"][-1]["nodes"][0]
    assert node["connector"]["metadata"]["mux.maxFrameSize"] == 4096
    assert node["dialer"]["metadata"]["mux.maxFrameSize"] == 4096


@pytest.mark.asyncio
async def test_gost_kcp_fast3_only_for_latency_sensitive(tmp_path, monkeypatch):
    base = {"mode": "server", "is_reverse": True, "control_port": 30003, "ports": [443], "transport_type": "kcp"}
    udp_cfg = await _gost_config(tmp_path, monkeypatch, "g-kcp-udp", {**base, "type": "udp"})
    tcp_cfg = await _gost_config(tmp_path, monkeypatch, "g-kcp-tcp", {**base, "type": "tcp"})
    assert udp_cfg["services"][0]["listener"]["metadata"]["mode"] == "fast3"
    assert "mode" not in tcp_cfg["services"][0]["listener"]["metadata"]


@pytest.mark.asyncio
async def test_gost_plain_tcp_tunnel_config_unchanged(tmp_path, monkeypatch):
    """Latency tuning must not touch non-gaming TCP tunnels."""
    spec = {"mode": "client", "is_reverse": True, "server_ip": "5.6.7.8", "control_port": 30004,
            "ports": [443], "type": "tcp", "transport_type": "tcp"}
    cfg = await _gost_config(tmp_path, monkeypatch, "g-tcp", spec)
    node = cfg["chains"][0]["hops"][-1]["nodes"][0]
    assert "mux.maxFrameSize" not in node["connector"].get("metadata", {})


@pytest.mark.asyncio
async def test_spawned_core_processes_are_tagged(monkeypatch):
    captured = {}

    async def fake_exec(*cmd, **kwargs):
        captured.update(kwargs)
        return _proc()

    monkeypatch.setattr(ca.asyncio, "create_subprocess_exec", fake_exec)
    ca._set_port_owner("tun-tagged")
    await ca._spawn_core_subprocess(["/bin/gost", "-C", "x.json"], env={"RUST_LOG": "warn"})
    ca._set_port_owner(None)
    env = captured["env"]
    assert env[ca.TUNNEL_TAG_ENV] == "tun-tagged"
    assert env[ca.AGENT_TAG_ENV] == ca.AGENT_INSTANCE_ID
    assert env["RUST_LOG"] == "warn"


# ---------------------------------------------------------------------------
# FRP
# ---------------------------------------------------------------------------

async def _frp_apply(tmp_path, monkeypatch, tunnel_id, spec):
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/frpc"))
    monkeypatch.setattr(adapter, "_resolve_server_binary_path", lambda: Path("/bin/frps"))
    monkeypatch.setattr(ca.asyncio, "sleep", AsyncMock())
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock, return_value=_proc()), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        await adapter.apply(tunnel_id, spec)
    return adapter


@pytest.mark.asyncio
async def test_frp_provider_sends_auth_scopes_and_matches_tcp_mux(tmp_path, monkeypatch):
    spec = {"mode": "server", "is_provider": True, "is_reverse": False, "bind_port": 7100, "token": "tok",
            "secret_key": "sk", "ports": [27015], "type": "udp", "tcp_mux": False}
    await _frp_apply(tmp_path, monkeypatch, "f-direct", spec)
    frps = (tmp_path / "frps_f-direct.yaml").read_text()
    provider = (tmp_path / "frpc_f-direct_provider.yaml").read_text()
    assert "tcpMux: false" in frps
    assert "tcpMux: false" in provider
    assert "additionalScopes" in frps
    assert "- HeartBeats" in provider and "- NewWorkConns" in provider


@pytest.mark.asyncio
async def test_frp_client_tcp_mux_default_and_disabled(tmp_path, monkeypatch):
    base = {"mode": "client", "server_addr": "1.2.3.4", "server_port": 7100, "token": "tok",
            "ports": [{"local": 27015, "remote": 27015}], "type": "udp", "security_type": "tls"}
    await _frp_apply(tmp_path, monkeypatch, "f-default", base)
    assert "tcpMux: true" in (tmp_path / "frpc_f-default.yaml").read_text()
    await _frp_apply(tmp_path, monkeypatch, "f-nomux", {**base, "tcp_mux": False})
    assert "tcpMux: false" in (tmp_path / "frpc_f-nomux.yaml").read_text()


# ---------------------------------------------------------------------------
# Spec builder gaming profile
# ---------------------------------------------------------------------------

def _tunnel(core, ttype, spec=None, **attrs):
    t = SimpleNamespace(id=f"tid-{core}-{ttype}", core=core, type=ttype, spec=dict(spec or {}),
                        is_reverse=attrs.pop("is_reverse", True), gaming_mode=attrs.pop("gaming_mode", False),
                        transport_type=attrs.pop("transport_type", None), security_type=None,
                        custom_sni=None, stealth_domain=None, port_ranges=None, foreign_node_id="f", iran_node_id="i")
    for k, v in attrs.items():
        setattr(t, k, v)
    return t


def test_builder_without_marker_is_unchanged():
    from app.spec_builder import build_tunnel_node_specs
    for core in ("rathole", "backhaul", "chisel", "frp", "gost"):
        t = _tunnel(core, "udp", {"ports": [27015], "control_port": 30010, "token": "x" * 16})
        iran_spec, foreign_spec = build_tunnel_node_specs(t, "1.1.1.1", "2.2.2.2")
        assert "latency_profile" not in iran_spec and "latency_profile" not in foreign_spec, core


def test_builder_gaming_profile_effects():
    from app.spec_builder import build_tunnel_node_specs
    marker = {"latency_profile": "gaming-v1", "ports": [27015], "control_port": 30011, "token": "x" * 16}

    bh_server, bh_client = build_tunnel_node_specs(_tunnel("backhaul", "udp", marker, transport_type="tcp"), "1.1.1.1", "2.2.2.2")
    assert bh_server["gaming_mode"] is True and bh_client["client_options"]["aggressive_pool"] is True
    assert bh_server["server_options"]["keepalive_period"] == 12

    ch_iran, ch_foreign = build_tunnel_node_specs(_tunnel("chisel", "udp", marker), "1.1.1.1", "2.2.2.2")
    client = ch_foreign if ch_foreign["mode"] == "client" else ch_iran
    assert client["max_retry_interval"] == "3s"
    assert client["keepalive"] == "10s"

    g_iran, g_foreign = build_tunnel_node_specs(_tunnel("gost", "udp", marker), "1.1.1.1", "2.2.2.2")
    assert g_iran["latency_profile"] == "gaming-v1" and g_foreign["latency_profile"] == "gaming-v1"


# ---------------------------------------------------------------------------
# Panel planning
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return SimpleNamespace(all=lambda: self._items)


@pytest.mark.asyncio
async def test_control_port_allocation_avoids_used_ports():
    from app import tunnel_planning
    import hashlib

    new = SimpleNamespace(id="new-tunnel", core="frp", spec={"ports": [27015]})
    start = 7000 + int(hashlib.sha256(b"new-tunnel").hexdigest()[:8], 16) % 1000
    taken = SimpleNamespace(id="other", spec={"bind_port": start, "ports": [start + 1]})
    db = AsyncMock()
    db.execute.return_value = _FakeResult([taken])
    with patch("app.tunnel_planning.flag_modified"):
        port = await tunnel_planning.ensure_conflict_free_control_port(db, new, ["iran", "foreign"])
    assert port not in (start, start + 1)
    assert new.spec["bind_port"] == port == new.spec["control_port"]


@pytest.mark.asyncio
async def test_control_port_allocation_keeps_existing_ports():
    from app import tunnel_planning
    t = SimpleNamespace(id="t", core="gost", spec={"control_port": 31000})
    db = AsyncMock()
    assert await tunnel_planning.ensure_conflict_free_control_port(db, t, ["n"]) is None
    db.execute.assert_not_called()
    assert t.spec["control_port"] == 31000


@pytest.mark.asyncio
async def test_frp_tcp_mux_only_when_both_nodes_support_it():
    from app import tunnel_planning
    with patch("app.tunnel_planning.flag_modified"):
        t = SimpleNamespace(id="t", core="frp", type="udp", gaming_mode=True, spec={})
        client = MagicMock()
        client.nodes_support = AsyncMock(return_value=False)
        await tunnel_planning.apply_latency_profile(t, client, ["a", "b"])
        assert t.spec["latency_profile"] == "gaming-v1" and "tcp_mux" not in t.spec

        t2 = SimpleNamespace(id="t2", core="frp", type="udp", gaming_mode=True, spec={})
        client.nodes_support = AsyncMock(return_value=True)
        await tunnel_planning.apply_latency_profile(t2, client, ["a", "b"])
        assert t2.spec["tcp_mux"] is False

        tcp = SimpleNamespace(id="t3", core="frp", type="tcp", gaming_mode=False, spec={})
        assert await tunnel_planning.apply_latency_profile(tcp, client, ["a", "b"]) is False
        assert tcp.spec == {}

        # Tunnels without gaming_mode should have any stale latency_profile cleared
        stale = SimpleNamespace(id="t4", core="frp", type="udp", gaming_mode=False, spec={"latency_profile": "gaming-v1"})
        assert await tunnel_planning.apply_latency_profile(stale, client, ["a", "b"]) is True
        assert "latency_profile" not in stale.spec

        # Test dictionary support for is_latency_sensitive
        assert tunnel_planning.is_latency_sensitive({"gaming_mode": True}) is True
        assert tunnel_planning.is_latency_sensitive({"spec": {"gaming_mode": True}}) is True
        assert tunnel_planning.is_latency_sensitive({"type": "udp", "gaming_mode": False}) is False


@pytest.mark.asyncio
async def test_apply_payload_carries_authoritative_ids():
    from app import node_client as nc
    node = SimpleNamespace(id="n1", node_metadata={"role": "foreign", "api_address": "http://9.9.9.9:8888"})
    sess = AsyncMock()
    sess.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=node)),
        _FakeResult(["t1", "t2"]),
    ]
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=sess)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    sent = {}

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent["json"] = json
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"status": "success"}
            return resp

    with patch.object(nc, "AsyncSessionLocal", return_value=session_cm), patch.object(nc.httpx, "AsyncClient", FakeHttp):
        await nc.NodeClient().send_to_node("n1", "/api/agent/tunnels/apply", {"tunnel_id": "t1", "spec": {"latency_ms": 5}})
    assert sent["json"]["authoritative_ids"] == ["t1", "t2"]
    assert "latency_ms" not in sent["json"]["spec"]


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def _manager(tmp_path, monkeypatch) -> AdapterManager:
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path / "pids")
    (tmp_path / "pids").mkdir(exist_ok=True)
    m = AdapterManager()
    m.config_dir = tmp_path
    m.tunnels_file = tmp_path / "tunnels.json"
    for adapter in m.adapters.values():
        adapter.config_dir = tmp_path / adapter.name
        adapter.config_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(ca, "_find_tagged_core_processes", lambda tunnel_id=None: {})
    monkeypatch.setattr(ca, "safe_stop_subprocess", AsyncMock())
    monkeypatch.setattr(ca, "free_ports", AsyncMock())
    monkeypatch.setattr(ca, "_find_pids_by_port_procfs", lambda port, listen_only=True: set())
    return m


@pytest.mark.asyncio
async def test_remove_deconfigures_before_stopping(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    seen = {}
    adapter = MagicMock()

    async def remove(tid, purge=False):
        seen["configured_during_kill"] = tid in m.tunnel_configs
        seen["persisted"] = json.loads(m.tunnels_file.read_text())

    adapter.remove = remove
    m.adapters["gost"] = adapter
    m.tunnel_configs["t-del"] = {"core": "gost", "spec": {"ports": [443], "control_port": 30020}}
    m.active_tunnels["t-del"] = adapter
    result = await m.remove_tunnel("t-del", purge=True)
    assert seen["configured_during_kill"] is False
    assert "t-del" not in seen["persisted"]
    assert result["found"] is True and result["stuck_ports"] == []


@pytest.mark.asyncio
async def test_remove_kills_tagged_orphans(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(ca, "_find_tagged_core_processes", lambda tunnel_id=None: {777: "t-orphan"} if tunnel_id in (None, "t-orphan") else {})
    killed = []
    monkeypatch.setattr(ca, "_kill_pids", lambda pids, timeout=3.0: killed.extend(pids))
    result = await m.remove_tunnel("t-orphan", purge=True)
    assert killed == [777]
    assert result["killed_pids"] == [777]


@pytest.mark.asyncio
async def test_watchdog_never_resurrects_removed_tunnel(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    adapter = MagicMock()
    adapter.status.return_value = {"process_running": False}
    adapter.apply = AsyncMock()
    adapter.remove = AsyncMock()
    m.adapters["gost"] = adapter
    m.tunnel_configs["t-race"] = {"core": "gost", "spec": {"ports": [443]}}

    lock = m._get_tunnel_lock("t-race")
    await lock.acquire()  # removal in progress holds the lock

    sleeps = {"n": 0}
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        # Only the watchdog's 15s interval sleep counts; stop on its second round
        if delay >= 15:
            sleeps["n"] += 1
            if sleeps["n"] > 1:
                raise asyncio.CancelledError()
        await real_sleep(0)

    monkeypatch.setattr(ca.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(m, "_rotate_oversized_logs", lambda: None)
    watchdog = asyncio.ensure_future(m._watchdog_loop())
    for _ in range(200):
        await real_sleep(0)
        if getattr(lock, "_waiters", None):
            break
    assert lock._waiters, "watchdog must be blocked on the tunnel lock for this race"
    # the removal finishes while the watchdog waits for the lock
    del m.tunnel_configs["t-race"]
    lock.release()
    try:
        await asyncio.wait_for(watchdog, timeout=2)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    adapter.apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_purges_unknown_tunnel_holding_its_ports(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    adapter = MagicMock()
    adapter.apply = AsyncMock()
    adapter.remove = AsyncMock()
    adapter.status.return_value = {"process_running": False}
    m.adapters["gost"] = adapter
    # leftover of a deleted tunnel (removal never reached this node) on port 443
    m.tunnel_configs["old-deleted"] = {"core": "gost", "spec": {"mode": "server", "ports": [443], "control_port": 30030}}
    # unrelated live tunnel the panel still knows about
    m.tunnel_configs["keep-me"] = {"core": "gost", "spec": {"mode": "server", "ports": [443], "control_port": 30031}}
    # unknown leftover on a different port: left for normal reconciliation
    m.tunnel_configs["other-orphan"] = {"core": "gost", "spec": {"mode": "server", "ports": [9999], "control_port": 30032}}

    await m.apply_tunnel("new-tunnel", "gost", {"mode": "server", "ports": [443], "control_port": 30033},
                         authoritative_ids=["new-tunnel", "keep-me"])
    assert "old-deleted" not in m.tunnel_configs
    assert "keep-me" in m.tunnel_configs
    assert "other-orphan" in m.tunnel_configs
    assert "new-tunnel" in m.tunnel_configs


@pytest.mark.asyncio
async def test_lock_kept_while_someone_waits(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    lock = m._get_tunnel_lock("t-lock")
    await lock.acquire()
    waiter = asyncio.ensure_future(m.remove_tunnel("t-lock"))
    await asyncio.sleep(0)
    lock.release()
    await waiter
    # nobody waits anymore -> dropped; while waited on it must have stayed the same object
    assert "t-lock" not in m._tunnel_locks


def test_agent_remove_endpoint_reports_result(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import node.app.agent_auth as agent_auth
    from node.app.routers import agent as agent_router

    monkeypatch.setattr(agent_auth, "TOKEN_FILE", tmp_path / "agent_token")
    monkeypatch.setattr(agent_auth, "_file_token", None)
    monkeypatch.setattr(agent_auth, "_loaded", False)
    monkeypatch.setattr(agent_auth.settings, "node_api_token", "")
    app = FastAPI()
    app.include_router(agent_router.router, prefix="/api/agent")
    manager = MagicMock()
    manager.remove_tunnel = AsyncMock(return_value={"found": True, "killed_pids": [55], "stuck_ports": [443]})
    manager.apply_tunnel = AsyncMock()
    app.state.adapter_manager = manager
    client = TestClient(app)

    data = client.post("/api/agent/tunnels/remove", json={"tunnel_id": "t1", "purge": True}).json()
    assert data["killed_pids"] == [55] and data["stuck_ports"] == [443]

    client.post("/api/agent/tunnels/apply", json={"tunnel_id": "t1", "core": "gost", "type": "udp", "spec": {},
                                                  "authoritative_ids": ["t1", "../evil", "t2"]})
    kwargs = manager.apply_tunnel.await_args.kwargs
    assert kwargs["authoritative_ids"] == ["t1", "t2"]
    assert "capabilities" in client.get("/api/agent/status").json()


@pytest.mark.asyncio
async def test_stray_sweep_kills_orphans_and_duplicates_after_two_rounds(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    m._configs_trusted = True
    m.tunnel_configs["live"] = {"core": "gost", "spec": {}}
    (tmp_path / "pids" / "live.pid").write_text("100")
    monkeypatch.setattr(ca, "_find_tagged_core_processes",
                        lambda tunnel_id=None: {100: "live", 101: "live", 200: "deleted-tunnel"})
    killed = []
    monkeypatch.setattr(ca, "_kill_pids", lambda pids, timeout=3.0: killed.extend(pids))

    assert await m._sweep_stray_processes() == []          # first sighting: only a strike
    assert sorted(await m._sweep_stray_processes()) == [101, 200]
    assert sorted(killed) == [101, 200]                       # recorded PID 100 untouched


@pytest.mark.asyncio
async def test_stray_sweep_is_safe(tmp_path, monkeypatch):
    m = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(ca, "_find_tagged_core_processes", lambda tunnel_id=None: {300: "being-applied", 400: "x"})
    killed = []
    monkeypatch.setattr(ca, "_kill_pids", lambda pids, timeout=3.0: killed.extend(pids))

    # tunnels.json could not be trusted -> never kill anything
    m._configs_trusted = False
    for _ in range(3):
        await m._sweep_stray_processes()
    assert killed == []

    # a tunnel currently being applied (lock held) is never touched
    m._configs_trusted = True
    lock = m._get_tunnel_lock("being-applied")
    await lock.acquire()
    for _ in range(3):
        await m._sweep_stray_processes()
    lock.release()
    assert killed == [400]


@pytest.mark.asyncio
async def test_apply_purge_skips_tunnel_with_operation_in_flight(tmp_path, monkeypatch):
    """Two tunnels created concurrently: neither may remove the other."""
    m = _manager(tmp_path, monkeypatch)
    adapter = MagicMock()
    adapter.apply = AsyncMock()
    adapter.remove = AsyncMock()
    adapter.status.return_value = {"process_running": False}
    m.adapters["gost"] = adapter
    m.tunnel_configs["concurrent"] = {"core": "gost", "spec": {"mode": "server", "ports": [443], "control_port": 30040}}
    lock = m._get_tunnel_lock("concurrent")
    await lock.acquire()
    try:
        await m.apply_tunnel("new-one", "gost", {"mode": "server", "ports": [443], "control_port": 30041},
                             authoritative_ids=["new-one"])
    finally:
        lock.release()
    assert "concurrent" in m.tunnel_configs
