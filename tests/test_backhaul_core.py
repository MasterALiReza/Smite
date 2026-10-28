"""
Comprehensive unit tests for Smite Backhaul tunneling core, adapters, and managers.
Validates protocol mappings, security hardening, TOML injection prevention,
process lifecycle safety, batch port freeing, and reverse tunnel logic.
"""
import asyncio
import os
import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "node"))
sys.path.insert(0, str(repo_root / "panel"))

from node.app.core_adapters import BackhaulAdapter, AdapterManager, format_address_port
from panel.app.spec_builder import build_backhaul_node_specs, format_address_port as panel_format_address_port
from panel.app.backhaul_manager import BackhaulManager
from panel.app.routers.tunnels import extract_all_tunnel_ports
from node.app.routers.agent import TunnelVerify


class DummyTunnel:
    def __init__(self, id="tun-backhaul-1", core="backhaul", type="tcp", spec=None):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}


def test_format_address_port():
    """Verify IPv6 addresses are properly bracketed while IPv4 addresses remain standard."""
    assert format_address_port("127.0.0.1", 8080) == "127.0.0.1:8080"
    assert format_address_port("::", 3080) == "[::]:3080"
    assert format_address_port("2001:db8::1", 443) == "[2001:db8::1]:443"
    assert format_address_port("[2001:db8::1]", 443) == "[2001:db8::1]:443"
    assert panel_format_address_port("::1", 9000) == "[::1]:9000"


def test_toml_escaping_injection_prevention():
    """Verify TOML renderer escapes double quotes, newlines, carriage returns, and backslashes."""
    adapter = BackhaulAdapter()
    malicious_data = {
        "server": {
            "token": 'secret"token\nwith\r\nnewlines\\and"quotes',
            "ports": [
                '80=127.0.0.1:80"\n[malicious_section]\nhack = true\n',
                "443"
            ]
        }
    }
    rendered = adapter._render_toml(malicious_data)
    # The output should NOT have an unescaped raw section injection
    assert "\n[malicious_section]\n" not in rendered
    assert '\\"token\\nwith\\r\\nnewlines\\\\and\\"quotes' in rendered

    # Also test BackhaulManager._render_toml on the panel side
    manager = BackhaulManager()
    panel_rendered = manager._render_toml(malicious_data)
    assert "\n[malicious_section]\n" not in panel_rendered
    assert '\\"token\\nwith\\r\\nnewlines\\\\and\\"quotes' in panel_rendered


def test_spec_builder_backhaul_pure_udp_mapping():
    """Verify Backhaul pure UDP is converted to TCP with accept_udp = True."""
    tunnel = DummyTunnel(
        spec={
            "transport_type": "udp",
            "control_port": 3080,
            "ports": [53, 1194],
            "token": "my-secret-token"
        }
    )
    server_spec, client_spec = build_backhaul_node_specs(tunnel, iran_node_ip="1.1.1.1", foreign_node_ip="2.2.2.2")
    
    assert server_spec["transport"] == "tcp"
    assert server_spec["accept_udp"] is True
    assert client_spec["transport"] == "tcp"
    assert client_spec["accept_udp"] is True
    assert client_spec["remote_addr"] == "1.1.1.1:3080"
    assert server_spec["server_options"]["mux_recievebuffer"] == 16777216
    assert server_spec["server_options"]["mux_streambuffer"] == 4194304
    assert client_spec["client_options"]["mux_recievebuffer"] == 16777216
    assert client_spec["client_options"]["mux_streambuffer"] == 4194304


def test_spec_builder_backhaul_gaming_buffer():
    """Verify Backhaul gaming mode sets mux_streambuffer to at least 2MB (2097152)."""
    tunnel = DummyTunnel(
        spec={
            "transport_type": "tcpmux",
            "control_port": 3080,
            "ports": [8080],
            "token": "game-token",
            "gaming_mode": True
        }
    )
    tunnel.gaming_mode = True
    server_spec, client_spec = build_backhaul_node_specs(tunnel, iran_node_ip="1.1.1.1", foreign_node_ip="2.2.2.2")
    assert server_spec["server_options"]["mux_streambuffer"] == 2097152
    assert client_spec["client_options"]["mux_streambuffer"] == 2097152
    assert server_spec["server_options"]["mux_framesize"] == 4096


def test_spec_builder_backhaul_wss_tls_certs():
    """Verify Backhaul WSS generates self-signed TLS cert & key with SANs and sets client insecure."""
    tunnel = DummyTunnel(
        spec={
            "transport_type": "wss",
            "control_port": 8443,
            "ports": [80, 443],
            "token": "wss-token",
            "custom_sni": "my-domain.com"
        }
    )
    server_spec, client_spec = build_backhaul_node_specs(tunnel, iran_node_ip="192.168.1.100", foreign_node_ip="10.0.0.2")

    assert server_spec["transport"] == "wss"
    assert "tls_cert_pem" in server_spec
    assert "tls_key_pem" in server_spec
    assert "BEGIN CERTIFICATE" in server_spec["tls_cert_pem"]
    assert "PRIVATE KEY" in server_spec["tls_key_pem"]
    assert client_spec["insecure"] is True
    assert client_spec["custom_sni"] == "my-domain.com"


@pytest.mark.asyncio
async def test_backhaul_adapter_server_apply_and_cert_files(tmp_path, monkeypatch):
    """Verify BackhaulAdapter writes TLS cert and key files in server mode and sets permissions."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))

    spec = {
        "mode": "server",
        "bind_addr": "0.0.0.0:8443",
        "control_port": 8443,
        "transport": "wss",
        "token": "test-secret-token",
        "tls_cert_pem": "-----BEGIN CERTIFICATE-----\nMIIDFakeCert\n-----END CERTIFICATE-----",
        "tls_key_pem": "-----BEGIN PRIVATE KEY-----\nMIIEFakeKey\n-----END PRIVATE KEY-----",
        "ports": ["8080=127.0.0.1:8080"],
        "accept_udp": True
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock) as mock_free, \
         patch("node.app.core_adapters.is_port_listening_locally", return_value=True):
        dummy_proc = MagicMock()
        dummy_proc.pid = 9876
        dummy_proc.returncode = None
        mock_proc.return_value = dummy_proc

        tunnel_id = "tun-cert-test"
        await adapter.apply(tunnel_id, spec)

        # Batch free_ports called once for control port (8443) and service port (8080)
        assert mock_free.call_count == 1
        freed = mock_free.call_args[0][0]
        assert 8443 in freed
        assert 8080 in freed

        cert_file = tmp_path / f"{tunnel_id}_cert.pem"
        key_file = tmp_path / f"{tunnel_id}_key.pem"
        assert cert_file.exists()
        assert key_file.exists()
        assert "MIIDFakeCert" in cert_file.read_text(encoding="utf-8")
        assert "MIIEFakeKey" in key_file.read_text(encoding="utf-8")

        config_file = tmp_path / f"{tunnel_id}.toml"
        assert config_file.exists()
        cfg_text = config_file.read_text(encoding="utf-8")
        # wss does not support accept_udp upstream; must not be written
        assert "accept_udp" not in cfg_text
        assert str(cert_file) in cfg_text or f"{tunnel_id}_cert.pem" in cfg_text

        # Test cleanup on remove
        await adapter.remove(tunnel_id)
        assert not cert_file.exists()
        assert not key_file.exists()


@pytest.mark.asyncio
async def test_backhaul_adapter_batch_port_freeing(tmp_path, monkeypatch):
    """Verify batch port freeing calls free_ports once with full port list in a single batch."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))

    spec = {
        "mode": "server",
        "bind_addr": "0.0.0.0:3080",
        "control_port": 3080,
        "transport": "tcp",
        "token": "tok",
        "ports": ["27000-27010", "8080", "9090"]
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock) as mock_free, \
         patch("node.app.core_adapters.is_port_listening_locally", return_value=True):
        dummy_proc = MagicMock()
        dummy_proc.pid = 1111
        dummy_proc.returncode = None
        mock_proc.return_value = dummy_proc

        tunnel_id = "tun-batch-free"
        await adapter.apply(tunnel_id, spec)

        # Single batch free_ports call for all ports (control + services)
        assert mock_free.call_count == 1
        service_ports_freed = mock_free.call_args[0][0]
        # Must include control port 3080, all ports in 27000-27010, plus 8080 and 9090
        assert 3080 in service_ports_freed
        assert 27000 in service_ports_freed
        assert 27010 in service_ports_freed
        assert 8080 in service_ports_freed
        assert 9090 in service_ports_freed
        assert len(service_ports_freed) == 14


@pytest.mark.asyncio
async def test_backhaul_adapter_cancellation_and_error_cleanup(tmp_path, monkeypatch):
    """Verify that if startup verification fails or is cancelled, remove is called to kill process."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))

    spec = {
        "mode": "client",
        "remote_addr": "1.1.1.1:3080",
        "transport": "tcp",
        "token": "tok"
    }

    dummy_proc = MagicMock()
    dummy_proc.pid = 5555
    dummy_proc.returncode = None
    dummy_proc.kill = MagicMock()

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock, return_value=dummy_proc), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):

        # Mock is_port_listening_locally to always return False to trigger startup verification timeout
        with patch("node.app.core_adapters.is_port_listening_locally", return_value=False):
            with patch.object(adapter, "remove", new_callable=AsyncMock) as mock_remove:
                # Set returncode to simulate process death on startup
                dummy_proc.returncode = 1
                tunnel_id = "tun-fail-startup"
                with pytest.raises(RuntimeError):
                    await adapter.apply(tunnel_id, spec)

                assert mock_remove.call_count >= 1
                mock_remove.assert_called_with(tunnel_id)


@pytest.mark.asyncio
async def test_adapter_manager_force_restart_bypass(tmp_path):
    """Verify force_restart = True bypasses the idempotency cache and restarts the tunnel."""
    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"

    adapter_mock = MagicMock()
    adapter_mock.apply = AsyncMock()
    adapter_mock.status.return_value = {"active": True, "process_running": True}
    manager.adapters["backhaul"] = adapter_mock

    tunnel_id = "tun-force-restart"
    spec = {"mode": "client", "transport": "tcp", "token": "abc"}

    # First apply
    await manager.apply_tunnel(tunnel_id, "backhaul", spec)
    assert adapter_mock.apply.call_count == 1

    # Second apply with exact same spec and force_restart=False -> should skip
    await manager.apply_tunnel(tunnel_id, "backhaul", spec)
    assert adapter_mock.apply.call_count == 1  # No new apply call

    # Third apply with force_restart=True -> must execute apply
    force_spec = dict(spec)
    force_spec["force_restart"] = True
    await manager.apply_tunnel(tunnel_id, "backhaul", force_spec)
    assert adapter_mock.apply.call_count == 2


def test_extract_all_tunnel_ports_with_string_mappings():
    """Verify panel's extract_all_tunnel_ports handles string mappings and port ranges."""
    spec = {
        "control_port": 3080,
        "ports": [
            "8080=127.0.0.1:8080",
            "27000-27005",
            9090
        ]
    }
    extracted = extract_all_tunnel_ports(spec)
    all_ports = extracted["all_ports"]
    assert 3080 in all_ports
    assert 8080 in all_ports
    assert 9090 in all_ports
    for p in range(27000, 27006):
        assert p in all_ports


def test_tunnel_verify_accepts_string_port_mappings():
    """Verify node agent TunnelVerify Pydantic model parses string mappings and ranges without 422."""
    tv = TunnelVerify(
        tunnel_id="tun-verify-test",
        core="backhaul",
        mode="server",
        ports=["8080=127.0.0.1:8080", "27000-27005", 9090],
        control_port=3080
    )
    assert len(tv.ports) == 3
    assert tv.ports[0] == "8080=127.0.0.1:8080"


@pytest.mark.asyncio
async def test_backhaul_log_rotation_on_apply(tmp_path, monkeypatch):
    """Verify BackhaulAdapter truncates log file when it exceeds 5MB."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))

    tunnel_id = "tun-log-test"
    log_file = tmp_path / f"backhaul_{tunnel_id}.log"
    # Create a >5MB file
    log_file.write_bytes(b"A" * (5 * 1024 * 1024 + 100))
    assert log_file.stat().st_size > 5 * 1024 * 1024

    spec = {
        "mode": "client",
        "remote_addr": "1.1.1.1:3080",
        "transport": "tcp",
        "token": "tok"
    }

    dummy_proc = MagicMock()
    dummy_proc.pid = 7777
    dummy_proc.returncode = None

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock, return_value=dummy_proc), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.is_port_listening_locally", return_value=True):
        await adapter.apply(tunnel_id, spec)

        # After apply, the log file should have been truncated (opened with "w")
        assert log_file.stat().st_size < 1000


@pytest.mark.asyncio
async def test_backhaul_adapter_client_config_is_upstream_valid(tmp_path, monkeypatch):
    """Client TOML must not contain keys absent from upstream ClientConfig and must honour false/large values."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))
    spec = {
        "mode": "client",
        "remote_addr": "1.1.1.1:3080",
        "transport": "tcp",
        "token": "tok",
        "accept_udp": True,
        "client_options": {
            "keepalive_period": 75, "heartbeat": 40, "channel_size": 4096,
            "insecure": True, "nodelay": False, "aggressive_pool": False,
        },
    }
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        dummy = MagicMock()
        dummy.pid = 1234
        dummy.returncode = None
        mock_proc.return_value = dummy
        await adapter.apply("tun-client-test", spec)
    txt = (tmp_path / "tun-client-test.toml").read_text(encoding="utf-8")
    assert "keepalive_period = 75" in txt
    assert "nodelay = false" in txt
    assert "aggressive_pool = false" in txt
    for bad in ("accept_udp", "heartbeat", "channel_size", "insecure"):
        assert bad not in txt


@pytest.mark.asyncio
async def test_backhaul_adapter_defaults_high_throughput_buffers(tmp_path, monkeypatch):
    """Verify BackhaulAdapter injects high throughput buffers for multiplexed transports when unspecified."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))
    
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1235, returncode=None)

        # 1. Server mode
        srv_spec = {
            "mode": "server",
            "bind_addr": "0.0.0.0:3080",
            "transport": "tcpmux",
            "token": "tok",
            "ports": [8080]
        }
        await adapter.apply("bh-high-srv", srv_spec)
        srv_txt = (tmp_path / "bh-high-srv.toml").read_text(encoding="utf-8")
        assert "mux_version = 2" in srv_txt
        assert "mux_recievebuffer = 16777216" in srv_txt
        assert "mux_streambuffer = 4194304" in srv_txt

        # 2. Client mode
        cli_spec = {
            "mode": "client",
            "remote_addr": "1.1.1.1:3080",
            "transport": "tcpmux",
            "token": "tok"
        }
        await adapter.apply("bh-high-cli", cli_spec)
        cli_txt = (tmp_path / "bh-high-cli.toml").read_text(encoding="utf-8")
        assert "mux_version = 2" in cli_txt
        assert "mux_recievebuffer = 16777216" in cli_txt
        assert "mux_streambuffer = 4194304" in cli_txt



def test_backhaul_range_mapping_keeps_explicit_target_host():
    """A-B=host:A-B expands 1:1 onto that host instead of collapsing to a native range."""
    from node.app.core_adapters import _normalize_backhaul_ports

    normalized = _normalize_backhaul_ports(
        ["27000-27002=10.0.0.5:27000-27002", "8080"], "127.0.0.1"
    )
    assert normalized == [
        "27000=10.0.0.5:27000",
        "27001=10.0.0.5:27001",
        "27002=10.0.0.5:27002",
        "8080=127.0.0.1:8080",
    ]


def test_backhaul_range_mapping_rejects_mismatched_or_ipv6_targets():
    from node.app.core_adapters import _normalize_backhaul_ports

    with pytest.raises(ValueError):
        _normalize_backhaul_ports(["27000-27002=10.0.0.5:28000-28002"], "127.0.0.1")
    with pytest.raises(ValueError):
        _normalize_backhaul_ports(["27000-27002=[::1]:27000-27002"], "127.0.0.1")


def test_backhaul_native_range_and_single_mapping_normalization():
    from node.app.core_adapters import _normalize_backhaul_ports

    # Default target host keeps the native range spelling Backhaul parses itself.
    assert _normalize_backhaul_ports(["27000-27050"], "127.0.0.1") == ["27000-27050"]
    # A non-default host cannot be expressed natively, so the range is expanded.
    expanded = _normalize_backhaul_ports(["27000-27001"], "10.0.0.5")
    assert expanded == ["27000=10.0.0.5:27000", "27001=10.0.0.5:27001"]
    assert _normalize_backhaul_ports([8080], None) == ["8080=127.0.0.1:8080"]
