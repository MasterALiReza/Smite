import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.tunnel_contract import TunnelContractError, normalize_transport, validate_tunnel_request


def test_gost_grpc_type_selects_grpc_carrier():
    result = validate_tunnel_request("gost", "grpc", "tcp")
    assert result["payload"] == "tcp"
    assert result["transport"] == "grpc"
    assert result["tls"] is True


def test_gost_grpc_type_rejects_conflicting_carrier():
    with pytest.raises(TunnelContractError):
        validate_tunnel_request("gost", "grpc", "wss")


def test_gost_tcpmux_type_rejects_grpc_carrier():
    with pytest.raises(TunnelContractError):
        validate_tunnel_request("gost", "tcpmux", "grpc")


def test_gost_tcpmux_type_sets_multiplex():
    result = validate_tunnel_request("gost", "tcpmux", "tcp")
    assert result["payload"] == "tcp"
    assert result["multiplex"] is True


def test_transport_aliases_are_canonicalized():
    assert normalize_transport("gost", "https") == "wss"
    assert normalize_transport("frp", "ws") == "websocket"
    assert normalize_transport("backhaul", "mux") == "tcpmux"


def test_unknown_transport_is_rejected():
    with pytest.raises(TunnelContractError):
        normalize_transport("rathole", "quic")


def test_backhaul_udp_over_web_carrier_is_rejected():
    with pytest.raises(TunnelContractError):
        validate_tunnel_request("backhaul", "tcp+udp", "wss")


def test_backhaul_udp_over_tcp_is_allowed():
    result = validate_tunnel_request("backhaul", "udp", "tcp")
    assert result["transport"] == "tcp"


def test_frp_wss_with_security_none_is_rejected():
    with pytest.raises(TunnelContractError):
        validate_tunnel_request("frp", "tcp", "wss", security_type="none")


def test_frp_quic_security_none_is_a_warning_only():
    result = validate_tunnel_request("frp", "tcp", "quic", security_type="none")
    assert result["tls"] is True
    assert result["warnings"]


def test_chisel_socks5_type_is_allowed():
    result = validate_tunnel_request("chisel", "socks5", "ws")
    assert result["type"] == "socks5"


def test_unknown_core_is_rejected():
    with pytest.raises(TunnelContractError):
        validate_tunnel_request("wireguard", "tcp", "tcp")


def test_router_contract_check_accepts_legacy_stored_type():
    from app.routers.tunnels import _contract_check

    result = _contract_check(
        "gost", "ws", "tcp", {}, "none", legacy_type="ws"
    )
    assert result["transport"] == "tcp"


def test_router_contract_check_rejects_new_unsupported_type():
    from app.routers.tunnels import _contract_check

    with pytest.raises(TunnelContractError):
        _contract_check("gost", "ws", "tcp", {}, "none", legacy_type="tcp")


def test_router_contract_check_tolerates_unchanged_legacy_pair():
    from app.routers.tunnels import _contract_check

    result = _contract_check("frp", "tcp", "wss", {}, "none", legacy_pair=("wss", "none"))
    assert result["transport"] == "wss"


def test_router_contract_check_rejects_new_contradictory_pair():
    from app.routers.tunnels import _contract_check

    with pytest.raises(TunnelContractError):
        _contract_check("frp", "tcp", "wss", {}, "none", legacy_pair=("tcp", "none"))
