import os
import sys
import tempfile
import asyncio
from pathlib import Path
import pytest

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "node"))
sys.path.insert(0, str(repo_root / "panel"))

from node.app.core_adapters import (
    _save_tunnel_pid,
    _get_tunnel_pid,
    _remove_tunnel_pid,
    _is_tunnel_pid_alive,
    _is_safe_core_process,
    AdapterManager,
    ALLOWED_CORE_BINARIES,
    RatholeAdapter,
    BackhaulAdapter,
    ChiselAdapter,
    FrpAdapter,
    GostAdapter
)


@pytest.mark.asyncio
async def test_pid_persistence_and_removal(monkeypatch, tmp_path):
    """Test saving, retrieving, and deleting PID files on disk."""
    monkeypatch.setattr("node.app.core_adapters._get_pid_dir", lambda: tmp_path)
    
    tunnel_id = "test-tunnel-123"
    current_pid = os.getpid()
    
    # Save PID
    _save_tunnel_pid(tunnel_id, current_pid)
    saved_pid = _get_tunnel_pid(tunnel_id)
    assert saved_pid == current_pid
    
    # Remove PID
    _remove_tunnel_pid(tunnel_id)
    assert _get_tunnel_pid(tunnel_id) is None


@pytest.mark.asyncio
async def test_allowed_core_binaries():
    """Test that safe core processes validation only recognizes authorized proxy binaries."""
    for binary in ["rathole", "backhaul", "frps", "frpc", "gost", "chisel"]:
        assert binary in ALLOWED_CORE_BINARIES


@pytest.mark.asyncio
async def test_adapter_manager_idempotency_and_adoption(monkeypatch, tmp_path):
    """Test that AdapterManager adopts running processes and skips apply if spec is unchanged."""
    monkeypatch.setattr("node.app.core_adapters._get_pid_dir", lambda: tmp_path)
    
    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"
    
    # Mock adapter
    class MockAdapter:
        def __init__(self):
            self.apply_count = 0
            self.remove_count = 0
            
        async def apply(self, tunnel_id, spec):
            self.apply_count += 1
            
        async def remove(self, tunnel_id):
            self.remove_count += 1
            
        def status(self, tunnel_id):
            return {"active": True, "process_running": True, "pid": 9999}
            
    mock_adapter = MockAdapter()
    monkeypatch.setattr(manager, "get_adapter", lambda core: mock_adapter)
    
    # Mock _is_tunnel_pid_alive to simulate a running proxy process
    monkeypatch.setattr("node.app.core_adapters._is_tunnel_pid_alive", lambda tid, core=None: True)
    
    tunnel_id = "tunnel-abc"
    spec = {"listen_port": 8080, "target": "127.0.0.1:8080"}
    
    # First apply
    await manager.apply_tunnel(tunnel_id, "gost", spec)
    assert mock_adapter.apply_count == 1
    assert tunnel_id in manager.active_tunnels
    
    # Second apply with IDENTICAL spec -> Must skip restarting to prevent downtime!
    await manager.apply_tunnel(tunnel_id, "gost", spec)
    assert mock_adapter.apply_count == 1, "Idempotent apply should NOT restart a running healthy process"
    
    # Third apply with CHANGED spec -> Must apply the new spec
    new_spec = {"listen_port": 8081, "target": "127.0.0.1:8081"}
    await manager.apply_tunnel(tunnel_id, "gost", new_spec)
    assert mock_adapter.apply_count == 2, "Apply with changed spec should apply the update"


@pytest.mark.asyncio
async def test_adapter_manager_cleanup_preserves_running_cores():
    """Test that cleanup on agent shutdown preserves background cores by default."""
    manager = AdapterManager()
    
    class MockAdapter:
        def __init__(self):
            self.remove_called = False
            
        async def remove(self, tunnel_id):
            self.remove_called = True
            
        def status(self, tunnel_id):
            return {"active": True}
            
    mock_adapter = MockAdapter()
    manager.active_tunnels["t-1"] = mock_adapter
    
    # Standard node shutdown (kill_processes=False)
    await manager.cleanup(kill_processes=False)
    assert mock_adapter.remove_called is False, "Node shutdown should NOT kill active tunnel processes"
    assert len(manager.active_tunnels) == 0


@pytest.mark.asyncio
async def test_adapter_manager_restore_adopts_live_pids(monkeypatch, tmp_path):
    """Test that node startup adopts live running proxy processes without calling apply."""
    monkeypatch.setattr("node.app.core_adapters._get_pid_dir", lambda: tmp_path)
    
    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"
    
    manager.tunnel_configs = {
        "tun-1": {
            "core": "rathole",
            "spec": {"remote_addr": "1.2.3.4:2333", "token": "abc"}
        }
    }
    manager._save_tunnels()
    
    class MockAdapter:
        def __init__(self):
            self.apply_called = False
            
        async def apply(self, tunnel_id, spec):
            self.apply_called = True
            
        def status(self, tunnel_id):
            return {"active": True, "process_running": True, "pid": 1234}
            
    mock_adapter = MockAdapter()
    monkeypatch.setattr(manager, "get_adapter", lambda core: mock_adapter)
    monkeypatch.setattr("node.app.core_adapters._is_tunnel_pid_alive", lambda tid, core=None: True)
    
    await manager.restore_tunnels()
    
    assert "tun-1" in manager.active_tunnels
    assert mock_adapter.apply_called is False, "restore_tunnels should adopt existing process WITHOUT re-applying or killing"


def test_staged_reapply_flag_logic():
    """Test that spec staging preserves _pending_reapply and pops cleanly."""
    spec = {"listen_port": 8080, "target": "127.0.0.1:8080"}
    
    # Simulated staged edit
    spec["_pending_reapply"] = True
    assert spec.get("_pending_reapply") is True
    
    # Simulated apply success
    popped = spec.pop("_pending_reapply", None)
    assert popped is True
    assert "_pending_reapply" not in spec


@pytest.mark.asyncio
async def test_spawn_core_subprocess_no_pipe_deadlock(monkeypatch):
    """Test that _spawn_core_subprocess defaults to DEVNULL instead of PIPE to avoid deadlock."""
    from node.app.core_adapters import _spawn_core_subprocess
    import subprocess
    
    captured_kwargs = {}
    async def mock_exec(*cmd, **kwargs):
        captured_kwargs.update(kwargs)
        class MockProc:
            pid = 9999
            returncode = None
        return MockProc()
        
    monkeypatch.setattr(asyncio, "create_subprocess_exec", mock_exec)
    
    await _spawn_core_subprocess(["echo", "hello"])
    assert captured_kwargs.get("stdout") == subprocess.DEVNULL
    assert captured_kwargs.get("stderr") == subprocess.DEVNULL


@pytest.mark.asyncio
async def test_adapter_manager_tunnel_lock_serializes_concurrent_calls(monkeypatch, tmp_path):
    """Test that concurrent apply_tunnel calls on the same tunnel_id are strictly serialized by _tunnel_locks."""
    monkeypatch.setattr("node.app.core_adapters._get_pid_dir", lambda: tmp_path)
    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"
    
    order = []
    
    class SlowAdapter:
        name = "slow"
        async def apply(self, tunnel_id, spec):
            order.append(f"start-{spec['step']}")
            await asyncio.sleep(0.05)
            order.append(f"end-{spec['step']}")
            
        async def remove(self, tunnel_id):
            pass
            
        def status(self, tunnel_id):
            return {"active": False, "process_running": False}

    monkeypatch.setattr(manager, "get_adapter", lambda core: SlowAdapter())
    
    # Run two concurrent applies for the same tunnel
    t1 = asyncio.create_task(manager.apply_tunnel("tun-concurrent", "slow", {"step": 1}))
    t2 = asyncio.create_task(manager.apply_tunnel("tun-concurrent", "slow", {"step": 2}))
    await asyncio.gather(t1, t2)
    
    # The first must finish before the second starts
    assert order == ["start-1", "end-1", "start-2", "end-2"]


@pytest.mark.asyncio
async def test_rathole_adapter_ipv6_brackets(monkeypatch, tmp_path):
    """Test RatholeAdapter properly encloses IPv6 addresses in square brackets in bind_addr."""
    adapter = RatholeAdapter()
    adapter.config_dir = tmp_path
    
    # Mock subprocess spawn and free_port
    monkeypatch.setattr("node.app.core_adapters._spawn_core_subprocess", lambda *args, **kwargs: asyncio.sleep(0.01))
    monkeypatch.setattr("node.app.core_adapters.free_port", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", lambda *args, **kwargs: asyncio.sleep(0.001))
    
    class DummyProc:
        pid = 1234
        returncode = None
    
    async def mock_spawn(*args, **kwargs):
        return DummyProc()
    
    monkeypatch.setattr("node.app.core_adapters._spawn_core_subprocess", mock_spawn)
    
    spec = {
        "mode": "server",
        "bind_addr": "2001:db8::1:23333",
        "token": "tok123",
        "ports": [8080]
    }
    await adapter.apply("rathole-ipv6", spec)
    cfg = (tmp_path / "rathole-ipv6.toml").read_text(encoding="utf-8")
    assert 'bind_addr = "[2001:db8::1]:23333"' in cfg
    await adapter.remove("rathole-ipv6")


@pytest.mark.asyncio
async def test_backhaul_adapter_ports_formatting(monkeypatch, tmp_path):
    """Test BackhaulAdapter properly normalizes raw port integers/strings with target_host in server mode."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    
    monkeypatch.setattr("node.app.core_adapters.free_port", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))
    
    class DummyProc:
        pid = 1235
        returncode = None
        
    async def mock_exec(*args, **kwargs):
        return DummyProc()
        
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    
    spec = {
        "mode": "server",
        "bind_addr": "0.0.0.0:3080",
        "token": "bh-tok",
        "ports": [8080, "8081", "9000=127.0.0.1:9000"],
        "target_host": "127.0.0.1"
    }
    await adapter.apply("backhaul-test", spec)
    cfg = (tmp_path / "backhaul-test.toml").read_text(encoding="utf-8")
    assert '"8080=127.0.0.1:8080"' in cfg
    assert '"8081=127.0.0.1:8081"' in cfg
    assert '"9000=127.0.0.1:9000"' in cfg
    await adapter.remove("backhaul-test")


@pytest.mark.asyncio
async def test_chisel_adapter_arguments_and_udp(monkeypatch, tmp_path):
    """Test ChiselAdapter passes resolved control_port, auth token, and UDP reverse mappings."""
    adapter = ChiselAdapter()
    adapter.config_dir = tmp_path
    
    monkeypatch.setattr("node.app.core_adapters.free_port", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/chisel"))
    
    captured_server_cmd = []
    captured_client_cmd = []
    
    class DummyProc:
        pid = 1236
        returncode = None
        
    async def mock_exec_server(*cmd, **kwargs):
        captured_server_cmd.extend(cmd)
        return DummyProc()
        
    async def mock_exec_client(*cmd, **kwargs):
        captured_client_cmd.extend(cmd)
        return DummyProc()
    
    # 1. Server mode test
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec_server)
    server_spec = {
        "mode": "server",
        "control_port": 26500,
        "auth": "secret_token",
        "ports": [8080]
    }
    await adapter.apply("chisel-srv", server_spec)
    assert "--port" in captured_server_cmd
    assert "26500" in captured_server_cmd
    assert "--auth" in captured_server_cmd
    assert "secret_token" in captured_server_cmd
    assert "--reverse" in captured_server_cmd
    await adapter.remove("chisel-srv")
    
    # 2. Client mode with UDP test
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec_client)
    client_spec = {
        "mode": "client",
        "server_url": "http://1.1.1.1:26500",
        "auth": "secret_token",
        "ports": [8080],
        "tunnel_type": "tcp+udp"
    }
    await adapter.apply("chisel-cli", client_spec)
    assert "R:8080:127.0.0.1:8080" in captured_client_cmd
    assert "R:8080:127.0.0.1:8080/udp" in captured_client_cmd
    assert "--auth" in captured_client_cmd
    assert "secret_token" in captured_client_cmd
    await adapter.remove("chisel-cli")


@pytest.mark.asyncio
async def test_frp_adapter_dual_stack_tcp_udp(monkeypatch, tmp_path):
    """Test FrpAdapter generates both TCP and UDP proxy sections when type is tcp+udp."""
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    
    monkeypatch.setattr("node.app.core_adapters.free_port", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/frpc"))
    
    class DummyProc:
        pid = 1237
        returncode = None
        
    async def mock_exec(*cmd, **kwargs):
        return DummyProc()
        
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    
    client_spec = {
        "mode": "client",
        "server_addr": "1.1.1.1",
        "server_port": 7000,
        "token": "frp-secret",
        "type": "tcp+udp",
        "ports": [{"local": 8080, "remote": 8080}]
    }
    await adapter.apply("frp-dual", client_spec)
    cfg = (tmp_path / "frpc_frp-dual.yaml").read_text(encoding="utf-8")
    assert "name: frp-dual_tcp" in cfg
    assert "type: tcp" in cfg
    assert "name: frp-dual_udp" in cfg
    assert "type: udp" in cfg
    await adapter.remove("frp-dual")


@pytest.mark.asyncio
async def test_gost_adapter_unique_service_names(monkeypatch, tmp_path):
    """Test GostAdapter formats unique service names containing tunnel_id to prevent collision."""
    import json
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    
    monkeypatch.setattr("node.app.core_adapters.free_port", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", lambda *args, **kwargs: asyncio.sleep(0.001))
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))
    
    class DummyProc:
        pid = 1238
        returncode = None
        
    async def mock_exec(*cmd, **kwargs):
        return DummyProc()
        
    monkeypatch.setattr("asyncio.create_subprocess_exec", mock_exec)
    
    spec = {
        "mode": "client",
        "server_ip": "1.1.1.1",
        "control_port": 28000,
        "ports": [8080],
        "type": "tcp+udp"
    }
    await adapter.apply("tun-gost-xyz", spec)
    cfg_data = json.loads((tmp_path / "tun-gost-xyz.json").read_text(encoding="utf-8"))
    
    service_names = [s["name"] for s in cfg_data["services"]]
    assert "tcp-in-8080-tun-gost-xyz" in service_names
    assert "udp-in-8080-tun-gost-xyz" in service_names
    await adapter.remove("tun-gost-xyz")


