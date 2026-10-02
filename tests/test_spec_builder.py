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
    """Verify UDP transport request is safely mapped to TCP with accept_udp=True (preventing upstream crash)."""
    tunnel = DummyTunnel(
        id="t-backhaul-pure-udp",
        core="backhaul",
        type="udp",
        spec={"ports": ["27015=127.0.0.1:27015"], "transport": "udp", "token": "game-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["transport"] == "tcp"
    assert client_spec["transport"] == "tcp"
    assert server_spec.get("accept_udp") is True
    assert client_spec.get("accept_udp") is True


def test_spec_builder_backhaul_wss_tls_certs():
    """Verify Backhaul WSS/TLS auto-generates self-signed certificates and sets insecure=True on client."""
    tunnel = DummyTunnel(
        id="t-backhaul-wss",
        core="backhaul",
        type="tcp",
        transport_type="wss",
        security_type="tls",
        custom_sni="vpn.example.com",
        spec={"ports": [8080], "token": "sec-token"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["transport"] == "wss"
    assert client_spec["transport"] == "wss"
    assert "tls_cert_pem" in server_spec and "BEGIN CERTIFICATE" in server_spec["tls_cert_pem"]
    assert "tls_key_pem" in server_spec and ("BEGIN RSA PRIVATE KEY" in server_spec["tls_key_pem"] or "BEGIN PRIVATE KEY" in server_spec["tls_key_pem"])
    assert client_spec["insecure"] is True
    assert client_spec["remote_addr"].startswith("wss://")


def test_spec_builder_backhaul_udp_over_tcp():
    """Verify UDP-over-TCP is used when accept_udp is true or type is tcp+udp."""
    tunnel = DummyTunnel(
        id="t-backhaul-udp-tcp",
        core="backhaul",
        type="tcp+udp",
        spec={"ports": [8080], "transport": "tcpmux", "token": "tok"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    
    assert server_spec["transport"] == "tcpmux"
    assert client_spec["transport"] == "tcpmux"
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


def test_spec_builder_chisel_wss_mode():
    """Verify Chisel in WSS mode uses https://, enables TLS, and provisions certificates."""
    tunnel = DummyTunnel(
        id="t-chisel-wss-1",
        core="chisel",
        type="tcp",
        spec={"ports": [4430], "transport": "wss", "auth": "admin:secret"}
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")

    assert server_spec["transport"] == "wss"
    assert client_spec["transport"] == "wss"
    assert server_spec["websocket_tls"] is True
    assert client_spec["websocket_tls"] is True
    assert client_spec["server_url"].startswith("https://1.1.1.1:")
    assert client_spec["tls_skip_verify"] is True
    assert "BEGIN CERTIFICATE" in server_spec["tls_cert_pem"]
    assert "BEGIN " in server_spec["tls_key_pem"]


def test_spec_builder_chisel_udp_gaming_and_socks5():
    """Verify Chisel preserves UDP gaming and SOCKS5 dynamic proxy types."""
    tunnel_udp = DummyTunnel(
        id="t-chisel-udp",
        core="chisel",
        type="udp",
        spec={"ports": [27015], "transport": "ws"}
    )
    s_udp, c_udp = build_tunnel_node_specs(tunnel_udp, "1.1.1.1", "2.2.2.2")
    assert s_udp["type"] == "udp"
    assert c_udp["type"] == "udp"

    tunnel_socks = DummyTunnel(
        id="t-chisel-socks",
        core="chisel",
        type="socks5",
        spec={"ports": [1080], "transport": "ws"}
    )
    s_socks, c_socks = build_tunnel_node_specs(tunnel_socks, "1.1.1.1", "2.2.2.2")
    assert s_socks["type"] == "socks5"
    assert c_socks["type"] == "socks5"


def test_spec_builder_chisel_anti_dpi_camouflage():
    """Verify Chisel propagates Anti-DPI camouflage, SNI, Host, Decoy Backend, and Keepalive."""
    tunnel = DummyTunnel(
        id="t-chisel-stealth",
        core="chisel",
        type="tcp",
        spec={
            "ports": [8080],
            "transport": "wss",
            "custom_sni": "speedtest.net",
            "custom_host": "cdn.speedtest.net",
            "backend_url": "https://speedtest.net",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "keepalive": "5s",
            "max_retry_interval": "5s"
        }
    )
    server_spec, client_spec = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")

    assert server_spec["backend_url"] == "https://speedtest.net"
    assert client_spec["custom_sni"] == "speedtest.net"
    assert client_spec["custom_host"] == "cdn.speedtest.net"
    assert client_spec["user_agent"] == "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    assert client_spec["keepalive"] == "5s"
    assert client_spec["max_retry_interval"] == "5s"
    assert server_spec["keepalive"] == "5s"


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
        spec={"listen_port": 9990, "force_direct": True}
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
    try:
        from panel.app.spec_builder import parse_ports_list
    except ImportError:
        from app.spec_builder import parse_ports_list
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


def test_spec_builder_frp_wss_mode():
    """Verify FRP WSS mode generates in-memory server TLS certs and enables client TLS"""
    tunnel = DummyTunnel(
        id="t-frp-wss",
        core="frp",
        type="tcp",
        spec={
            "ports": [8443],
            "transport_type": "wss",
            "custom_sni": "cdn.example.com",
            "token": "secret-wss-token"
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["mode"] == "server"
    assert c["mode"] == "client"
    assert s["transport_type"] == "websocket"
    assert c["transport"] == "websocket"
    assert c["tls_enable"] is True
    assert c["custom_sni"] == "cdn.example.com"
    assert "tls_cert_pem" in s and "BEGIN CERTIFICATE" in s["tls_cert_pem"]
    assert "tls_key_pem" in s and ("BEGIN RSA PRIVATE KEY" in s["tls_key_pem"] or "BEGIN PRIVATE KEY" in s["tls_key_pem"])


def test_spec_builder_frp_quic_and_kcp():
    """Verify FRP QUIC and KCP transports are properly recognized and configured"""
    tunnel_quic = DummyTunnel(
        id="t-frp-quic",
        core="frp",
        type="tcp",
        spec={"ports": [8080], "transport": "quic"}
    )
    s_q, c_q = build_tunnel_node_specs(tunnel_quic, "1.1.1.1", "2.2.2.2")
    assert s_q["transport"] == "quic"
    assert c_q["transport"] == "quic"
    assert c_q["tls_enable"] is True

    tunnel_kcp = DummyTunnel(
        id="t-frp-kcp",
        core="frp",
        type="tcp",
        spec={"ports": [8080], "transport": "kcp", "security_type": "none"}
    )
    s_k, c_k = build_tunnel_node_specs(tunnel_kcp, "1.1.1.1", "2.2.2.2")
    assert s_k["transport"] == "kcp"
    assert c_k["transport"] == "kcp"
    assert c_k["tls_enable"] is False


def test_spec_builder_frp_reliability_and_limits():
    """Verify health checks, rate limiting, and proxy protocol are propagated"""
    tunnel = DummyTunnel(
        id="t-frp-limits",
        core="frp",
        type="tcp",
        rate_limit_mbps=25,
        proxy_protocol_version="v2",
        spec={
            "ports": [8080],
            "enable_health_check": True,
            "health_check_type": "tcp",
            "health_check_interval_s": 5,
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert c["health_check_type"] == "tcp"
    assert c["health_check_interval_s"] == 5
    assert c["bandwidth_limit"] == "25MB"
    assert c["proxy_protocol_version"] == "v2"


def test_spec_builder_frp_vhost_http():
    """Verify HTTP/HTTPS vhost custom domains routing and separate external vhost port"""
    tunnel = DummyTunnel(
        id="t-frp-vhost",
        core="frp",
        type="http",
        spec={
            "ports": [80],
            "custom_domains": ["api.example.com", "app.example.com"]
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["type"] == "http"
    assert c["type"] == "http"
    assert s["vhost_http_port"] == 80
    assert s["bind_port"] != s["vhost_http_port"]
    assert s["custom_domains"] == ["api.example.com", "app.example.com"]
    assert c["custom_domains"] == ["api.example.com", "app.example.com"]


def test_spec_builder_frp_port_collision_avoidance():
    """Verify FRP dynamically reallocates bind_port when it conflicts with forwarded service ports"""
    tunnel = DummyTunnel(
        id="t-frp-conflict",
        core="frp",
        type="tcp",
        spec={
            "ports": [7000],
            "bind_port": 7000
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["bind_port"] != 7000
    assert c["server_port"] != 7000
    assert s["bind_port"] == c["server_port"]
    assert s["ports"] == [7000]


def test_spec_builder_frp_bandwidth_normalization():
    """Verify FRP normalizes bandwidth limits, stripping spaces and units correctly"""
    tunnel = DummyTunnel(
        id="t-frp-bw",
        core="frp",
        type="tcp",
        spec={
            "ports": [8080],
            "bandwidth_limit": " 50 mb "
        }
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert c["bandwidth_limit"] == "50MB"

    tunnel_invalid = DummyTunnel(
        id="t-frp-bw-inv",
        core="frp",
        type="tcp",
        spec={
            "ports": [8080],
            "bandwidth_limit": "unlimited\nmalicious_yaml"
        }
    )
    s_inv, c_inv = build_tunnel_node_specs(tunnel_invalid, "1.1.1.1", "2.2.2.2")
    assert "bandwidth_limit" not in c_inv


def test_spec_builder_frp_udp_disables_health_check_and_compression():
    """Verify FRP UDP tunnels disable health checks, compression, and proxy protocol to prevent dropped proxies"""
    tunnel_udp = DummyTunnel(
        id="t-frp-udp-wg",
        core="frp",
        type="udp",
        spec={
            "ports": [51820],
            "enable_health_check": True,
            "health_check_type": "tcp",
            "proxy_protocol_version": "v2"
        }
    )
    s, c = build_tunnel_node_specs(tunnel_udp, "1.1.1.1", "2.2.2.2")
    assert s["type"] == "udp"
    assert c["type"] == "udp"
    assert "health_check_type" not in c
    assert "proxy_protocol_version" not in c
    assert c["use_compression"] is False
    assert s["use_compression"] is False

def test_spec_builder_gost_udp_multiplexing_and_target_host():
    """Verify GOST UDP tunnels automatically enable yamux multiplexing over streaming transports and propagate target_host"""
    tunnel_udp = DummyTunnel(
        id="t-gost-udp",
        core="gost",
        type="udp",
        transport_type="grpc",
        spec={
            "ports": [51820],
            "target_host": "127.0.0.1",
            "is_reverse": True
        }
    )
    s, c = build_tunnel_node_specs(tunnel_udp, "1.1.1.1", "2.2.2.2")
    assert s["type"] == "udp"
    assert c["type"] == "udp"
    assert s["multiplex"] is True
    assert c["multiplex"] is True
    assert s["mux_type"] == "yamux"
    assert c["mux_type"] == "yamux"
    assert s["target_host"] == "127.0.0.1"
    assert c["target_host"] == "127.0.0.1"

def test_spec_builder_gost_reverse_default_and_force_direct():
    """Verify GOST multi-node tunnels default to reverse mode unless force_direct is set"""
    tunnel_multi = DummyTunnel(
        id="t-gost-multi",
        core="gost",
        type="udp",
        transport_type="grpc",
        spec={"ports": [51820]}
    )
    s, c = build_tunnel_node_specs(tunnel_multi, "178.239.146.188", "103.83.86.35")
    assert s["mode"] == "server"
    assert c["mode"] == "client"
    assert c["server_ip"] == "178.239.146.188"
    assert s["is_reverse"] is True
    assert c["is_reverse"] is True

    tunnel_direct = DummyTunnel(
        id="t-gost-direct",
        core="gost",
        type="udp",
        transport_type="grpc",
        spec={"ports": [51820], "force_direct": True}
    )
    s_d, c_d = build_tunnel_node_specs(tunnel_direct, "178.239.146.188", "103.83.86.35")
    assert s_d["mode"] == "client"
    assert s_d["server_ip"] == "103.83.86.35"
    assert c_d["mode"] == "server"
    assert s_d["is_reverse"] is False
    assert c_d["is_reverse"] is False


def test_spec_builder_gost_kcp_quic_no_forced_yamux():
    """Verify GOST KCP and QUIC do not force yamux multiplexing for UDP tunnels"""
    tunnel_kcp = DummyTunnel(
        id="t-gost-kcp",
        core="gost",
        type="udp",
        spec={"ports": [51820], "transport": "kcp", "transport_type": "kcp"}
    )
    s_k, c_k = build_tunnel_node_specs(tunnel_kcp, "178.239.146.188", "103.83.86.35")
    assert s_k.get("multiplex") is not True
    assert c_k.get("multiplex") is not True

    tunnel_quic = DummyTunnel(
        id="t-gost-quic",
        core="gost",
        type="udp",
        spec={"ports": [51820], "transport": "quic", "transport_type": "quic"}
    )
    s_q, c_q = build_tunnel_node_specs(tunnel_quic, "178.239.146.188", "103.83.86.35")
    assert s_q.get("multiplex") is not True
    assert c_q.get("multiplex") is not True

    # But stream-based transports (tcp, ws, grpc) SHOULD default to multiplexing for UDP mode
    tunnel_grpc = DummyTunnel(
        id="t-gost-grpc",
        core="gost",
        type="udp",
        spec={"ports": [51820], "transport": "grpc", "transport_type": "grpc"}
    )
    s_g, c_g = build_tunnel_node_specs(tunnel_grpc, "178.239.146.188", "103.83.86.35")
    assert s_g.get("multiplex") is True
    assert c_g.get("multiplex") is True
def test_spec_builder_gost_token_entropy():
    """Verify GOST spec builder generates a high-entropy random auth_token when none is provided"""
    t1 = DummyTunnel(id="t-gost-entropy-1", core="gost", type="tcp", spec={"ports": [8080]})
    t2 = DummyTunnel(id="t-gost-entropy-2", core="gost", type="tcp", spec={"ports": [8081]})
    s1, c1 = build_tunnel_node_specs(t1, "1.1.1.1", "2.2.2.2")
    s2, c2 = build_tunnel_node_specs(t2, "1.1.1.1", "2.2.2.2")
    
    assert s1["auth_token"].startswith("gost-")
    assert s1["auth_token"] == c1["auth_token"]
    assert s2["auth_token"] == c2["auth_token"]
    assert s1["auth_token"] != s2["auth_token"]  # Unique per tunnel
    assert len(s1["auth_token"]) >= 20


def test_extract_all_tunnel_ports_with_ranges():
    """Verify extract_all_tunnel_ports includes ports from port_ranges for collision safety"""
    from app.routers.tunnels import extract_all_tunnel_ports
    spec = {
        "ports": [8080],
        "port_ranges": ["10000-10005"],
        "control_port": 35000
    }
    extracted = extract_all_tunnel_ports(spec)
    assert 8080 in extracted["service_ports"]
    for p in range(10000, 10006):
        assert p in extracted["service_ports"]
    assert 35000 in extracted["control_ports"]


def test_extract_all_tunnel_ports_with_dict_and_vhost():
    """Verify extract_all_tunnel_ports correctly extracts dict-based ports and vhost ports"""
    from app.routers.tunnels import extract_all_tunnel_ports
    spec = {
        "ports": [{"local": 8080, "remote": 8080}],
        "vhost_http_port": 80,
        "vhost_https_port": 443,
        "bind_port": 7000
    }
    extracted = extract_all_tunnel_ports(spec)
    assert 8080 in extracted["service_ports"]
    assert 80 in extracted["service_ports"]
    assert 443 in extracted["service_ports"]
    assert 7000 in extracted["control_ports"]
    assert {8080, 80, 443, 7000}.issubset(extracted["all_ports"])


def test_parse_ports_list_robustness():
    """Verify parse_ports_list handles comma strings, ranges, dicts, and integer lists"""
    from panel.app.spec_builder import parse_ports_list

    # Comma-separated string in dict
    assert parse_ports_list({"ports": "8080,8081"}) == [8080, 8081]
    # Range string
    assert parse_ports_list({"ports": "9000-9003"}) == [9000, 9001, 9002, 9003]
    # Dict ports
    assert parse_ports_list({"ports": [{"remote": 443, "local": 443}]}) == [443]
    # Single integer
    assert parse_ports_list(80) == [80]


def test_spec_builder_frp_target_host_and_bandwidth():
    """Verify FRP target_host and bandwidth normalization (GB to MB)"""
    tunnel = DummyTunnel(
        id="t-frp-bw",
        core="frp",
        type="tcp",
        spec={"ports": [8080], "bandwidth_limit": "2GB", "target_host": "192.168.1.50"}
    )
    s_spec, c_spec = build_frp_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert c_spec["local_ip"] == "192.168.1.50"
    assert c_spec["bandwidth_limit"] == "2048MB"
    assert "health_check_type" not in c_spec  # Default disabled!


def test_spec_builder_frp_san_cert():
    """Verify in-memory TLS certificate includes SubjectAlternativeName"""
    from cryptography import x509
    tunnel = DummyTunnel(
        id="t-frp-san",
        core="frp",
        type="tcp",
        spec={"ports": [8080], "transport": "tcp", "security_type": "tls", "custom_sni": "my-domain.com"}
    )
    s_spec, c_spec = build_frp_node_specs(tunnel, "203.0.113.10", "198.51.100.20")
    assert s_spec["tls_enable"] is True
    cert_pem = s_spec.get("tls_cert_pem")
    assert cert_pem and "BEGIN CERTIFICATE" in cert_pem

    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    assert "my-domain.com" in dns_names




def test_spec_builder_chisel_auth_normalization():
    """Verify Chisel auth token is automatically formatted as user:pass (smite:token) if no colon is present"""
    tunnel = DummyTunnel(
        id="t-chisel-auth-norm",
        core="chisel",
        spec={"ports": [8080], "auth": "singletoken12345"}
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert s["auth"] == "smite:singletoken12345"
    assert c["auth"] == "smite:singletoken12345"

    tunnel_colon = DummyTunnel(
        id="t-chisel-auth-colon",
        core="chisel",
        spec={"ports": [8080], "auth": "customuser:custompass"}
    )
    s2, c2 = build_tunnel_node_specs(tunnel_colon, "1.1.1.1", "2.2.2.2")
    assert s2["auth"] == "customuser:custompass"
    assert c2["auth"] == "customuser:custompass"


def test_spec_builder_chisel_san_certificate():
    """Verify in-memory TLS certificate for Chisel includes SubjectAlternativeName with DNS and IP"""
    from cryptography import x509
    tunnel = DummyTunnel(
        id="t-chisel-san",
        core="chisel",
        type="tcp",
        spec={"ports": [8080], "transport": "wss", "custom_sni": "stealth.example.com"}
    )
    s_spec, c_spec = build_tunnel_node_specs(tunnel, "203.0.113.10", "198.51.100.20")
    cert_pem = s_spec.get("tls_cert_pem")
    assert cert_pem and "BEGIN CERTIFICATE" in cert_pem
    assert c_spec.get("tls_ca_cert_pem") == cert_pem

    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    dns_names = san_ext.value.get_values_for_type(x509.DNSName)
    ip_addrs = [str(ip) for ip in san_ext.value.get_values_for_type(x509.IPAddress)]

    assert "stealth.example.com" in dns_names
    assert "chisel-tunnel" in dns_names
    assert "203.0.113.10" in ip_addrs


def test_spec_builder_chisel_direct_vs_reverse_directionality():
    """Verify Chisel produces correct node assignments for both reverse and direct tunnels"""
    # 1. Reverse tunnel (default)
    t_rev = DummyTunnel(
        id="t-chisel-rev",
        core="chisel",
        is_reverse=True,
        spec={"ports": [8080], "transport": "ws"}
    )
    iran_rev, foreign_rev = build_tunnel_node_specs(t_rev, "100.1.1.1", "200.2.2.2")
    assert iran_rev["mode"] == "server"
    assert iran_rev["reverse_only"] is True
    assert foreign_rev["mode"] == "client"
    assert foreign_rev["server_url"].startswith("http://100.1.1.1:")

    # 2. Direct tunnel (is_reverse=False)
    t_dir = DummyTunnel(
        id="t-chisel-dir",
        core="chisel",
        is_reverse=False,
        spec={"ports": [8080], "transport": "ws"}
    )
    iran_dir, foreign_dir = build_tunnel_node_specs(t_dir, "100.1.1.1", "200.2.2.2")
    assert iran_dir["mode"] == "client"
    assert iran_dir["server_url"].startswith("http://200.2.2.2:")  # Connects to Foreign server!
    assert foreign_dir["mode"] == "server"
    assert foreign_dir["reverse_only"] is False


def test_spec_builder_chisel_backend_ssrf_protection():
    """Verify private/loopback backend decoy URLs are stripped to protect against SSRF"""
    tunnel_malicious = DummyTunnel(
        id="t-chisel-ssrf",
        core="chisel",
        spec={"ports": [8080], "backend_url": "http://127.0.0.1:8000"}
    )
    s_mal, _ = build_tunnel_node_specs(tunnel_malicious, "1.1.1.1", "2.2.2.2")
    assert "backend_url" not in s_mal

    tunnel_cloud_meta = DummyTunnel(
        id="t-chisel-meta",
        core="chisel",
        spec={"ports": [8080], "backend_url": "http://169.254.169.254/latest/meta-data"}
    )
    s_meta, _ = build_tunnel_node_specs(tunnel_cloud_meta, "1.1.1.1", "2.2.2.2")
    assert "backend_url" not in s_meta

    tunnel_safe = DummyTunnel(
        id="t-chisel-safe",
        core="chisel",
        spec={"ports": [8080], "backend_url": "https://speedtest.net"}
    )
    s_safe, _ = build_tunnel_node_specs(tunnel_safe, "1.1.1.1", "2.2.2.2")
    assert s_safe.get("backend_url") == "https://speedtest.net"


def test_spec_builder_chisel_port_ranges_expansion():
    """Verify port_ranges are parsed and expanded into discrete integer ports for Chisel"""
    tunnel = DummyTunnel(
        id="t-chisel-ranges",
        core="chisel",
        port_ranges="9000-9003",
        spec={"ports": [8080]}
    )
    s, c = build_tunnel_node_specs(tunnel, "1.1.1.1", "2.2.2.2")
    assert 8080 in s["ports"]
    assert 9000 in s["ports"]
    assert 9001 in s["ports"]
    assert 9002 in s["ports"]
    assert 9003 in s["ports"]
    assert len(s["ports"]) == 5


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
