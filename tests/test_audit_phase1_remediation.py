import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from cryptography import x509

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from panel.app.spec_builder import build_frp_node_specs
from node.app.core_adapters import (
    FrpAdapter,
    ChiselAdapter,
    BackhaulAdapter,
    RatholeAdapter,
    AdapterManager,
    _get_protected_tunnel_ids,
)
from panel.app.tunnel_reapply_manager import TunnelReapplyManager


class DummyTunnel:
    def __init__(self, id, core="frp", type="tcp", spec=None, is_reverse=True, **kwargs):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = is_reverse
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# 1. FRP Direct Mode Provider TLS & Protocol Alignment
# ---------------------------------------------------------------------------

def test_frp_direct_mode_cert_includes_loopback_sans():
    """Verify generated FRP server certificate includes loopback SANs (127.0.0.1, ::1, localhost)."""
    tunnel = DummyTunnel(
        id="tun-san-check",
        core="frp",
        type="tcp",
        is_reverse=False,
        spec={"ports": [8443], "bind_port": 7000, "security_type": "tls"}
    )
    iran_ip = "194.5.200.10"
    foreign_ip = "185.10.50.20"

    _, foreign_spec = build_frp_node_specs(tunnel, iran_ip, foreign_ip)
    cert_pem = foreign_spec["tls_cert_pem"]
    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    san_ext = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    san_values = [str(name.value) for name in san_ext.value]

    assert "127.0.0.1" in san_values
    assert "::1" in san_values
    assert "localhost" in san_values


@pytest.mark.asyncio
async def test_frp_provider_tls_and_port_range_expansion(tmp_path, monkeypatch):
    """Verify frpc provider config gets transport.tls, protocol match, and port range expansion."""
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/frpc"))
    monkeypatch.setattr(adapter, "_resolve_server_binary_path", lambda: Path("/bin/frps"))

    foreign_spec = {
        "mode": "server",
        "is_provider": True,
        "is_visitor": False,
        "is_reverse": False,
        "bind_port": 7000,
        "transport_type": "wss",
        "security_type": "tls",
        "token": "tok-test",
        "secret_key": "sec-test",
        "ports": [8080],
        "port_ranges": ["9000-9003"],
        "tls_cert_pem": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----",
        "tls_key_pem": "-----BEGIN RSA PRIVATE KEY-----\nMOCK\n-----END RSA PRIVATE KEY-----",
    }
    tunnel_id = "tun-frp-prov"

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        dummy_p = MagicMock(pid=1001, returncode=None, poll=MagicMock(return_value=None))
        mock_proc.return_value = dummy_p

        await adapter.apply(tunnel_id, foreign_spec)

        provider_cfg_path = tmp_path / f"frpc_{tunnel_id}_provider.yaml"
        assert provider_cfg_path.exists()
        provider_content = provider_cfg_path.read_text()

        # Check protocol alignment
        assert 'protocol: "wss"' in provider_content
        # Check TLS section
        assert "enable: true" in provider_content
        assert "disableCustomTLSFirstByte: true" in provider_content
        assert "trustedCaFile:" in provider_content

        # Check port ranges expansion (8080 + 9000..9003 = 5 proxies)
        assert "localPort: 8080" in provider_content
        assert "localPort: 9000" in provider_content
        assert "localPort: 9001" in provider_content
        assert "localPort: 9002" in provider_content
        assert "localPort: 9003" in provider_content

        await adapter.remove(tunnel_id)


# ---------------------------------------------------------------------------
# 2. Chisel Port Tracking and Credential Protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chisel_ports_metadata_and_logless_teardown(tmp_path, monkeypatch):
    """Verify ChiselAdapter writes ports metadata and frees ports even when logs are rotated/lost."""
    adapter = ChiselAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/chisel"))

    freed_ports = set()
    async def fake_free(ports):
        freed_ports.update(ports)

    monkeypatch.setattr("node.app.core_adapters.free_ports", fake_free)
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", AsyncMock())

    server_spec = {
        "mode": "server",
        "control_port": 19000,
        "ports": [8080, 8081],
        "is_reverse": True,
        "token": "tok123",
    }
    tunnel_id = "tun-chisel-meta"

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc:
        mock_proc.return_value = MagicMock(pid=2001, returncode=None, poll=MagicMock(return_value=None))

        await adapter.apply(tunnel_id, server_spec)

        ports_file = tmp_path / f"{tunnel_id}_ports.json"
        assert ports_file.exists()
        ports_data = json.loads(ports_file.read_text())
        assert 19000 in ports_data
        assert 8080 in ports_data
        assert 8081 in ports_data

        # Simulate log rotation/truncation
        log_file = tmp_path / f"{tunnel_id}.log"
        if log_file.exists():
            log_file.write_text("")

        freed_ports.clear()
        await adapter.remove(tunnel_id, purge=True)

        assert 19000 in freed_ports
        assert 8080 in freed_ports
        assert 8081 in freed_ports
        assert not ports_file.exists()


@pytest.mark.asyncio
async def test_chisel_credentials_in_env_omits_cli_flags(tmp_path, monkeypatch):
    """Verify that when credentials_in_env is requested, credentials are omitted from CLI args."""
    adapter = ChiselAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/chisel"))

    captured = {}
    async def fake_spawn(cmd, stdout=None, stderr=None, env=None):
        captured["cmd"] = cmd
        captured["env"] = env
        return MagicMock(pid=3001, returncode=None, poll=MagicMock(return_value=None))

    monkeypatch.setattr("node.app.core_adapters._spawn_core_subprocess", fake_spawn)
    monkeypatch.setattr("node.app.core_adapters.free_ports", AsyncMock())
    monkeypatch.setattr("node.app.core_adapters.safe_stop_subprocess", AsyncMock())

    client_spec = {
        "mode": "client",
        "server_url": "http://1.2.3.4:8080",
        "auth": "super-secret-auth",
        "key": "super-secret-key",
        "ports": [80],
        "credentials_in_env": True,
    }
    await adapter.apply("tun-env-cred", client_spec)

    cmd = captured.get("cmd", [])
    env = captured.get("env", {})

    assert "--auth" not in cmd
    assert "--key" not in cmd
    assert env.get("AUTH") == "super-secret-auth"
    assert env.get("KEY") == "super-secret-key"

    await adapter.remove("tun-env-cred", purge=True)


# ---------------------------------------------------------------------------
# 3. Backhaul Fallback Certificate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backhaul_wss_generates_fallback_self_signed_cert(tmp_path, monkeypatch):
    """Verify Backhaul server in WSS mode without PEMs generates a self-signed fallback cert."""
    adapter = BackhaulAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/backhaul"))

    spec = {
        "mode": "server",
        "transport": "wss",
        "bind_port": 25000,
        "token": "backhaul-tok",
        "ports": ["8080=8080"],
    }
    tunnel_id = "tun-backhaul-wss"

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=4001, returncode=None, poll=MagicMock(return_value=None))

        await adapter.apply(tunnel_id, spec)

        cert_file = tmp_path / f"{tunnel_id}_cert.pem"
        key_file = tmp_path / f"{tunnel_id}_key.pem"
        assert cert_file.exists()
        assert key_file.exists()

        toml_file = tmp_path / f"{tunnel_id}.toml"
        assert toml_file.exists()
        toml_content = toml_file.read_text()
        assert "tls_cert" in toml_content
        assert "tls_key" in toml_content
        assert "\\" not in toml_content  # Forward slashes only

        await adapter.remove(tunnel_id, purge=True)


# ---------------------------------------------------------------------------
# 4. Rathole Windows TOML Path Escaping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rathole_toml_renders_posix_paths(tmp_path, monkeypatch):
    """Verify Rathole TOML configuration uses forward slashes on Windows."""
    adapter = RatholeAdapter()
    adapter.config_dir = tmp_path

    client_spec = {
        "mode": "client",
        "remote_addr": "1.2.3.4:23333",
        "transport_type": "wss",
        "token": "tok",
        "ports": [8080],
        "tls_ca_cert_pem": "-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----",
    }
    tunnel_id = "tun-rathole-posix"

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=5001, returncode=None, poll=MagicMock(return_value=None))

        await adapter.apply(tunnel_id, client_spec)

        cfg_file = tmp_path / f"{tunnel_id}.toml"
        assert cfg_file.exists()
        content = cfg_file.read_text()
        assert "trusted_root" in content
        # Backslashes must not appear in path values
        for line in content.splitlines():
            if "trusted_root" in line:
                assert "\\" not in line
                assert "/" in line

        await adapter.remove(tunnel_id)


# ---------------------------------------------------------------------------
# 5. Multi-Node Reapply Compensating Rollback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tunnel_reapply_manager_compensating_rollback():
    """Verify TunnelReapplyManager rolls back first_node if second_node apply fails."""
    manager = TunnelReapplyManager()
    client = MagicMock()

    sent_requests = []
    async def mock_send_to_node(node_id, endpoint, data):
        sent_requests.append((node_id, endpoint, data))
        if endpoint == "/api/agent/tunnels/apply":
            if node_id == "node-first":
                return {"status": "success"}
            elif node_id == "node-second":
                return {"status": "error", "message": "Failed to bind client socket"}
        return {"status": "success"}

    client.send_to_node = AsyncMock(side_effect=mock_send_to_node)

    tunnel = DummyTunnel(
        id="tun-rollback-test",
        core="gost",
        type="tcp",
        is_reverse=True,
        node_id="node-first",
        iran_node_id="node-first",
        foreign_node_id="node-second",
        spec={"ports": [8080], "control_port": 8443}
    )

    first_node = MagicMock(id="node-first", node_metadata={"role": "iran", "ip_address": "10.0.0.1"})
    second_node = MagicMock(id="node-second", node_metadata={"role": "foreign", "ip_address": "10.0.0.2"})

    session = AsyncMock()
    mock_res_iran = MagicMock()
    mock_res_iran.scalar_one_or_none.return_value = first_node
    mock_res_all = MagicMock()
    mock_res_all.scalars.return_value.all.return_value = [first_node, second_node]

    # Return first_node on first query, and all nodes on second query
    session.execute = AsyncMock(side_effect=[mock_res_iran, mock_res_all])

    with patch("app.spec_builder.build_tunnel_node_specs", return_value=({"mode": "server"}, {"mode": "client"})), \
         patch("app.spec_builder.persist_generated_spec", new_callable=AsyncMock), \
         patch("asyncio.sleep", AsyncMock()):
        ok = await manager._reapply_tunnel_safe(tunnel, session, client)

    assert ok is False
    # Check that remove endpoint was called on node-first with purge=True
    rollback_call = next((r for r in sent_requests if r[1] == "/api/agent/tunnels/remove"), None)
    assert rollback_call is not None
    assert rollback_call[0] == "node-first"
    assert rollback_call[2]["tunnel_id"] == "tun-rollback-test"
    assert rollback_call[2]["purge"] is True


# ---------------------------------------------------------------------------
# 6. In-Flight Tunnel Protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_in_flight_tunnel_protected_during_apply(tmp_path, monkeypatch):
    """Verify that an in-flight tunnel is included in _get_protected_tunnel_ids() during apply."""
    manager = AdapterManager()
    manager.config_dir = tmp_path
    manager.tunnels_file = tmp_path / "tunnels.json"

    captured_in_flight_protection = []

    class SlowMockAdapter:
        name = "slow-mock"
        async def apply(self, tunnel_id, spec):
            # Check protected IDs while apply is in flight
            protected = _get_protected_tunnel_ids()
            captured_in_flight_protection.append(tunnel_id in protected)
            await asyncio.sleep(0.01)

        async def remove(self, tunnel_id, purge=False):
            pass

        def status(self, tunnel_id):
            return {"process_running": False}

    monkeypatch.setattr(manager, "get_adapter", lambda core: SlowMockAdapter())

    await manager.apply_tunnel("tun-in-flight-check", "gost", {"dummy": 123})

    assert len(captured_in_flight_protection) == 1
    assert captured_in_flight_protection[0] is True
    # Verify protected IDs still contain tunnel_id immediately after apply completes (active_tunnels set)
    assert "tun-in-flight-check" in _get_protected_tunnel_ids()


# ---------------------------------------------------------------------------
# 7. Additional Deep Edge-Case Verifications
# ---------------------------------------------------------------------------

def test_spec_builder_frp_preserves_port_ranges_in_specs():
    """Verify build_frp_node_specs preserves port_ranges in both direct and reverse specs."""
    tunnel_direct = DummyTunnel(
        id="tun-frp-pr-direct",
        core="frp",
        type="tcp",
        is_reverse=False,
        port_ranges=["9000-9005"],
        spec={"port_ranges": ["9000-9005"]}
    )
    iran_spec, foreign_spec = build_frp_node_specs(tunnel_direct, "1.2.3.4", "5.6.7.8")
    assert foreign_spec.get("port_ranges") == ["9000-9005"]
    assert iran_spec.get("port_ranges") == ["9000-9005"]
    assert any(9000 <= int(p) <= 9005 for p in foreign_spec.get("ports", []))

    tunnel_reverse = DummyTunnel(
        id="tun-frp-pr-reverse",
        core="frp",
        type="tcp",
        is_reverse=True,
        port_ranges=["8000-8002"],
        spec={"port_ranges": ["8000-8002"]}
    )
    server_spec, client_spec = build_frp_node_specs(tunnel_reverse, "1.2.3.4", "5.6.7.8")
    assert server_spec.get("port_ranges") == ["8000-8002"]
    assert client_spec.get("port_ranges") == ["8000-8002"]


def test_spec_builder_chisel_enables_credentials_in_env_and_authfile():
    """Verify build_chisel_node_specs automatically enables credentials_in_env and use_authfile."""
    from panel.app.spec_builder import build_chisel_node_specs
    tunnel = DummyTunnel(
        id="tun-chisel-cred-sec",
        core="chisel",
        type="tcp",
        is_reverse=True,
        spec={"auth": "smite:secret123", "ports": [8080]}
    )
    server_spec, client_spec = build_chisel_node_specs(tunnel, "1.2.3.4", "5.6.7.8")
    assert server_spec.get("credentials_in_env") is True
    assert server_spec.get("use_authfile") is True
    assert client_spec.get("credentials_in_env") is True


def test_ensure_self_signed_cert_loopback_sans_and_ip(tmp_path):
    """Verify _ensure_self_signed_cert_and_key creates certificates with loopback SANs and IPAddress support."""
    from node.app.core_adapters import _ensure_self_signed_cert_and_key
    cert_path = tmp_path / "test_cert.pem"
    key_path = tmp_path / "test_key.pem"

    ok = _ensure_self_signed_cert_and_key(cert_path, key_path, common_name="192.168.1.100")
    assert ok is True
    assert cert_path.exists()
    assert key_path.exists()

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san_ext = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    san_values = [str(name.value) for name in san_ext.value]

    assert "192.168.1.100" in san_values
    assert "127.0.0.1" in san_values
    assert "::1" in san_values
    assert "localhost" in san_values


@pytest.mark.asyncio
async def test_frp_server_fallback_self_signed_cert(tmp_path, monkeypatch):
    """Verify FrpAdapter generates fallback self-signed cert when force_tls is active and PEMs are absent."""
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    monkeypatch.setattr(adapter, "_resolve_binary_path", lambda: Path("/bin/frpc"))
    monkeypatch.setattr(adapter, "_resolve_server_binary_path", lambda: Path("/bin/frps"))

    spec = {
        "mode": "server",
        "bind_port": 7000,
        "force_tls": True,
        "token": "tok123",
        "ports": [8080],
    }
    tunnel_id = "tun-frp-fallback-cert"

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        mock_proc.return_value = MagicMock(pid=6001, returncode=None, poll=MagicMock(return_value=None))

        await adapter.apply(tunnel_id, spec)

        cert_file = tmp_path / f"{tunnel_id}_cert.pem"
        key_file = tmp_path / f"{tunnel_id}_key.pem"
        assert cert_file.exists()
        assert key_file.exists()

        frps_cfg = tmp_path / f"frps_{tunnel_id}.yaml"
        assert frps_cfg.exists()
        cfg_content = frps_cfg.read_text()
        assert "certFile:" in cfg_content
        assert "keyFile:" in cfg_content

        await adapter.remove(tunnel_id)
        assert not cert_file.exists()
        assert not key_file.exists()

