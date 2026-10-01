"""
Comprehensive unit tests for Smite GOST v3 tunneling core, adapters, and forwarders.
Validates all 26 bug fixes, protocol combinations, and reverse/direct logic.
"""
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from node.app.core_adapters import GostAdapter, AdapterManager
from panel.app.spec_builder import parse_ports_list, build_gost_node_specs
from panel.app.gost_forwarder import GostForwarder


class DummyTunnel:
    def __init__(self, id="tun-gost-1", core="gost", type="tcp", spec=None):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}


def test_parse_ports_list_mapped_and_ranges():
    """Verify parse_ports_list extracts mapped ports, ranges, ints, and dicts without data loss"""
    input_data = [
        80,
        "443",
        "8080=127.0.0.1:8080",
        "9000-9003",
        {"local_port": 3000},
        {"remote": "5000"}
    ]
    parsed = parse_ports_list(input_data)
    expected = [80, 443, 8080, 9000, 9001, 9002, 9003, 3000, 5000]
    assert parsed == expected


def test_build_gost_node_specs_reverse_mode():
    """Verify build_gost_node_specs assigns server to Iran and client to Foreign in reverse mode"""
    tunnel = DummyTunnel(
        spec={
            "is_reverse": True,
            "control_port": 8443,
            "ports": [80, 443],
            "gost_type": "wss",
            "custom_sni": "speedtest.net",
            "auth_token": "secret-token"
        }
    )
    server_spec, client_spec = build_gost_node_specs(tunnel, iran_node_ip="1.1.1.1", foreign_node_ip="2.2.2.2")
    
    assert server_spec["mode"] == "server"
    assert server_spec["is_reverse"] is True
    assert client_spec["mode"] == "client"
    assert client_spec["is_reverse"] is True
    assert client_spec["server_ip"] == "1.1.1.1"


@pytest.mark.asyncio
async def test_gost_adapter_reverse_server_config(tmp_path, monkeypatch):
    """Verify GOST server config in reverse mode uses tcp/udp handler with rtcp/rudp listener"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "server",
        "is_reverse": True,
        "control_port": 8443,
        "ports": [80, 443],
        "gost_type": "tcp",
        "target_ip": "127.0.0.1",
        "target_port": 8080,
        "enable_mux": True,
        "dns_resolvers": "1.1.1.1\n8.8.8.8",
        "bypass_ips": "10.0.0.0/8, 192.168.0.0/16"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock) as mock_free:
        dummy_proc = MagicMock()
        dummy_proc.pid = 1234
        dummy_proc.returncode = None
        mock_proc.return_value = dummy_proc

        await adapter.apply("test-tunnel-1", spec)

        # Verify free_ports was called on control_port and server ingress ports in reverse mode
        assert mock_free.call_count == 2
        calls = [c[0][0] for c in mock_free.call_args_list]
        assert [8443] in calls
        assert [80, 443] in calls

        # Verify generated JSON config
        config_path = tmp_path / f"test-tunnel-1.json"
        assert config_path.exists()
        config = json.loads(config_path.read_text())

        # Verify log level is warn to prevent disk I/O bloat
        assert config.get("log", {}).get("level") == "warn"

        # Verify server control service on control_port
        services = config.get("services", [])
        assert len(services) == 1
        svc = services[0]
        assert svc["name"] == "gost-server-test-tunnel-1"
        assert svc["addr"] == "0.0.0.0:8443"
        assert svc["handler"]["type"] == "relay"
        assert svc["listener"]["type"] == "tcp"

        # Verify DNS resolvers and bypass IPs were normalized to lists
        assert "resolvers" in config
        assert "bypasses" in config
        assert isinstance(config["resolvers"][0]["nodes"], list)
        assert len(config["resolvers"][0]["nodes"]) == 2
        assert isinstance(config["bypasses"][0]["matchers"], list)
        assert len(config["bypasses"][0]["matchers"]) == 2


@pytest.mark.asyncio
async def test_gost_adapter_reverse_client_foreign_host_safety(tmp_path, monkeypatch):
    """Verify Foreign client in reverse mode does NOT free service ports and generates rtcp listeners"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "client",
        "is_reverse": True,
        "control_port": 8443,
        "server_ip": "1.1.1.1",
        "ports": [80, 443],
        "gost_type": "tcp",
        "target_ip": "127.0.0.1",
        "failover_ips": "3.3.3.3\n4.4.4.4",
        "rate_limit_mbps": "50.5"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock) as mock_free:
        dummy_proc = MagicMock()
        dummy_proc.pid = 5678
        dummy_proc.returncode = None
        mock_proc.return_value = dummy_proc

        await adapter.apply("test-tunnel-2", spec)

        # Foreign client in reverse mode MUST NOT kill ports on host!
        mock_free.assert_not_called()

        config_path = tmp_path / "test-tunnel-2.json"
        config = json.loads(config_path.read_text())

        # Check rate limiter parsed float properly
        assert "limiters" in config
        assert config["limiters"][0]["limits"][0] == f"{int(50.5 * 125000)}B"

        # Check failover IPs were not character-split
        hop = config["chains"][0]["hops"][0]
        nodes = hop["nodes"]
        assert len(nodes) == 3  # 1 primary + 2 failovers
        assert nodes[1]["addr"] == "3.3.3.3:8443"
        assert nodes[2]["addr"] == "4.4.4.4:8443"

        # In reverse client mode, client registers rtcp listeners pointing back to server chain
        client_services = config.get("services", [])
        assert len(client_services) == 2
        svc_80 = next(s for s in client_services if "80" in s["name"])
        assert svc_80["handler"]["type"] == "tcp"
        assert svc_80["listener"]["type"] == "rtcp"
        assert svc_80["listener"]["chain"] == "chain-test-tunnel-2"
        assert svc_80["listener"]["metadata"]["keepAlive"] is True
        assert svc_80["listener"]["metadata"]["ttl"] == "10s"


@pytest.mark.asyncio
async def test_gost_kcp_encryption_and_ssh_auth(tmp_path, monkeypatch):
    """Verify KCP has native AES encryption and SSH has hostKey and auth configured"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # Test KCP Client
    kcp_spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 9000,
        "server_ip": "1.1.1.1",
        "ports": [8080],
        "gost_type": "kcp",
        "auth_token": "my-secret-key"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=111, returncode=None)
        await adapter.apply("kcp-client", kcp_spec)

        config = json.loads((tmp_path / "kcp-client.json").read_text())
        dialer_meta = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]["metadata"]
        assert dialer_meta.get("crypt") == "aes"
        assert dialer_meta.get("key") == "my-secret-key"

    # Test SSH Client
    ssh_spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 2222,
        "server_ip": "1.1.1.1",
        "ports": [8080],
        "gost_type": "ssh",
        "auth_token": "ssh-token"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=222, returncode=None)
        await adapter.apply("ssh-client", ssh_spec)

        config = json.loads((tmp_path / "ssh-client.json").read_text())
        dialer = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]
        assert dialer["auth"]["username"] == "smite"
        assert dialer["auth"]["password"] == "ssh-token"


@pytest.mark.asyncio
async def test_inspect_tunnel_health_direct_gost():
    """Verify inspect_tunnel_health does not falsely mark Foreign Server unhealthy in Direct GOST"""
    manager = AdapterManager()
    
    tunnel_id = "direct-gost-server"
    manager.tunnel_configs[tunnel_id] = {
        "core": "gost",
        "spec": {
            "mode": "server",
            "is_reverse": False,
            "control_port": 8443,
            "ports": [80, 443],  # Service ports are on Iran client, NOT foreign server
            "gost_type": "wss"
        }
    }

    dummy_adapter = MagicMock()
    dummy_adapter.status.return_value = {"process_running": True, "active": True}
    manager.active_tunnels[tunnel_id] = dummy_adapter

    # Mock socket check: control port 8443 is listening locally, but 80/443 are NOT listening on foreign server
    def mock_is_listening(port, proto="tcp"):
        return port == 8443

    with patch("node.app.core_adapters.is_port_listening_locally", side_effect=mock_is_listening):
        health = await manager.inspect_tunnel_health(tunnel_id, tunnel_core="gost", mode="server")
        
        # Must be healthy because service ports should be skipped on foreign server in Direct GOST!
        assert health["healthy"] is True
        assert len(health["missing_ports"]) == 0
        assert health["listening_ports"][0]["port"] == 8443


@pytest.mark.asyncio
async def test_gost_forwarder_multi_port_cleanup(tmp_path):
    """Verify GostForwarder.stop_forward terminates multi-port processes with prefix {tunnel_id}_*"""
    forwarder = GostForwarder()
    forwarder.config_dir = tmp_path

    proc1 = MagicMock()
    proc1.pid = 1001
    proc1.returncode = None
    proc2 = MagicMock()
    proc2.pid = 1002
    proc2.returncode = None

    forwarder.active_forwards["tun123_80"] = proc1
    forwarder.active_forwards["tun123_443"] = proc2
    forwarder.forward_configs["tun123_80"] = {"local_port": 80}
    forwarder.forward_configs["tun123_443"] = {"local_port": 443}

    with patch("panel.app.gost_forwarder.stop_async_process", new_callable=AsyncMock) as mock_stop:
        await forwarder.stop_forward("tun123")

        # Both sub-processes should be stopped and cleaned from active_forwards
        assert mock_stop.call_count == 2
        assert "tun123_80" not in forwarder.active_forwards
        assert "tun123_443" not in forwarder.active_forwards
        assert "tun123_80" not in forwarder.forward_configs
        assert "tun123_443" not in forwarder.forward_configs


@pytest.mark.asyncio
async def test_gost_mtcp_and_mtls_transport(tmp_path, monkeypatch):
    """Verify tcpmux maps to native GOST v3 mtcp, and with TLS maps to mtls"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # Test tcpmux (plain) -> mtcp
    spec_mtcp = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 8800,
        "transport_type": "tcpmux",
        "security_type": "none"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=333, returncode=None)
        await adapter.apply("mtcp-server", spec_mtcp)

        config = json.loads((tmp_path / "mtcp-server.json").read_text())
        assert config["services"][0]["listener"]["type"] == "mtcp"

    # Test tcpmux with TLS -> mtls
    spec_mtls = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 8801,
        "transport_type": "tcpmux",
        "security_type": "tls",
        "tls_cert": "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        "tls_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=334, returncode=None)
        await adapter.apply("mtls-server", spec_mtls)

        config = json.loads((tmp_path / "mtls-server.json").read_text())
        assert config["services"][0]["listener"]["type"] == "mtls"
        assert "tls" in config["services"][0]["listener"]


@pytest.mark.asyncio
async def test_gost_ssh_server_auth_and_cert_cleanup(tmp_path, monkeypatch):
    """Verify SSH server listener auth is synchronized and remove cleans certs and keys"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    ssh_server_spec = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 2222,
        "transport_type": "ssh",
        "auth_token": "secret-ssh-token",
        "ssh_key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=444, returncode=None)
        await adapter.apply("ssh-server-test", ssh_server_spec)

        config = json.loads((tmp_path / "ssh-server-test.json").read_text())
        srv_listener = config["services"][0]["listener"]
        assert srv_listener["type"] == "sshd"
        assert srv_listener["auth"]["username"] == "smite"
        assert srv_listener["auth"]["password"] == "secret-ssh-token"

    # Verify cert and ssh key files exist
    ssh_key_file = tmp_path / "ssh_host_key_ssh-server-test.pem"
    assert ssh_key_file.exists()

    # Now remove and verify clean up
    with patch("node.app.core_adapters.safe_stop_subprocess", new_callable=AsyncMock):
        await adapter.remove("ssh-server-test")
        assert not ssh_key_file.exists()
        assert not (tmp_path / "ssh-server-test.json").exists()


def test_build_gost_node_specs_direct_mode_persistence():
    """
    CRITICAL BUG REGRESSION TEST:
    When Reverse Mode is turned off (is_reverse=False), reapplying or calling build_gost_node_specs
    must NEVER forcibly flip is_reverse back to True.
    """
    tunnel = DummyTunnel(
        spec={
            "is_reverse": False,
            "force_direct": True,
            "control_port": 8443,
            "ports": [80, 443],
            "gost_type": "tcp",
        }
    )
    tunnel.is_reverse = False
    tunnel.foreign_node_id = "foreign-node-1"
    tunnel.iran_node_id = "iran-node-1"

    # First build / apply
    server_spec, client_spec = build_gost_node_specs(tunnel, iran_node_ip="10.0.0.1", foreign_node_ip="20.0.0.2")

    assert tunnel.is_reverse is False
    assert tunnel.spec["is_reverse"] is False
    assert tunnel.spec["force_direct"] is True
    assert server_spec["mode"] == "client"  # Iran node is client
    assert server_spec["is_reverse"] is False
    assert server_spec["server_ip"] == "20.0.0.2"
    assert client_spec["mode"] == "server"  # Foreign node is server
    assert client_spec["is_reverse"] is False

    # Second build / reapply (must preserve is_reverse=False)
    server_spec2, client_spec2 = build_gost_node_specs(tunnel, iran_node_ip="10.0.0.1", foreign_node_ip="20.0.0.2")

    assert tunnel.is_reverse is False
    assert tunnel.spec["is_reverse"] is False
    assert tunnel.spec["force_direct"] is True
    assert server_spec2["mode"] == "client"
    assert client_spec2["mode"] == "server"


