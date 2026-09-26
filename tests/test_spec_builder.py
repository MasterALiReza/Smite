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

try:
    from panel.app.spec_builder import (
        build_tunnel_node_specs,
        build_rathole_node_specs,
        build_backhaul_node_specs,
        build_chisel_node_specs,
        build_frp_node_specs,
        build_gost_node_specs,
    )
except ImportError:
    from app.spec_builder import (
        build_tunnel_node_specs,
        build_rathole_node_specs,
        build_backhaul_node_specs,
        build_chisel_node_specs,
        build_frp_node_specs,
        build_gost_node_specs,
    )


class DummyTunnel:
    def __init__(self, id, core, type="tcp", spec=None, gaming_mode=False, **kwargs):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = True
        self.gaming_mode = gaming_mode
        for k, v in kwargs.items():
            setattr(self, k, v)


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


def test_spec_builder_backhaul_pure_udp():
    """Verify Pure UDP transport is preserved for low-jitter competitive gaming without forcing TCP."""
    tunnel = DummyTunnel(
        id="t-backhaul-pure-udp",
        core="backhaul",
        type="udp",
        spec={"ports": ["27015=127.0.0.1:27015"], "transport": "udp", "token": "game-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["transport"] == "udp"
    assert client_spec["transport"] == "udp"
    assert server_spec.get("accept_udp") is not True


def test_spec_builder_backhaul_udp_over_tcp():
    """Verify UDP-over-TCP is used when accept_udp is true or type is tcp+udp."""
    tunnel = DummyTunnel(
        id="t-backhaul-udp-tcp",
        core="backhaul",
        type="tcp+udp",
        spec={"ports": [8080], "transport": "tcpmux", "token": "tok"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["transport"] == "tcp"
    assert client_spec["transport"] == "tcp"
    assert server_spec["accept_udp"] is True
    assert client_spec["accept_udp"] is True


def test_spec_builder_backhaul_port_ranges_and_control_collision():
    """Verify Backhaul port range (e.g. 27000-27050) is preserved and control port avoids colliding with any port in the range."""
    tunnel = DummyTunnel(
        id="t-backhaul-range",
        core="backhaul",
        type="udp",
        spec={"ports": ["27000-27050"], "transport": "udp", "token": "game-tok"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert "27000-27050" in server_spec["ports"]
    # Control port must NOT be between 27000 and 27050 inclusive
    ctrl_p = server_spec["control_port"]
    assert not (27000 <= ctrl_p <= 27050)


def test_spec_builder_backhaul_gaming_mode_and_v072_tuning():
    """Verify gaming mode auto-injects low-latency tuning and propagates v0.7.2 options."""
    tunnel = DummyTunnel(
        id="t-backhaul-gaming",
        core="backhaul",
        type="udp",
        gaming_mode=True,
        spec={
            "ports": [7777],
            "transport": "udp",
            "server_options": {"proxy_protocol": True, "skip_optz": True},
            "client_options": {"mss": 1380}
        }
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["gaming_mode"] is True
    assert client_spec["gaming_mode"] is True
    assert server_spec["server_options"]["nodelay"] is True
    assert client_spec["client_options"]["nodelay"] is True
    assert server_spec["server_options"]["channel_size"] == 8192
    assert server_spec["server_options"]["mux_framesize"] == 4096
    assert server_spec["proxy_protocol"] is True
    assert server_spec["skip_optz"] is True
    assert client_spec["mss"] == 1380



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
    assert server_spec["control_port"] == server_spec["server_port"]
    assert client_spec["control_port"] == server_spec["control_port"]
    assert f"http://1.1.1.1:{server_spec['control_port']}" == client_spec["server_url"]
    assert server_spec["auth"] == "user:pass"
    assert server_spec["token"] == "user:pass"
    assert client_spec["auth"] == "user:pass"
    assert client_spec["token"] == "user:pass"


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


def test_spec_builder_frp_tcp_udp():
    tunnel = DummyTunnel(
        id="t-frp-dual",
        core="frp",
        type="tcp+udp",
        spec={"ports": [8080], "token": "frp-dual-secret"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["tunnel_type"] == "tcp+udp"
    assert server_spec["type"] == "tcp+udp"
    assert client_spec["tunnel_type"] == "tcp+udp"
    assert client_spec["type"] == "tcp+udp"


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
def test_spec_builder_backhaul_mux_version_and_types_normalization():
    """Verify that Backhaul v0.7.2 options like mux_version, mss, channel_size are properly cast to integers"""
    tunnel = DummyTunnel(
        id="t-bh-mux",
        core="backhaul",
        type="tcpmux",
        spec={
            "control_port": 3080,
            "ports": [8080],
            "server_options": {
                "mux_version": "2",
                "mss": "1380",
                "channel_size": "8192",
                "nodelay": "true",
                "skip_optz": "false"
            },
            "client_options": {
                "mux_version": "2",
                "mss": "1380",
                "connection_pool": "16",
                "aggressive_pool": "true"
            }
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["server_options"]["mux_version"] == 2
    assert isinstance(s["server_options"]["mux_version"], int)
    assert s["server_options"]["mss"] == 1380
    assert s["server_options"]["channel_size"] == 8192
    assert s["server_options"]["nodelay"] is True
    assert s["server_options"]["skip_optz"] is False

    assert c["client_options"]["mux_version"] == 2
    assert isinstance(c["client_options"]["mux_version"], int)
    assert c["client_options"]["mss"] == 1380
    assert c["client_options"]["connection_pool"] == 16
    assert c["client_options"]["aggressive_pool"] is True


def test_spec_builder_parse_ports_list_bounds():
    """Verify parse_ports_list filters out non-standard and out-of-bounds ports"""
    from panel.app.spec_builder import parse_ports_list
    bad_spec = {
        "ports": [80, 0, 443, 70000, -1, 65535, "invalid", "8080"]
    }
    cleaned = parse_ports_list(bad_spec)
    assert cleaned == [80, 443, 65535, 8080]


def test_spec_builder_rathole_control_port_preservation():
    """Verify Rathole respects user control_port < 24000 (such as canonical 23333)"""
    tunnel = DummyTunnel(
        id="t-rathole-custom-cp",
        core="rathole",
        type="tcp",
        spec={"ports": [8080], "control_port": 23333, "transport": "tcp"}
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["control_port"] == 23333
    assert c["control_port"] == 23333
    assert ":23333" in c["remote_addr"]


def test_spec_builder_chisel_control_port_bounds():
    """Verify Chisel handles custom and invalid control_port safely"""
    tunnel_valid = DummyTunnel(
        id="t-chisel-valid",
        core="chisel",
        spec={"ports": [8080], "control_port": 18080}
    )
    s_v, c_v = build_tunnel_node_specs(tunnel_valid, "1.1.1.1", "2.2.2.2")
    assert s_v["control_port"] == 18080
    assert c_v["control_port"] == 18080

    tunnel_invalid = DummyTunnel(
        id="t-chisel-invalid",
        core="chisel",
        spec={"ports": [8080], "control_port": 80}  # below 1024
    )
    s_inv, c_inv = build_tunnel_node_specs(tunnel_invalid, "1.1.1.1", "2.2.2.2")
    assert s_inv["control_port"] >= 1024
    assert s_inv["control_port"] != 8080



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
