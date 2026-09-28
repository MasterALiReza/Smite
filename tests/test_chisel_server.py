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
            return mock_proc

        with patch("app.chisel_server.start_async_process" if "app.chisel_server" in sys.modules else "panel.app.chisel_server.start_async_process", side_effect=fake_start_process):
            with patch("app.chisel_server.wait_for_port" if "app.chisel_server" in sys.modules else "panel.app.chisel_server.wait_for_port", return_value=True):
                with patch("shutil.which", return_value="/usr/local/bin/chisel"):
                    with patch("os.path.exists", return_value=True):
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

        # Cleanup test
        asyncio.run(manager.stop_server("test-chisel-srv"))
        assert (manager.config_dir / "test-chisel-srv_cert.pem").exists() is False
        assert (manager.config_dir / "test-chisel-srv_key.pem").exists() is False


if __name__ == "__main__":
    import sys
    test_chisel_server_manager_command_assembly()
    print("PASS: test_chisel_server_manager_command_assembly")
