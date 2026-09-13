import sys
from pathlib import Path
import pytest
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
