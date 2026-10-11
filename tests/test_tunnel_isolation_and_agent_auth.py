"""Regression tests for cross-tunnel isolation, zero-downtime apply and agent API auth.

These guard the fixes for:
- port freeing that killed processes of *other* tunnels (and `ss -K` dropping
  unrelated live connections, even SSH)
- volatile spec keys (latency_ms, force_restart, _pending_reapply) restarting tunnels
- removal requests for tunnels a node never had (fleet-wide "ghost purge")
- PID reuse making a dead tunnel look alive / killing an unrelated process
- unauthenticated node agent API
"""
import asyncio
import json
import os
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
from node.app.core_adapters import AdapterManager, ChiselAdapter


# ---------------------------------------------------------------------------
# Port freeing: listening sockets only, never another tunnel's process
# ---------------------------------------------------------------------------

def _write_proc_net(root: Path, tcp_lines, udp_lines=()):
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (root / "tcp").write_text(header + "".join(tcp_lines))
    (root / "tcp6").write_text(header)
    (root / "udp").write_text(header + "".join(udp_lines))
    (root / "udp6").write_text(header)


def _line(local, remote, state, inode):
    return f"   0: {local} {remote} {state} 00000000:00000000 00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0\n"


def test_socket_inodes_only_listening_tcp_and_unconnected_udp(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "PROC_NET_ROOT", str(tmp_path))
    port_hex = f"{443:04X}"
    _write_proc_net(
        tmp_path,
        tcp_lines=[
            _line(f"00000000:{port_hex}", "00000000:0000", "0A", "1001"),   # LISTEN on 443 -> match
            _line(f"0100007F:{port_hex}", "0A000001:C350", "01", "1002"),   # ESTABLISHED, local port 443 -> ignore
            _line("0100007F:9C40", f"0A000001:{port_hex}", "01", "1003"),   # outbound to :443 -> ignore
        ],
        udp_lines=[
            _line(f"00000000:{port_hex}", "00000000:0000", "07", "2001"),   # unconnected UDP -> match
            _line(f"00000000:{port_hex}", "0A000001:1F90", "01", "2002"),   # connected UDP -> ignore
        ],
    )
    assert ca._socket_inodes_for_port(443, listen_only=True) == {"1001", "2001"}
    assert ca._socket_inodes_for_port(443, listen_only=False) == {"1001", "1002", "2001", "2002"}


def test_may_kill_port_holder_protects_other_tunnels(monkeypatch):
    monkeypatch.setattr(ca, "_is_safe_core_process", lambda pid: True)
    cmdlines = {
        100: "/usr/local/bin/gost -C /etc/smite-node/gost/tunnel-a.json",
        200: "/usr/local/bin/gost -C /etc/smite-node/gost/tunnel-b.json",
        300: "/usr/local/bin/gost -C /app/data/gost/panel-forward.json",  # not started by the node agent
        400: "/usr/local/bin/chisel client --auth x http://1.2.3.4:9000 R:443:127.0.0.1:443",  # no tunnel id in cmdline
    }
    monkeypatch.setattr(ca, "_cmdline_of", lambda pid: cmdlines.get(pid, ""))
    recorded = {"tunnel-c": 400}
    protected = {"tunnel-a", "tunnel-b", "tunnel-c"}

    # Own (stale) process may be killed
    assert ca._may_kill_port_holder(100, "tunnel-a", protected, recorded) is True
    # Another configured tunnel's process is never killed
    assert ca._may_kill_port_holder(200, "tunnel-a", protected, recorded) is False
    # Processes that the node agent did not start are never killed
    assert ca._may_kill_port_holder(300, "tunnel-a", protected, recorded) is False
    # Chisel identified through its PID file is protected as well
    assert ca._may_kill_port_holder(400, "tunnel-a", protected, recorded) is False
    # Leftover of a tunnel that no longer exists on this node may be cleaned up
    assert ca._may_kill_port_holder(200, "tunnel-a", {"tunnel-a"}, {}) is True


def test_may_kill_port_holder_never_kills_non_core(monkeypatch):
    monkeypatch.setattr(ca, "_is_safe_core_process", lambda pid: False)
    monkeypatch.setattr(ca, "_cmdline_of", lambda pid: "/usr/sbin/sshd -D /etc/smite-node/")
    assert ca._may_kill_port_holder(22, None, set(), {}) is False


@pytest.mark.asyncio
async def test_free_ports_never_runs_ss_kill(monkeypatch):
    """free_ports must never tear down established sockets by port number."""
    spawned = []

    async def fake_exec(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("no subprocess expected")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(ca, "_find_pids_by_port_procfs", lambda port, listen_only=True: set())
    monkeypatch.setattr(ca.psutil, "process_iter", lambda *a, **k: iter(()))
    await ca.free_ports([22, 443, 8080])
    assert spawned == []


@pytest.mark.asyncio
async def test_free_ports_skips_protected_process(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path)
    killed = []
    monkeypatch.setattr(ca.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(ca, "_find_pids_by_port_procfs", lambda port, listen_only=True: {4242})
    monkeypatch.setattr(ca.os.path, "exists", lambda p: True if p == "/proc/net/tcp" else os.path.isfile(p) or os.path.isdir(p))
    monkeypatch.setattr(ca, "_is_safe_core_process", lambda pid: True)
    monkeypatch.setattr(ca, "_cmdline_of", lambda pid: "/usr/local/bin/rathole -s /etc/smite-node/rathole/other-tunnel.toml")
    monkeypatch.setattr(ca, "_protected_tunnels_provider", lambda: {"other-tunnel", "my-tunnel"})

    ca._set_port_owner("my-tunnel")
    await ca.free_ports([443])
    assert killed == []

    ca._set_port_owner("other-tunnel")
    await ca.free_ports([443])
    assert killed == [4242]
    ca._set_port_owner(None)


# ---------------------------------------------------------------------------
# PID identity (PID reuse safety)
# ---------------------------------------------------------------------------

def test_pid_record_with_create_time_detects_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path)
    (tmp_path / "tun-1.pid").write_text(f"{os.getpid()} 12345.000")

    fake_proc = MagicMock()
    fake_proc.is_running.return_value = True
    fake_proc.status.return_value = "running"
    fake_proc.create_time.return_value = 99999.0  # different process now owns the PID
    fake_proc.cmdline.return_value = ["/usr/local/bin/gost", "-C", "/etc/smite-node/gost/tun-1.json"]
    monkeypatch.setattr(ca.psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(ca.psutil, "Process", lambda pid: fake_proc)
    monkeypatch.setattr(ca, "_is_safe_core_process", lambda pid: True)

    assert ca._is_tunnel_pid_alive("tun-1", "gost") is False
    fake_proc.create_time.return_value = 12345.2
    assert ca._is_tunnel_pid_alive("tun-1", "gost") is True


def test_legacy_pid_record_requires_tunnel_id_in_cmdline(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path)
    (tmp_path / "tun-1.pid").write_text("777")
    fake_proc = MagicMock()
    fake_proc.is_running.return_value = True
    fake_proc.status.return_value = "running"
    fake_proc.name.return_value = "gost"
    fake_proc.cmdline.return_value = ["/usr/local/bin/gost", "-C", "/etc/smite-node/gost/other.json"]
    monkeypatch.setattr(ca.psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(ca.psutil, "Process", lambda pid: fake_proc)
    monkeypatch.setattr(ca, "_is_safe_core_process", lambda pid: True)
    # A recycled PID now used by another gost tunnel must not count as alive
    assert ca._is_tunnel_pid_alive("tun-1", "gost") is False


def test_verified_pid_rejects_pid_recorded_for_another_tunnel(monkeypatch, tmp_path):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path)
    (tmp_path / "tun-1.pid").write_text("555")
    (tmp_path / "tun-2.pid").write_text("555")
    monkeypatch.setattr(ca, "_is_tunnel_pid_alive", lambda tid, core=None: True)
    assert ca._get_verified_tunnel_pid("tun-1", "chisel") is None


# ---------------------------------------------------------------------------
# AdapterManager: idempotent apply, unknown-tunnel removal, restore
# ---------------------------------------------------------------------------

def _manager(tmp_path) -> AdapterManager:
    m = AdapterManager()
    m.config_dir = tmp_path
    m.tunnels_file = tmp_path / "tunnels.json"
    return m


@pytest.mark.asyncio
async def test_volatile_spec_keys_do_not_restart_tunnel(tmp_path):
    m = _manager(tmp_path)
    adapter = MagicMock()
    adapter.apply = AsyncMock()
    adapter.remove = AsyncMock()
    adapter.status.return_value = {"process_running": True}
    m.adapters["gost"] = adapter

    spec = {"mode": "server", "control_port": 30000, "ports": [443]}
    await m.apply_tunnel("t1", "gost", dict(spec))
    assert adapter.apply.await_count == 1

    for extra in ({"latency_ms": 42}, {"_pending_reapply": True}, {"latency_ms": 17, "_pending_reapply": True}):
        await m.apply_tunnel("t1", "gost", {**spec, **extra})
    assert adapter.apply.await_count == 1, "bookkeeping keys must never restart a tunnel"
    assert "latency_ms" not in m.tunnel_configs["t1"]["spec"]

    # A persisted force_restart flag (left by an older core reset) must not
    # make the next normal apply restart the tunnel either.
    m.tunnel_configs["t1"]["spec"]["force_restart"] = True
    await m.apply_tunnel("t1", "gost", dict(spec))
    assert adapter.apply.await_count == 1

    # An explicit force restart still restarts
    await m.apply_tunnel("t1", "gost", {**spec, "force_restart": True})
    assert adapter.apply.await_count == 2
    assert "force_restart" not in m.tunnel_configs["t1"]["spec"]

    # A real config change restarts
    await m.apply_tunnel("t1", "gost", {**spec, "ports": [443, 8443]})
    assert adapter.apply.await_count == 3


@pytest.mark.asyncio
async def test_apply_adopts_running_process_without_restart(tmp_path):
    """After an agent reload the tunnel may not be in active_tunnels yet."""
    m = _manager(tmp_path)
    adapter = MagicMock()
    adapter.apply = AsyncMock()
    adapter.status.return_value = {"process_running": True}
    m.adapters["rathole"] = adapter
    spec = {"mode": "client", "remote_addr": "1.2.3.4:30000", "token": "x"}
    m.tunnel_configs["t2"] = {"core": "rathole", "spec": dict(spec)}
    await m.apply_tunnel("t2", "rathole", dict(spec))
    adapter.apply.assert_not_awaited()
    assert "t2" in m.active_tunnels


@pytest.mark.asyncio
async def test_remove_unknown_tunnel_frees_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path / "pids")
    (tmp_path / "pids").mkdir()
    m = _manager(tmp_path)
    for adapter in m.adapters.values():
        adapter.config_dir = tmp_path / adapter.name
        adapter.config_dir.mkdir(exist_ok=True)
    free_mock = AsyncMock()
    monkeypatch.setattr(ca, "free_ports", free_mock)
    stop_mock = AsyncMock()
    monkeypatch.setattr(ca, "safe_stop_subprocess", stop_mock)

    await m.remove_tunnel("never-here", purge=True, ports=[22, 443, 2053], control_port=30000, core="gost")
    free_mock.assert_not_awaited()
    stop_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_runs_in_parallel_and_adopts(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path / "pids")
    (tmp_path / "pids").mkdir()
    m = _manager(tmp_path)
    m.start_watchdog = lambda: None
    configs = {f"t{i}": {"core": "gost", "spec": {"mode": "server", "control_port": 30000 + i}} for i in range(6)}
    configs["alive"] = {"core": "gost", "spec": {"mode": "server", "control_port": 31000}}
    m.tunnels_file.write_text(json.dumps(configs))

    in_flight = {"now": 0, "max": 0}

    async def slow_apply(tid, spec):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.05)
        in_flight["now"] -= 1

    adapter = MagicMock()
    adapter.apply = AsyncMock(side_effect=slow_apply)
    adapter.status.side_effect = lambda tid: {"process_running": tid == "alive"}
    m.adapters["gost"] = adapter
    monkeypatch.setattr(ca, "_is_tunnel_pid_alive", lambda tid, core=None: tid == "alive")

    await m.restore_tunnels()
    assert adapter.apply.await_count == 6           # "alive" adopted, not restarted
    assert in_flight["max"] > 1                       # restored concurrently
    assert set(m.active_tunnels) == set(configs)


def test_load_tunnels_recovers_from_backup(tmp_path):
    m = _manager(tmp_path)
    m.tunnel_configs = {"a": {"core": "gost", "spec": {"mode": "server"}}}
    m._save_tunnels()
    m.tunnel_configs = {"a": {"core": "gost", "spec": {"mode": "server"}}, "b": {"core": "rathole", "spec": {"mode": "client"}}}
    m._save_tunnels()                                  # backup now holds {"a"}
    m.tunnels_file.write_text("{ corrupted")
    m._load_tunnels()
    assert set(m.tunnel_configs) == {"a"}


def test_log_rotation_shrinks_append_mode_log(tmp_path):
    m = _manager(tmp_path)
    m.LOG_ROTATE_THRESHOLD = 1024
    m.LOG_ROTATE_KEEP = 100
    log_path = tmp_path / "core.log"
    writer = ca._open_core_log(log_path)
    writer.write("x" * 5000)
    writer.flush()
    m._rotate_oversized_logs()
    assert log_path.stat().st_size < 200
    writer.write("tail-after-rotation\n")
    writer.flush()
    writer.close()
    # O_APPEND: new data lands at the new end, no sparse gap
    assert log_path.stat().st_size < 300
    assert log_path.read_text().endswith("tail-after-rotation\n")


@pytest.mark.asyncio
async def test_chisel_remove_does_not_parse_log_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "_get_pid_dir", lambda: tmp_path / "pids")
    (tmp_path / "pids").mkdir()
    adapter = ChiselAdapter.__new__(ChiselAdapter)
    adapter.config_dir = tmp_path
    adapter.processes = {}
    adapter.log_handles = {}
    (tmp_path / "tun-c.log").write_text(
        "Starting chisel client for tunnel tun-c\n"
        "Command: /usr/local/bin/chisel client --auth ***REDACTED*** http://1.2.3.4:9000 R:2053:127.0.0.1:2053\n"
        "2026/10/12 22:15:03 client: Connected (Latency 80ms)\n"
        "2026/10/12 21:00:01 client: Retrying in 1s...\n"
    )
    freed = []

    async def capture(ports):
        freed.extend(ports)

    monkeypatch.setattr(ca, "free_ports", capture)
    monkeypatch.setattr(ca, "safe_stop_subprocess", AsyncMock())
    await adapter.remove("tun-c")
    assert 2053 in freed
    assert 22 not in freed and 21 not in freed and 15 not in freed


# ---------------------------------------------------------------------------
# Agent API authentication
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_client(tmp_path, monkeypatch):
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
    app.include_router(agent_router.auth_router, prefix="/api/agent")
    manager = MagicMock()
    manager.active_tunnels = {}
    manager.get_tunnel_status = AsyncMock(return_value={"active": True})
    app.state.adapter_manager = manager
    return TestClient(app), agent_auth


def test_agent_api_open_until_token_provisioned_then_enforced(agent_client):
    client, agent_auth = agent_client
    token = "A" * 43

    assert client.get("/api/agent/tunnels/status", params={"tunnel_id": "t1"}).status_code == 200

    resp = client.post("/api/agent/auth/bootstrap", json={"token": token}, headers={"X-Node-Token": token})
    assert resp.status_code == 200 and resp.json()["changed"] is True
    assert agent_auth.TOKEN_FILE.read_text() == token

    assert client.get("/api/agent/tunnels/status", params={"tunnel_id": "t1"}).status_code == 401
    assert client.get("/api/agent/tunnels/status", params={"tunnel_id": "t1"}, headers={"X-Node-Token": "wrong" * 9}).status_code == 401
    assert client.get("/api/agent/tunnels/status", params={"tunnel_id": "t1"}, headers={"X-Node-Token": token}).status_code == 200
    assert client.post("/api/agent/decommission", json={}).status_code == 401

    # Idempotent re-provisioning by the owning panel
    resp = client.post("/api/agent/auth/bootstrap", json={"token": token}, headers={"X-Node-Token": token})
    assert resp.status_code == 200 and resp.json()["changed"] is False
    # A third party cannot replace the token
    other = "B" * 43
    assert client.post("/api/agent/auth/bootstrap", json={"token": other}, headers={"X-Node-Token": other}).status_code == 401
    assert agent_auth.TOKEN_FILE.read_text() == token


def test_agent_bootstrap_rejects_malformed_token(agent_client):
    client, _ = agent_client
    assert client.post("/api/agent/auth/bootstrap", json={"token": "short"}).status_code == 400
    assert client.post("/api/agent/auth/bootstrap", json={"token": "x" * 40 + '"; rm -rf /'}).status_code == 400


def test_agent_static_env_token_wins(agent_client, monkeypatch):
    client, agent_auth = agent_client
    monkeypatch.setattr(agent_auth.settings, "node_api_token", "static-env-token")
    token = "C" * 43
    assert client.post("/api/agent/auth/bootstrap", json={"token": token}, headers={"X-Node-Token": token}).status_code == 409
    assert client.get("/api/agent/tunnels/status", params={"tunnel_id": "t1"}, headers={"X-Node-Token": "static-env-token"}).status_code == 200


def test_ping_rejects_option_injection(agent_client):
    client, _ = agent_client
    assert client.get("/api/agent/ping", params={"target": "-f"}).status_code == 400
    assert client.get("/api/agent/ping", params={"target": "1.1.1.1", "port": 70000}).status_code == 400


def test_status_reports_capabilities(agent_client):
    client, _ = agent_client
    data = client.get("/api/agent/status").json()
    assert data["hot_reload"] is True
    assert data["api_version"] >= 2
    assert "agent_started_at" in data


# ---------------------------------------------------------------------------
# Panel side
# ---------------------------------------------------------------------------

def test_node_client_strips_volatile_keys_only_for_apply():
    from app.node_client import _strip_volatile_spec_keys
    payload = {"tunnel_id": "t", "spec": {"ports": [1], "latency_ms": 9, "_pending_reapply": True, "force_restart": True}}
    out = _strip_volatile_spec_keys("/api/agent/tunnels/apply", payload)
    assert out["spec"] == {"ports": [1], "force_restart": True}
    assert payload["spec"]["latency_ms"] == 9  # caller's dict untouched
    assert _strip_volatile_spec_keys("/api/agent/tunnels/remove", payload) is payload


def test_public_node_metadata_hides_agent_token():
    from app.node_client import public_node_metadata, node_agent_token
    meta = {"ip_address": "1.2.3.4", "agent_token": "secret", "agent_token_pending": "p", "role": "iran"}
    assert public_node_metadata(meta) == {"ip_address": "1.2.3.4", "role": "iran"}
    node = SimpleNamespace(node_metadata=meta)
    assert node_agent_token(node) == "secret"
    node_env = SimpleNamespace(node_metadata={**meta, "agent_token_mode": "env"})
    with patch("app.node_client.settings") as s:
        s.node_api_token = "global"
        assert node_agent_token(node_env) == "global"


def test_node_request_trust_rules():
    from app.routers.nodes import _node_request_trust
    req = MagicMock()
    node = SimpleNamespace(node_metadata={"agent_token": "tok-123"})
    with patch("app.config.settings") as s:
        s.node_api_token = ""
        req.headers = {"X-Node-Token": "tok-123"}
        assert _node_request_trust(req, node) == (True, False)
        req.headers = {}
        assert _node_request_trust(req, node) == (False, False)
        legacy = SimpleNamespace(node_metadata={})
        assert _node_request_trust(req, legacy) == (False, True)


@pytest.mark.asyncio
async def test_enrich_authoritative_list_contains_error_tunnels():
    """Tunnels in 'error' state must stay authoritative, or nodes purge (kill) them."""
    from app.routers.nodes import enrich_node_response_metadata
    db = AsyncMock()
    t_res = MagicMock()
    t_res.scalars.return_value.all.return_value = ["t-active", "t-error"]
    p_res = MagicMock()
    p_res.scalar_one_or_none.return_value = None
    db.execute.side_effect = [t_res, p_res]
    enriched = await enrich_node_response_metadata(db, "node-1", {})
    assert enriched["active_tunnel_ids"] == ["t-active", "t-error"]
    query_sql = str(db.execute.call_args_list[0].args[0])
    assert "status" not in query_sql.lower()


def test_frp_comm_server_binds_node_apis_to_loopback(tmp_path, monkeypatch):
    from app import frp_comm_manager as fcm
    mgr = fcm.FrpCommManager.__new__(fcm.FrpCommManager)
    mgr.config_dir = tmp_path
    mgr.config_file = tmp_path / "frps_comm.yaml"
    mgr.log_file = tmp_path / "frps_comm.log"
    mgr.process = None
    mgr.enabled = False

    def no_binary():
        raise FileNotFoundError("frps")

    mgr._resolve_binary_path = no_binary
    asyncio.run(mgr.start(7000, 'tok"en'))
    cfg = mgr.config_file.read_text()
    assert 'proxyBindAddr: "127.0.0.1"' in cfg
    assert "start: 10000" in cfg and "end: 19999" in cfg
    assert 'token: "tok\\"en"' in cfg


# ---------------------------------------------------------------------------
# Zero-downtime update tooling
# ---------------------------------------------------------------------------

def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("smite_node_cli", repo_root / "cli" / "smite-node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_update_keeps_main_py_inode_and_backs_up(tmp_path):
    cli = _load_cli()
    root = tmp_path / "smite-node"
    (root / "app" / "routers").mkdir(parents=True)
    (root / "main.py").write_bytes(b"old main\n")
    (root / "app" / "core_adapters.py").write_bytes(b"old core\n")
    inode_before = os.stat(root / "main.py").st_ino

    backup = cli._write_code_atomically(root, {"main.py": b"new main\n", "app/core_adapters.py": b"new core\n", "app/hot_reload.py": b"x = 1\n"})
    assert (root / "main.py").read_bytes() == b"new main\n"
    assert os.stat(root / "main.py").st_ino == inode_before  # single-file bind mount keeps working
    assert (root / "app" / "hot_reload.py").exists()
    assert (backup / "main.py").read_bytes() == b"old main\n"

    cli._restore_backup(root, backup)
    assert (root / "main.py").read_bytes() == b"old main\n"
    assert (root / "app" / "core_adapters.py").read_bytes() == b"old core\n"


def test_cli_node_manifest_covers_every_agent_module():
    cli = _load_cli()
    node_dir = repo_root / "node"
    on_disk = {str(p.relative_to(node_dir)).replace("\\", "/") for p in node_dir.rglob("*.py") if "__pycache__" not in p.parts}
    assert on_disk == set(cli.NODE_CODE_FILES), "update manifest must list every node agent file"


def test_hot_reload_exec_argv_for_uvicorn(monkeypatch):
    from node.app import hot_reload
    monkeypatch.setattr(hot_reload.sys, "argv", ["/usr/local/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"])
    argv = hot_reload._build_exec_argv()
    assert argv[0] == hot_reload.sys.executable
    assert argv[1:4] == ["-m", "uvicorn", "main:app"]
    assert argv[-2:] == ["--port", "8888"]


@pytest.mark.asyncio
async def test_hot_reload_aborts_on_broken_code(monkeypatch):
    from node.app import hot_reload
    monkeypatch.setattr(hot_reload, "_validate_new_code", lambda: False)
    execs = []
    monkeypatch.setattr(hot_reload.os, "execv", lambda *a: execs.append(a))
    app = SimpleNamespace(state=SimpleNamespace(registration_task=MagicMock(), adapter_manager=MagicMock(), h2_client=None))
    await hot_reload._reload(app)
    assert execs == []
    app.state.registration_task.cancel.assert_not_called()
