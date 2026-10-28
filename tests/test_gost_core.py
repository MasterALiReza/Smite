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

        # Check rate limiter parsed float properly, in GOST v3 "<scope> <in> <out>" form
        assert "limiters" in config
        rate = int(50.5 * 125000)
        assert config["limiters"][0]["limits"][0] == f"$ {rate}B {rate}B"
        # Reverse tunnels are limited on the relay server; foreign client services are not
        assert all("limiter" not in s for s in config["services"])

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
        assert svc_80["listener"]["metadata"]["ttl"] == "45s"


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
@pytest.mark.parametrize("reverse", [True, False])
async def test_gost_rate_limit_attached_where_enforced(tmp_path, monkeypatch, reverse):
    """Reverse: limiter on the relay server service. Direct: limiter on the Iran-side client services."""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))
    base = {"is_reverse": reverse, "control_port": 8820, "transport_type": "tcp", "security_type": "none",
            "auth_token": "tok", "ports": [8080], "type": "tcp+udp", "rate_limit_mbps": 8}

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=336, returncode=None)
        await adapter.apply("rl-server", dict(base, mode="server"))
        await adapter.apply("rl-client", dict(base, mode="client", server_ip="2.2.2.2"))

    server = json.loads((tmp_path / "rl-server.json").read_text())
    client = json.loads((tmp_path / "rl-client.json").read_text())
    assert server["limiters"][0]["limits"] == ["$ 1000000B 1000000B"]
    relay = server["services"][0]
    client_ports = client["services"]
    assert len(client_ports) == 2
    if reverse:
        assert relay["limiter"] == "limiter-rl-server"
        assert all("limiter" not in s for s in client_ports)
    else:
        assert "limiter" not in relay
        assert all(s["limiter"] == "limiter-rl-client" for s in client_ports)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport,security,expected", [
    ("mws", "none", "ws"),
    ("mws", "tls", "wss"),
    ("mwss", "none", "wss"),
    ("mwss", "tls", "wss"),
])
async def test_gost_mws_mwss_use_ws_with_mux(tmp_path, monkeypatch, transport, security, expected):
    """GOST rc10's mws/mwss pair fails every stream ("websocket: bad MASK"); both ends must use ws/wss + mux"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))
    base = {"is_reverse": False, "control_port": 8810, "transport_type": transport, "security_type": security,
            "auth_token": "tok", "ports": [8080],
            "tls_cert": "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
            "tls_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"}

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=335, returncode=None)
        await adapter.apply("mws-server", dict(base, mode="server"))
        await adapter.apply("mws-client", dict(base, mode="client", server_ip="2.2.2.2"))

    listener = json.loads((tmp_path / "mws-server.json").read_text())["services"][0]["listener"]
    assert listener["type"] == expected
    assert listener["metadata"]["mux"] is True
    dialer = json.loads((tmp_path / "mws-client.json").read_text())["chains"][0]["hops"][-1]["nodes"][0]["dialer"]
    assert dialer["type"] == expected
    assert dialer["metadata"]["mux"] is True
    if expected == "wss":
        assert "tls" in listener and "tls" in dialer


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
        # relay-over-SSH data channel: "ssh" listener (pairs with the client's ssh dialer);
        # "sshd" is GOST's port-forwarding listener and rejects relay streams.
        assert srv_listener["type"] == "ssh"
        assert srv_listener["metadata"]["privateKeyFile"].endswith("ssh_host_key_ssh-server-test.pem")
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


@pytest.mark.asyncio
async def test_gost_kcp_reverse_multiplexing_metadata(tmp_path, monkeypatch):
    """Verify reverse KCP tunnels generate smux metadata on both listener and dialer"""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # 1. Reverse Server (Iran)
    server_spec = {
        "mode": "server",
        "is_reverse": True,
        "control_port": 8443,
        "ports": [8080],
        "gost_type": "kcp",
        "auth_token": "kcp-token-123",
        "multiplex": True
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=333, returncode=None)
        await adapter.apply("kcp-rev-server", server_spec)

        cfg_server = json.loads((tmp_path / "kcp-rev-server.json").read_text())
        svc = cfg_server["services"][0]
        # Listener metadata must have smux enabled
        assert svc["listener"]["metadata"].get("mux") is True
        assert svc["listener"]["metadata"].get("mux.type") == "smux"
        assert svc["listener"]["metadata"].get("bind") is True
        # Handler metadata must have smux enabled
        assert svc["handler"]["metadata"].get("mux") is True
        assert svc["handler"]["metadata"].get("mux.type") == "smux"

    # 2. Reverse Client (Foreign)
    client_spec = {
        "mode": "client",
        "is_reverse": True,
        "control_port": 8443,
        "server_ip": "1.1.1.1",
        "ports": [8080],
        "gost_type": "kcp",
        "auth_token": "kcp-token-123",
        "multiplex": True
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=444, returncode=None)
        await adapter.apply("kcp-rev-client", client_spec)

        cfg_client = json.loads((tmp_path / "kcp-rev-client.json").read_text())
        node0 = cfg_client["chains"][0]["hops"][0]["nodes"][0]
        # Dialer metadata must have smux enabled
        assert node0["dialer"]["metadata"].get("mux") is True
        assert node0["dialer"]["metadata"].get("mux.type") == "smux"
        # Connector metadata must have smux enabled
        assert node0["connector"]["metadata"].get("mux") is True
        assert node0["connector"]["metadata"].get("mux.type") == "smux"


# ==============================================================================
# Comprehensive GOST v3 Tests (Task 4)
# ==============================================================================

@pytest.mark.asyncio
async def test_gost_custom_host_not_overwritten_by_sni(tmp_path, monkeypatch):
    """
    Verify Bug 3 fix: When both Custom Host and Custom SNI are provided,
    dialer_metadata['host'] preserves Custom Host and is NOT overwritten by SNI.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 8443,
        "server_ip": "1.2.3.4",
        "ports": [8080],
        "transport_type": "ws",
        "security_type": "tls",
        "custom_host": "my-worker.cloudflare.com",
        "custom_sni": "fronting-domain.com",
        "tls_cert": "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        "tls_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=555, returncode=None)
        await adapter.apply("sni-host-test", spec)

        config = json.loads((tmp_path / "sni-host-test.json").read_text())
        dialer = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]

        # Custom Host must be preserved in dialer metadata['host']
        assert dialer["metadata"]["host"] == "my-worker.cloudflare.com"
        # SNI must be set in dialer tls['serverName']
        assert dialer["tls"]["serverName"] == "fronting-domain.com"


def test_build_gost_node_specs_remote_ip_translation():
    """
    Verify Bug 2 fix: Remote IP / Target Host handling in spec_builder.
    - If target equals foreign server's public IP (legacy behavior), translate to 127.0.0.1.
    - If target is a custom backend IP (e.g. 10.0.0.5), preserve it.
    """
    # 1. Legacy case: remote_ip matches foreign_node_ip
    tunnel_legacy = DummyTunnel(
        spec={
            "control_port": 8443,
            "remote_ip": "203.0.113.50",
            "ports": [8080],
        }
    )
    srv_spec, cli_spec = build_gost_node_specs(
        tunnel_legacy,
        iran_node_ip="198.51.100.10",
        foreign_node_ip="203.0.113.50"
    )
    assert srv_spec["target_host"] == "127.0.0.1"
    assert cli_spec["target_host"] == "127.0.0.1"

    # 2. Custom internal backend IP
    tunnel_custom = DummyTunnel(
        spec={
            "control_port": 8443,
            "remote_ip": "10.10.0.99",
            "ports": [8080],
        }
    )
    srv_spec2, cli_spec2 = build_gost_node_specs(
        tunnel_custom,
        iran_node_ip="198.51.100.10",
        foreign_node_ip="203.0.113.50"
    )
    assert srv_spec2["target_host"] == "10.10.0.99"
    assert cli_spec2["target_host"] == "10.10.0.99"


def test_build_gost_node_specs_legacy_types():
    """
    Verify Bug 1 fix: Legacy tunnel types (grpc, tcpmux) are properly mapped in spec_builder.
    """
    # Legacy type = "grpc"
    tunnel_grpc = DummyTunnel(
        type="grpc",
        spec={"control_port": 8443, "ports": [8080]}
    )
    srv_spec, _ = build_gost_node_specs(tunnel_grpc, "1.1.1.1", "2.2.2.2")
    assert srv_spec["type"] == "tcp"
    assert srv_spec["transport_type"] == "grpc"

    # Legacy type = "tcpmux"
    tunnel_mux = DummyTunnel(
        type="tcpmux",
        spec={"control_port": 8443, "ports": [8080]}
    )
    srv_spec_mux, _ = build_gost_node_specs(tunnel_mux, "1.1.1.1", "2.2.2.2")
    assert srv_spec_mux["type"] == "tcp"
    assert srv_spec_mux.get("multiplex") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("port_count,expected_buffer_size", [
    (3, 4194304),     # <= 5 ports -> 4 MB
    (10, 1048576),    # 6-20 ports -> 1 MB
    (25, 262144),     # > 20 ports -> 256 KB
])
async def test_gost_dynamic_udp_buffer_sizing(tmp_path, monkeypatch, port_count, expected_buffer_size):
    """
    Verify dynamic UDP buffer sizing prevents memory exhaustion on multi-port tunnels.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    ports = [7000 + i for i in range(port_count)]
    spec = {
        "mode": "client",
        "is_reverse": True,
        "control_port": 8443,
        "server_ip": "1.1.1.1",
        "ports": ports,
        "type": "udp",
        "transport_type": "tcp",
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=666, returncode=None)
        await adapter.apply(f"udp-buf-{port_count}", spec)

        config = json.loads((tmp_path / f"udp-buf-{port_count}.json").read_text())
        services = config["services"]
        assert len(services) == port_count
        first_svc = services[0]
        assert first_svc["listener"]["type"] == "rudp"
        assert first_svc["listener"]["metadata"]["ttl"] == "45s"
        assert first_svc["listener"]["metadata"]["readBufferSize"] == expected_buffer_size


@pytest.mark.asyncio
async def test_gost_grpc_insecure_vs_tls(tmp_path, monkeypatch):
    """
    Verify gRPC with security_type='none' enables insecure mode (plaintext HTTP/2),
    while security_type='tls' configures TLS and does not force insecure.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # 1. gRPC Insecure (none)
    spec_insecure = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 9001,
        "ports": [8080],
        "transport_type": "grpc",
        "security_type": "none"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=771, returncode=None)
        await adapter.apply("grpc-insecure", spec_insecure)

        cfg_insecure = json.loads((tmp_path / "grpc-insecure.json").read_text())
        listener = cfg_insecure["services"][0]["listener"]
        assert listener["type"] == "grpc"
        assert listener["metadata"]["insecure"] is True
        assert "tls" not in listener

    # 2. gRPC with TLS
    spec_tls = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 9002,
        "ports": [8080],
        "transport_type": "grpc",
        "security_type": "tls",
        "tls_cert": "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        "tls_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=772, returncode=None)
        await adapter.apply("grpc-tls", spec_tls)

        cfg_tls = json.loads((tmp_path / "grpc-tls.json").read_text())
        listener_tls = cfg_tls["services"][0]["listener"]
        assert listener_tls["type"] == "grpc"
        assert listener_tls["metadata"].get("insecure") is not True
        assert "tls" in listener_tls


@pytest.mark.asyncio
async def test_gost_v3_mux_metadata_keys(tmp_path, monkeypatch):
    """
    Verify GOST v3 standard mux metadata keys:
    - mux.version: 2
    - mux.keepaliveDisabled: False
    - mux.keepaliveInterval, mux.keepaliveTimeout
    - mux.maxFrameSize (gaming 4096 vs normal 32768)
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # Normal mode
    spec_normal = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 8888,
        "ports": [8080],
        "transport_type": "mws",
        "security_type": "none",
        "gaming_mode": False,
        "keepalive_interval": 15
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=881, returncode=None)
        await adapter.apply("mux-normal", spec_normal)

        cfg = json.loads((tmp_path / "mux-normal.json").read_text())
        meta = cfg["services"][0]["listener"]["metadata"]
        assert meta["mux.version"] == 2
        assert meta["mux.keepaliveDisabled"] is False
        assert meta["mux.keepaliveInterval"] == "15s"
        assert meta["mux.keepaliveTimeout"] == "45s"
        assert meta["mux.maxFrameSize"] == 32768
        assert meta["mux.maxReceiveBuffer"] == 33554432
        assert meta["mux.maxStreamBuffer"] == 8388608

    # Gaming mode
    spec_gaming = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 8889,
        "ports": [8080],
        "transport_type": "mws",
        "security_type": "none",
        "gaming_mode": True,
        "keepalive_interval": 10
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=882, returncode=None)
        await adapter.apply("mux-gaming", spec_gaming)

        cfg_g = json.loads((tmp_path / "mux-gaming.json").read_text())
        meta_g = cfg_g["services"][0]["listener"]["metadata"]
        assert meta_g["mux.version"] == 2
        assert meta_g["mux.keepaliveDisabled"] is False
        assert meta_g["mux.keepaliveInterval"] == "10s"
        assert meta_g["mux.keepaliveTimeout"] == "30s"
        assert meta_g["mux.maxFrameSize"] == 4096
        assert meta_g["mux.maxReceiveBuffer"] == 33554432
        assert meta_g["mux.maxStreamBuffer"] == 8388608


@pytest.mark.asyncio
async def test_gost_utls_security_falls_back_to_standard_tls(tmp_path, monkeypatch):
    """
    The pinned GOST core has no uTLS dialer: a legacy 'utls' security type must
    render standard TLS metadata (no fake fingerprint) and keep the SNI.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 8443,
        "server_ip": "1.2.3.4",
        "ports": [8080],
        "transport_type": "tcp",
        "security_type": "utls",
        "utls_fingerprint": "firefox",
        "custom_sni": "example.com"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=556, returncode=None)
        await adapter.apply("utls-test", spec)

        config = json.loads((tmp_path / "utls-test.json").read_text())
        dialer = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]

        assert "tls" in dialer
        assert dialer["tls"]["serverName"] == "example.com"
        assert "utls" not in dialer["tls"]


@pytest.mark.asyncio
async def test_gost_wss_client_verifies_distributed_ca(tmp_path, monkeypatch):
    """A tunnel that terminates TLS server-side must verify against the panel CA."""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 8443,
        "server_ip": "1.2.3.4",
        "ports": [8080],
        "transport_type": "wss",
        "security_type": "tls",
        "tls_ca_cert_pem": "-----BEGIN CERTIFICATE-----\ntest-ca\n-----END CERTIFICATE-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=557, returncode=None)
        await adapter.apply("wss-ca-test", spec)

        config = json.loads((tmp_path / "wss-ca-test.json").read_text())
        dialer = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]

        assert dialer["tls"]["secure"] is True
        assert dialer["tls"]["serverName"] == "1.2.3.4"
        assert dialer["tls"]["caFile"].endswith("gost_wss-ca-test_ca.pem")


@pytest.mark.asyncio
async def test_gost_kcp_ignores_tunnel_ca_without_tls_listener(tmp_path, monkeypatch):
    """KCP has its own crypt and no TLS listener: a tunnel CA must never be attached or written."""
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec = {
        "mode": "client",
        "is_reverse": False,
        "control_port": 8443,
        "server_ip": "1.2.3.4",
        "ports": [8080],
        "transport_type": "kcp",
        "security_type": "tls",
        "tls_ca_cert_pem": "-----BEGIN CERTIFICATE-----\ntest-ca\n-----END CERTIFICATE-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=558, returncode=None)
        await adapter.apply("kcp-no-tls-test", spec)

        config = json.loads((tmp_path / "kcp-no-tls-test.json").read_text())
        dialer = config["chains"][0]["hops"][0]["nodes"][0]["dialer"]

        assert "tls" not in dialer
        assert not (tmp_path / "gost_kcp-no-tls-test_ca.pem").exists()
        assert dialer["metadata"]["crypt"] == "aes"


@pytest.mark.asyncio
async def test_gost_adapter_status_port_health(tmp_path, monkeypatch):
    """
    Verify GostAdapter.status checks port health in both server mode (control_port)
    and direct client mode (service ports).
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    # 1. Server mode
    srv_spec = {
        "mode": "server",
        "is_reverse": False,
        "control_port": 9100,
        "ports": [8080],
        "transport_type": "tcp",
    }
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1001, returncode=None)
        await adapter.apply("test-status-srv", srv_spec)

    with patch("node.app.core_adapters.is_port_listening_locally", return_value=True):
        st = adapter.status("test-status-srv")
        assert st["active"] is True
        assert st["process_running"] is True
        assert st["config_exists"] is True
        assert st["port_healthy"] is True

    with patch("node.app.core_adapters.is_port_listening_locally", return_value=False):
        st_bad = adapter.status("test-status-srv")
        assert st_bad["port_healthy"] is False

    # 2. Client direct mode (checks local service ports)
    cli_spec = {
        "mode": "client",
        "is_reverse": False,
        "server_ip": "1.2.3.4",
        "control_port": 9100,
        "ports": [8080],
        "transport_type": "tcp",
    }
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1002, returncode=None)
        await adapter.apply("test-status-cli", cli_spec)

    with patch("node.app.core_adapters.is_port_listening_locally", return_value=True):
        st_cli = adapter.status("test-status-cli")
        assert st_cli["port_healthy"] is True

    with patch("node.app.core_adapters.is_port_listening_locally", return_value=False):
        st_cli_bad = adapter.status("test-status-cli")
        assert st_cli_bad["port_healthy"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["tcp", "ws", "wss", "mws", "mwss", "quic", "grpc", "kcp", "ssh"])
@pytest.mark.parametrize("tunnel_type", ["tcp", "udp", "tcp+udp"])
@pytest.mark.parametrize("security", ["none", "tls", "utls"])
@pytest.mark.parametrize("is_reverse", [True, False])
async def test_gost_all_transports_types_and_directions(tmp_path, monkeypatch, transport, tunnel_type, security, is_reverse):
    """
    Parametric matrix test covering:
    - 9 transports: tcp, ws, wss, mws, mwss, quic, grpc, kcp, ssh
    - 3 tunnel types: tcp, udp, tcp+udp
    - 3 security modes: none, tls, utls
    - 2 modes: Reverse vs Direct
    Validates complete and valid GOST v3 JSON config generation for both server and client (162 combinations).
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    spec_base = {
        "control_port": 9500,
        "ports": [8080],
        "transport_type": transport,
        "type": tunnel_type,
        "security_type": security,
        "is_reverse": is_reverse,
        "auth_token": "matrix-token",
        "tls_cert": "-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----",
        "tls_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----",
        "ssh_key_pem": "-----BEGIN OPENSSH PRIVATE KEY-----\ntest\n-----END OPENSSH PRIVATE KEY-----"
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=999, returncode=None)

        # 1. Apply Server Spec
        server_spec = dict(spec_base, mode="server")
        if not is_reverse:
            server_spec["target_ip"] = "127.0.0.1"
        srv_tid = f"m-{transport}-{tunnel_type}-{security}-{'rev' if is_reverse else 'dir'}-srv"
        await adapter.apply(srv_tid, server_spec)

        srv_cfg = json.loads((tmp_path / f"{srv_tid}.json").read_text())
        assert len(srv_cfg["services"]) >= 1

        # 2. Apply Client Spec
        client_spec = dict(spec_base, mode="client", server_ip="1.1.1.1")
        if is_reverse:
            client_spec["target_ip"] = "127.0.0.1"
        cli_tid = f"m-{transport}-{tunnel_type}-{security}-{'rev' if is_reverse else 'dir'}-cli"
        await adapter.apply(cli_tid, client_spec)

        cli_cfg = json.loads((tmp_path / f"{cli_tid}.json").read_text())
        assert len(cli_cfg["chains"]) == 1

        # Client services handle port forwarding in both reverse and direct modes
        if tunnel_type == "tcp+udp":
            assert len(cli_cfg["services"]) == 2
        elif tunnel_type == "udp":
            assert any("udp" in s["name"] for s in cli_cfg["services"])
        else:
            assert any("tcp" in s["name"] for s in cli_cfg["services"])


@pytest.mark.asyncio
async def test_gost_reverse_mode_no_double_smux(tmp_path, monkeypatch):
    """
    Verify GOST reverse tunnels without explicit multiplexing do NOT enable
    an outer layer of Yamux/SMUX (avoiding nested double-SMUX over relay).
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1001, returncode=None)

        # 1. Reverse Server (Iran node)
        srv_spec = {
            "mode": "server",
            "is_reverse": True,
            "control_port": 25001,
            "ports": [8080],
            "transport_type": "tcp",
            "security_type": "none",
        }
        await adapter.apply("gost-rev-clean-srv", srv_spec)
        srv_cfg = json.loads((tmp_path / "gost-rev-clean-srv.json").read_text())
        listener_meta = srv_cfg["services"][0]["listener"]["metadata"]
        assert listener_meta.get("mux") is None, "Server listener must not wrap transport in Yamux"
        assert listener_meta.get("mux.type") is None

        # 2. Reverse Client (Foreign node)
        cli_spec = {
            "mode": "client",
            "is_reverse": True,
            "server_ip": "1.1.1.1",
            "control_port": 25001,
            "ports": [8080],
            "transport_type": "tcp",
            "security_type": "none",
        }
        await adapter.apply("gost-rev-clean-cli", cli_spec)
        cli_cfg = json.loads((tmp_path / "gost-rev-clean-cli.json").read_text())
        node = cli_cfg["chains"][0]["hops"][-1]["nodes"][0]
        dialer_meta = node["dialer"].get("metadata", {})
        connector_meta = node["connector"].get("metadata", {})
        assert dialer_meta.get("mux") is None, "Client dialer must not wrap transport in Yamux"
        assert connector_meta.get("mux") is None, "Client connector must not wrap transport in Yamux"


@pytest.mark.asyncio
async def test_gost_reverse_mode_explicit_mux_enabled(tmp_path, monkeypatch):
    """
    Verify GOST reverse tunnels WITH explicit multiplex: True or enable_mux: True
    DO configure mux metadata.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1002, returncode=None)

        cli_spec = {
            "mode": "client",
            "is_reverse": True,
            "server_ip": "1.1.1.1",
            "control_port": 25002,
            "ports": [8080],
            "transport_type": "tcp",
            "security_type": "none",
            "multiplex": True,
        }
        await adapter.apply("gost-rev-mux-cli", cli_spec)
        cli_cfg = json.loads((tmp_path / "gost-rev-mux-cli.json").read_text())
        node = cli_cfg["chains"][0]["hops"][-1]["nodes"][0]
        assert node["dialer"]["metadata"]["mux"] is True
        assert node["connector"]["metadata"]["mux"] is True
        assert node["connector"]["metadata"]["mux.maxReceiveBuffer"] == 33554432
        assert node["connector"]["metadata"]["mux.maxStreamBuffer"] == 8388608
        assert node["connector"]["metadata"]["mux.maxFrameSize"] == 32768


@pytest.mark.asyncio
async def test_gost_anti_packetloss_frame_size_32k(tmp_path, monkeypatch):
    """
    Verify anti-packet-loss preset (WSS + TLS, non-gaming) preserves 32KB mux.maxFrameSize.
    """
    adapter = GostAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/gost"))

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=1003, returncode=None)

        # Anti-packet-loss preset: WSS + TLS with gaming_mode=False
        spec = {
            "mode": "server",
            "is_reverse": False,
            "control_port": 8443,
            "ports": [8080],
            "transport_type": "wss",
            "security_type": "tls",
            "gaming_mode": False,
            "enable_mux": True,
        }
        await adapter.apply("gost-anti-loss", spec)
        cfg = json.loads((tmp_path / "gost-anti-loss.json").read_text())
        meta = cfg["services"][0]["listener"]["metadata"]
        assert meta["mux.maxFrameSize"] == 32768
        assert meta["mux.maxReceiveBuffer"] == 33554432
        assert meta["mux.maxStreamBuffer"] == 8388608





