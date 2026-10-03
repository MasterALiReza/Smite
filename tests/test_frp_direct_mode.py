import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from panel.app.spec_builder import (
    build_tunnel_node_specs,
    build_frp_node_specs,
)
from node.app.core_adapters import FrpAdapter, AdapterManager


class DummyTunnel:
    def __init__(self, id, core="frp", type="tcp", spec=None, is_reverse=True, gaming_mode=False, **kwargs):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = is_reverse
        self.gaming_mode = gaming_mode
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_frp_direct_mode_spec_builder():
    """Verify FRP Direct Mode spec generation:
    - Iran Node: client (visitor), connects to Foreign Node IP, binds 0.0.0.0 locally, stripped healthCheck
    - Foreign Node: server (broker + provider), TLS cert SAN with Foreign Node IP, matching secret_key
    """
    tunnel = DummyTunnel(
        id="t-frp-direct-1",
        core="frp",
        type="tcp",
        is_reverse=False,
        spec={
            "ports": [8443, 9443],
            "bind_port": 7000,
            "transport_type": "tcp",
            "security_type": "tls",
            "token": "my-secret-token",
            "custom_sni": "cdn.example.com",
            "enable_health_check": True,
        }
    )
    iran_ip = "194.5.200.10"
    foreign_ip = "185.10.50.20"

    iran_spec, foreign_spec = build_frp_node_specs(tunnel, iran_ip, foreign_ip)

    # 1. Iran Spec (Visitor Client)
    assert iran_spec["mode"] == "client"
    assert iran_spec.get("is_visitor") is True
    assert iran_spec.get("is_provider", False) is False
    assert iran_spec.get("is_reverse") is False
    assert iran_spec["server_addr"] == foreign_ip
    assert iran_spec["server_port"] == 7000
    assert iran_spec["token"] == "my-secret-token"
    assert bool(iran_spec.get("secret_key")) is True
    assert iran_spec["bind_addr"] == "0.0.0.0"
    # Visitor block must not have healthCheck
    assert "healthCheck" not in iran_spec

    # 2. Foreign Spec (Broker + Provider Server)
    assert foreign_spec["mode"] == "server"
    assert foreign_spec.get("is_provider") is True
    assert foreign_spec.get("is_visitor", False) is False
    assert foreign_spec.get("is_reverse") is False
    assert foreign_spec["bind_port"] == 7000
    assert foreign_spec["token"] == "my-secret-token"
    assert foreign_spec["secret_key"] == iran_spec["secret_key"]
    assert foreign_spec["security_type"] == "tls"
    # TLS cert PEM must be present on server
    assert "BEGIN CERTIFICATE" in foreign_spec["tls_cert_pem"]
    assert "BEGIN RSA PRIVATE KEY" in foreign_spec["tls_key_pem"] or "BEGIN PRIVATE KEY" in foreign_spec["tls_key_pem"]


def test_frp_reverse_mode_spec_builder_backward_compatibility():
    """Verify FRP Reverse Mode preserves existing behavior:
    - Iran Node: server (broker)
    - Foreign Node: client (reverse proxies to Iran)
    """
    tunnel = DummyTunnel(
        id="t-frp-rev-1",
        core="frp",
        type="tcp",
        is_reverse=True,
        spec={
            "ports": [8080],
            "bind_port": 7100,
            "token": "rev-token",
            "custom_sni": "iran.example.com",
        }
    )
    iran_ip = "194.5.200.10"
    foreign_ip = "185.10.50.20"

    iran_spec, foreign_spec = build_frp_node_specs(tunnel, iran_ip, foreign_ip)

    # Iran Spec (Reverse Server)
    assert iran_spec["mode"] == "server"
    assert iran_spec.get("is_visitor", False) is False
    assert iran_spec.get("is_provider", False) is False
    assert iran_spec.get("is_reverse") is True
    assert iran_spec["bind_port"] == 7100
    assert "BEGIN CERTIFICATE" in iran_spec["tls_cert_pem"]

    # Foreign Spec (Reverse Client)
    assert foreign_spec["mode"] == "client"
    assert foreign_spec.get("is_visitor", False) is False
    assert foreign_spec.get("is_provider", False) is False
    assert foreign_spec.get("is_reverse") is True
    assert foreign_spec["server_addr"] == iran_ip
    assert foreign_spec["server_port"] == 7100


@pytest.mark.asyncio
async def test_frp_adapter_direct_mode_configs(tmp_path, monkeypatch):
    """Verify FrpAdapter generates correct YAML configs for Direct mode:
    - Foreign Server: frps_{id}.yaml (bindPort) and frpc_{id}_provider.yaml (proxies: type stcp)
    - Iran Client: frpc_{id}.yaml (visitors: type stcp, bindAddr 0.0.0.0, serverName, secretKey)
    """
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
        "token": "tok123",
        "secret_key": "sec999",
        "ports": [8443],
        "security_type": "tls",
        "tls_cert_pem": "MOCK_CERT",
        "tls_key_pem": "MOCK_KEY",
    }

    iran_spec = {
        "mode": "client",
        "is_visitor": True,
        "is_provider": False,
        "is_reverse": False,
        "server_addr": "185.10.50.20",
        "server_port": 7000,
        "token": "tok123",
        "secret_key": "sec999",
        "ports": [8443],
        "bind_addr": "0.0.0.0",
        "security_type": "tls",
    }

    tunnel_id = "test-direct-tun"

    # Test config writing for foreign node (server + provider)
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        dummy_p = MagicMock()
        dummy_p.pid = 1111
        dummy_p.poll.return_value = None
        dummy_p.returncode = None
        mock_proc.return_value = dummy_p

        await adapter.apply(tunnel_id, foreign_spec)

        # Check server config
        frps_cfg_path = tmp_path / f"frps_{tunnel_id}.yaml"
        assert frps_cfg_path.exists()
        frps_content = frps_cfg_path.read_text()
        assert "bindPort: 7000" in frps_content
        assert "auth:" in frps_content
        assert 'token: "tok123"' in frps_content

        # Check provider client config
        provider_cfg_path = tmp_path / f"frpc_{tunnel_id}_provider.yaml"
        assert provider_cfg_path.exists()
        provider_content = provider_cfg_path.read_text()
        assert "proxies:" in provider_content
        assert "type: stcp" in provider_content
        assert 'secretKey: "sec999"' in provider_content

        # Cleanup
        await adapter.remove(tunnel_id)
        assert not frps_cfg_path.exists()
        assert not provider_cfg_path.exists()

    # Test config writing for Iran node (visitor client)
    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_proc, \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock):
        dummy_p = MagicMock()
        dummy_p.pid = 2222
        dummy_p.poll.return_value = None
        dummy_p.returncode = None
        mock_proc.return_value = dummy_p

        await adapter.apply(tunnel_id, iran_spec)

        frpc_cfg_path = tmp_path / f"frpc_{tunnel_id}.yaml"
        assert frpc_cfg_path.exists()
        frpc_content = frpc_cfg_path.read_text()
        assert "visitors:" in frpc_content
        assert "type: stcp" in frpc_content
        assert 'serverName: "test-direct-tun"' in frpc_content
        assert 'secretKey: "sec999"' in frpc_content
        assert 'bindAddr: "0.0.0.0"' in frpc_content
        assert "bindPort: 8443" in frpc_content

        # Cleanup
        await adapter.remove(tunnel_id)
        assert not frpc_cfg_path.exists()


@pytest.mark.asyncio
async def test_inspect_tunnel_health_frp_direct_mode():
    """Verify inspect_tunnel_health correctly skips service ports on foreign server
    and checks service ports on Iran visitor client in FRP direct mode.
    """
    mgr = AdapterManager()
    
    # 1. Foreign Server Direct: inspect_tunnel_health should skip service ports and only check control_port
    mgr.tunnel_configs["tun-frp-dir-foreign"] = {
        "core": "frp",
        "spec": {
            "mode": "server",
            "is_reverse": False,
            "bind_port": 7000,
            "ports": [8443],
        }
    }
    
    with patch("node.app.core_adapters.is_port_listening_locally", return_value=True), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await mgr.inspect_tunnel_health(
            tunnel_id="tun-frp-dir-foreign",
            tunnel_core="frp",
            mode="server",
            ports=[8443],
            control_port=7000,
            proto="tcp"
        )
        assert st["process_running"] is True
        # Listening ports should ONLY have control port, service port 8443 skipped on foreign server
        types = [lp["type"] for lp in st["listening_ports"]]
        assert "control_tcp" in types
        assert "service_tcp" not in types

    # 2. Iran Client Direct: inspect_tunnel_health should check service ports locally on Iran
    mgr.tunnel_configs["tun-frp-dir-iran"] = {
        "core": "frp",
        "spec": {
            "mode": "client",
            "is_reverse": False,
            "ports": [8443],
        }
    }
    
    with patch("node.app.core_adapters.is_port_listening_locally", return_value=True), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await mgr.inspect_tunnel_health(
            tunnel_id="tun-frp-dir-iran",
            tunnel_core="frp",
            mode="client",
            ports=[8443],
            proto="tcp"
        )
        assert st["process_running"] is True
        types = [lp["type"] for lp in st["listening_ports"]]
        assert "service_tcp" in types


@pytest.mark.asyncio
async def test_frp_port_conflicts_direct_mode():
    """Verify check_port_conflicts correctly checks:
    - Iran Node for service ports (e.g. 8443)
    - Foreign Node for control/bind port (e.g. 7000)
    and does NOT falsely collide Foreign control port with Iran ports.
    """
    from fastapi import HTTPException
    from panel.app.routers.tunnels import check_port_conflicts

    class MockTunnelModel:
        def __init__(self, id, name, core, is_reverse, iran_node_id, foreign_node_id, spec, status="active"):
            self.id = id
            self.name = name
            self.core = core
            self.is_reverse = is_reverse
            self.iran_node_id = iran_node_id
            self.foreign_node_id = foreign_node_id
            self.node_id = iran_node_id
            self.spec = spec
            self.type = spec.get("type", "tcp")
            self.status = status

    existing_rev = MockTunnelModel(
        id="tun-rev-1",
        name="Existing Rev",
        core="frp",
        is_reverse=True,
        iran_node_id="iran-1",
        foreign_node_id="foreign-1",
        spec={"ports": [8443], "bind_port": 7000, "type": "tcp"}
    )

    db = AsyncMock()

    async def mock_execute(stmt):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM nodes" in stmt_str:
            mock_node = MagicMock()
            mock_node.name = "TestNode"
            res.scalar_one_or_none.return_value = mock_node
        else:
            res.scalars.return_value.all.return_value = [existing_rev]
        return res

    db.execute = AsyncMock(side_effect=mock_execute)

    # 1. Collision on Iran service port: New direct tunnel wants port 8443 on Iran node
    with pytest.raises(HTTPException) as exc_info:
        await check_port_conflicts(
            db=db,
            spec={"ports": [8443], "bind_port": 9000, "type": "tcp"},
            iran_node_id="iran-1",
            foreign_node_id="foreign-2",
            core="frp",
            is_reverse=False
        )
    assert exc_info.value.status_code == 400
    assert "تداخل پورت: پورت 8443" in exc_info.value.detail

    # 2. No collision: New direct tunnel uses port 8444 on Iran node and bind_port 7000 on Foreign node foreign-2
    # Since foreign-2 is different from iran-1, 7000 on foreign-2 does not collide with 7000 on iran-1!
    await check_port_conflicts(
        db=db,
        spec={"ports": [8444], "bind_port": 7000, "type": "tcp"},
        iran_node_id="iran-1",
        foreign_node_id="foreign-2",
        core="frp",
        is_reverse=False
    )


@pytest.mark.asyncio
async def test_frp_test_tunnel_config_endpoint():
    """Verify test_tunnel_config endpoint runs cleanly for FRP without NameError (use_tls)
    and properly verifies ports and protocol specs in direct mode.
    """
    from panel.app.routers.tunnels import test_tunnel_config

    db = AsyncMock()
    mock_iran = MagicMock()
    mock_iran.id = "iran-1"
    mock_iran.name = "Iran Server"
    mock_iran.node_metadata = {"ip_address": "1.2.3.4"}

    mock_foreign = MagicMock()
    mock_foreign.id = "foreign-1"
    mock_foreign.name = "Foreign Server"
    mock_foreign.node_metadata = {"ip_address": "5.6.7.8"}

    async def mock_execute(stmt):
        res = MagicMock()
        stmt_str = str(stmt)
        if "iran-1" in stmt_str:
            res.scalar_one_or_none.return_value = mock_iran
        elif "foreign-1" in stmt_str:
            res.scalar_one_or_none.return_value = mock_foreign
        else:
            res.scalars.return_value.all.return_value = []
        return res

    db.execute = AsyncMock(side_effect=mock_execute)
    admin = MagicMock()

    with patch("panel.app.routers.tunnels.measure_node_latency", AsyncMock(return_value=(True, 35, "OK"))):
        payload = {
            "core": "frp",
            "type": "tcp",
            "iran_node_id": "iran-1",
            "foreign_node_id": "foreign-1",
            "ports": "8443,9443",
            "control_port": 7000,
            "frp_transport": "tcp",
            "frp_security": "tls",
            "is_reverse": False
        }
        res = await test_tunnel_config(payload=payload, db=db, current_user=admin)
        assert res["valid"] is True
        proto_check = next((c for c in res["checks"] if c["name"] == "protocol"), None)
        assert proto_check is not None
        assert "TLS Encrypted" in proto_check["detail"]
        assert "Direct STCP" in proto_check["detail"]


