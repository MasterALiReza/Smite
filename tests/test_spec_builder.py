import sys
from pathlib import Path
try:
    import pytest
except ImportError:
    pytest = None
from unittest.mock import MagicMock

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from panel.app.spec_builder import (
    build_tunnel_node_specs,
    build_rathole_node_specs,
    build_backhaul_node_specs,
    build_chisel_node_specs,
    build_frp_node_specs,
    build_gost_node_specs,
)


class DummyTunnel:
    def __init__(self, id, core, type="tcp", spec=None):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = True


def test_spec_builder_rathole():
    tunnel = DummyTunnel(
        id="t-rathole-1",
        core="rathole",
        type="tcp",
        spec={"ports": [8080], "transport": "tcp", "token": "test-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert server_spec["ports"] == [8080]
    assert client_spec["ports"] == [8080]
    assert client_spec["token"] == "test-token"
    assert "1.1.1.1" in client_spec["remote_addr"]


def test_spec_builder_rathole_ws():
    tunnel = DummyTunnel(
        id="t-rathole-ws",
        core="rathole",
        type="tcp",
        spec={"ports": [8080], "transport": "ws", "transport_type": "ws", "token": "test-ws-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")

    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert server_spec["websocket_tls"] is False
    assert client_spec["websocket_tls"] is False
    assert client_spec["remote_addr"].startswith("ws://1.1.1.1:")


def test_spec_builder_rathole_wss():
    tunnel = DummyTunnel(
        id="t-rathole-wss",
        core="rathole",
        type="tcp",
        spec={"ports": [8080], "transport": "wss", "transport_type": "wss", "token": "test-wss-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")

    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert server_spec["websocket_tls"] is True
    assert client_spec["websocket_tls"] is True
    assert client_spec["remote_addr"].startswith("wss://1.1.1.1:")
    assert "tls_pkcs12_b64" in server_spec and len(server_spec["tls_pkcs12_b64"]) > 100
    assert "tls_pkcs12_password" in server_spec and len(server_spec["tls_pkcs12_password"]) > 0
    assert "tls_ca_cert_pem" in client_spec and "BEGIN CERTIFICATE" in client_spec["tls_ca_cert_pem"]
    assert client_spec.get("custom_sni") == "1.1.1.1"


def test_spec_builder_backhaul():
    tunnel = DummyTunnel(
        id="t-backhaul-1",
        core="backhaul",
        type="tcp",
        spec={"ports": ["8080=127.0.0.1:8080"], "transport": "tcp", "token": "tok"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert "8080=127.0.0.1:8080" in server_spec["ports"]
    assert "1.1.1.1" in client_spec["remote_addr"]


def test_spec_builder_chisel():
    tunnel = DummyTunnel(
        id="t-chisel-1",
        core="chisel",
        type="tcp",
        spec={"ports": [9000], "auth": "user:pass"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert server_spec["reverse_port"] == 9000
    assert "http://1.1.1.1:" in client_spec["server_url"]
    assert client_spec["auth"] == "user:pass"


def test_spec_builder_frp():
    tunnel = DummyTunnel(
        id="t-frp-1",
        core="frp",
        type="tcp",
        spec={"ports": [{"local": 443, "remote": 443}], "token": "frp-secret"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["mode"] == "server"
    assert client_spec["mode"] == "client"
    assert client_spec["server_addr"] == "1.1.1.1"
    assert client_spec["token"] == "frp-secret"
    assert client_spec["ports"] == [{"local": 443, "remote": 443}]


def test_spec_builder_gost_deterministic_distinct_ports():
    tunnel1 = DummyTunnel(
        id="t-gost-alpha-1",
        core="gost",
        type="tcp",
        spec={"ports": [1035]}
    )
    tunnel1.is_reverse = False
    
    tunnel2 = DummyTunnel(
        id="t-gost-beta-2",
        core="gost",
        type="tcp",
        spec={"ports": [8081, 8082]}
    )
    tunnel2.is_reverse = False

    s1, c1 = build_tunnel_node_specs(tunnel1, "192.0.2.1", "198.51.100.1")
    s2, c2 = build_tunnel_node_specs(tunnel2, "192.0.2.1", "198.51.100.1")

    # Both must have distinct control ports
    assert s1["control_port"] != s2["control_port"]
    assert c1["control_port"] != c2["control_port"]
    assert s1["control_port"] == c1["control_port"]
    assert s2["control_port"] == c2["control_port"]

    # Ports must be in high range (25000-50000) and NOT 44300
    assert 25000 <= s1["control_port"] < 50000
    assert 25000 <= s2["control_port"] < 50000
    assert s1["control_port"] != 44300
    assert s2["control_port"] != 44300

    # Must be saved into tunnel.spec
    assert tunnel1.spec["control_port"] == s1["control_port"]
    assert tunnel2.spec["control_port"] == s2["control_port"]


def test_spec_builder_gost_custom_port_preserved():
    tunnel = DummyTunnel(
        id="custom-gost",
        core="gost",
        type="tcp",
        spec={"ports": [9990], "control_port": 30763}
    )
    tunnel.is_reverse = False
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["control_port"] == 30763
    assert c["control_port"] == 30763


def test_spec_builder_gost_legacy_44300_migrated():
    tunnel = DummyTunnel(
        id="legacy-gost-tunnel",
        core="gost",
        type="tcp",
        spec={"ports": [8080], "control_port": 44300}
    )
    tunnel.is_reverse = False
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["control_port"] != 44300
    assert 25000 <= s["control_port"] < 50000
    assert tunnel.spec["control_port"] == s["control_port"]


def test_spec_builder_gost_ports_resolution_from_listen_port():
    """Test that GOST spec builder correctly derives ports array from listen_port if ports is omitted"""
    tunnel = DummyTunnel(
        id="listen-port-gost",
        core="gost",
        type="tcp",
        spec={"listen_port": 9990}
    )
    tunnel.is_reverse = False
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["ports"] == [9990]
    assert c["ports"] == [9990]
    assert s["mode"] == "client"
    assert s["server_ip"] == "2.2.2.2"
    assert c["mode"] == "server"
    assert tunnel.spec["ports"] == [9990]


if __name__ == "__main__":
    import inspect
    current_module = sys.modules[__name__]
    passed = 0
    failed = 0
    for name, func in inspect.getmembers(current_module, inspect.isfunction):
        if name.startswith("test_"):
            try:
                func()
                passed += 1
                print(f"PASS: {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL: {name}: {e}")
    print(f"\nTotal: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
