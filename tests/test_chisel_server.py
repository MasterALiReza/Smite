import sys
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from panel.app.chisel_server import ChiselServerManager
except ImportError:
    from app.chisel_server import ChiselServerManager


def test_chisel_server_manager_command_assembly():
    """Verify ChiselServerManager builds valid chisel server command with WSS, backend, and keyfile"""
    manager = ChiselServerManager()
    with tempfile.TemporaryDirectory() as tmpdir:
        manager.config_dir = Path(tmpdir)
        
        captured_cmds = []
        async def fake_start_process(cmd, cwd, log_f):
            captured_cmds.append(cmd)
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.wait = AsyncMock(return_value=0)
            mock_proc.poll = MagicMock(return_value=0)
            return mock_proc

        target_mod = ChiselServerManager.__module__
        with patch(f"{target_mod}.start_async_process", side_effect=fake_start_process), \
             patch(f"{target_mod}.wait_for_port", return_value=True), \
             patch("shutil.which", return_value="/usr/local/bin/chisel"), \
             patch("os.path.exists", return_value=True), \
             patch("subprocess.run"):
            asyncio.run(manager.start_server(
                                tunnel_id="test-chisel-srv",
                                server_port=18080,
                                auth="user:pass",
                                backend_url="https://speedtest.net",
                                socks5=True,
                                keepalive="15s",
                                tls_cert_pem="-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----",
                                tls_key_pem="-----BEGIN RSA PRIVATE KEY-----\nMOCK\n-----END RSA PRIVATE KEY-----"
                            ))

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert "server" in cmd
        assert "--port" in cmd
        assert "18080" in cmd
        assert "--auth" in cmd
        assert "user:pass" in cmd
        assert "--backend" in cmd
        assert "https://speedtest.net" in cmd
        assert "--socks5" in cmd
        assert "--keepalive" in cmd
        assert "15s" in cmd
        assert "--tls-cert" in cmd
        assert "--tls-key" in cmd
        # Crucial check: verify --fingerprint is NEVER in server command
        assert "--fingerprint" not in cmd

        # Verify log file masked sensitive --auth
        log_file = manager.config_dir / "chisel_test-chisel-srv.log"
        assert log_file.exists()
        log_content = log_file.read_text(encoding="utf-8")
        assert "user:pass" not in log_content
        assert "***REDACTED***" in log_content

        # Create dummy SSH key to verify persistence
        ssh_key = manager.config_dir / "test-chisel-srv_ssh.key"
        ssh_key.write_text("MOCK_SSH_KEY", encoding="utf-8")

        # Cleanup test (restart semantics: certs deleted, ssh key preserved)
        asyncio.run(manager.stop_server("test-chisel-srv"))
        assert (manager.config_dir / "test-chisel-srv_cert.pem").exists() is False
        assert (manager.config_dir / "test-chisel-srv_key.pem").exists() is False
        assert ssh_key.exists() is True  # Key preserved for client fingerprint pinning!

        # Permanent removal test (purge=True)
        asyncio.run(manager.stop_server("test-chisel-srv", purge=True))
        assert ssh_key.exists() is False


def test_chisel_adapter_client_reverse_and_direct():
    """Verify ChiselAdapter correctly builds client reverse & direct remotes, validates specs, and preserves keys"""
    from node.app.core_adapters import ChiselAdapter

    adapter = ChiselAdapter()
    with tempfile.TemporaryDirectory() as tmpdir:
        adapter.config_dir = Path(tmpdir)
        adapter._resolve_binary_path = lambda: Path("/fake/chisel")

        captured_cmds = []
        async def fake_spawn(cmd, stdout=None, stderr=None):
            captured_cmds.append(cmd)
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.returncode = None
            mock_proc.poll = MagicMock(return_value=None)
            return mock_proc

        with patch("node.app.core_adapters._spawn_core_subprocess", side_effect=fake_spawn), \
             patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
             patch("node.app.core_adapters.safe_stop_subprocess", new_callable=AsyncMock), \
             patch("node.app.core_adapters._save_tunnel_pid"), \
             patch("node.app.core_adapters._remove_tunnel_pid"), \
             patch("asyncio.sleep", new_callable=AsyncMock):

            # Test 1: Reverse mode client (is_reverse=True)
            reverse_spec = {
                "mode": "client",
                "server_url": "https://1.2.3.4:25000",
                "transport": "wss",
                "is_reverse": True,
                "type": "tcp+udp",
                "ports": [8080],
                "auth": "smite:secret",
                "key": "symmetric-key",
                "custom_sni": "my-sni.com",
                "custom_host": "my-host.com",
                "user_agent": "Mozilla/5.0",
                "proxy": "http://upstream:8080",
                "fingerprint": "12:34:56:78"
            }
            asyncio.run(adapter.apply("tun-rev", reverse_spec))

            assert len(captured_cmds) == 1
            cmd_rev = captured_cmds[0]
            assert "client" in cmd_rev
            assert "--auth" in cmd_rev and "smite:secret" in cmd_rev
            assert "--key" in cmd_rev and "symmetric-key" in cmd_rev
            assert "--fingerprint" in cmd_rev and "12:34:56:78" in cmd_rev
            assert "--sni" in cmd_rev and "my-sni.com" in cmd_rev
            assert "--hostname" in cmd_rev and "my-host.com" in cmd_rev
            assert "--proxy" in cmd_rev and "http://upstream:8080" in cmd_rev
            assert "--header" in cmd_rev and "User-Agent: Mozilla/5.0" in cmd_rev
            # In reverse mode with tcp+udp: both R:8080:127.0.0.1:8080 and R:8080:127.0.0.1:8080/udp
            assert "R:8080:127.0.0.1:8080" in cmd_rev
            assert "R:8080:127.0.0.1:8080/udp" in cmd_rev

            # Test 2: Direct mode client (is_reverse=False)
            captured_cmds.clear()
            direct_spec = {
                "mode": "client",
                "server_url": "https://5.6.7.8:25000",
                "is_reverse": False,
                "type": "all",
                "ports": [9090],
                "auth": "smite:secret"
            }
            asyncio.run(adapter.apply("tun-dir", direct_spec))

            assert len(captured_cmds) == 1
            cmd_dir = captured_cmds[0]
            assert "client" in cmd_dir
            # In direct mode with all (tcp+udp): 9090:127.0.0.1:9090 and 9090:127.0.0.1:9090/udp (NO 'R:' prefix!)
            assert "9090:127.0.0.1:9090" in cmd_dir
            assert "9090:127.0.0.1:9090/udp" in cmd_dir
            assert not any("R:" in arg for arg in cmd_dir)

            # Test 3: Early spec validation before killing process
            adapter.processes["tun-dir"] = MagicMock()
            invalid_spec = {
                "mode": "client",
                # missing server_url!
            }
            import pytest
            with pytest.raises(ValueError):
                asyncio.run(adapter.apply("tun-dir", invalid_spec))
            # Verify process was NOT removed because validation caught it first!
            assert "tun-dir" in adapter.processes

            # Test 4: Key persistence in remove
            ssh_key = adapter.config_dir / "tun-dir_ssh.key"
            ssh_key.write_text("NODE_SSH_KEY", encoding="utf-8")
            asyncio.run(adapter.remove("tun-dir", purge=False))
            assert ssh_key.exists() is True

            asyncio.run(adapter.remove("tun-dir", purge=True))
            assert ssh_key.exists() is False

            # Cleanup tun-rev process and log handles
            asyncio.run(adapter.remove("tun-rev", purge=True))


if __name__ == "__main__":
    import sys
    test_chisel_server_manager_command_assembly()
    test_chisel_adapter_client_reverse_and_direct()
    print("ALL TESTS PASSED")
