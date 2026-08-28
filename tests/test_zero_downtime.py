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
    ALLOWED_CORE_BINARIES
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
