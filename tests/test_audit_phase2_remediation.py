import base64
import copy
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from panel.app.spec_builder import (
    build_chisel_node_specs,
    build_frp_node_specs,
    build_rathole_node_specs,
    _safe_noise_keypair,
)
from node.app.core_adapters import AdapterManager


class DummyTunnel:
    def __init__(self, id, core="frp", type="tcp", spec=None, is_reverse=None, **kwargs):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = is_reverse
        for k, v in kwargs.items():
            setattr(self, k, v)


# ===========================================================================
# 1. Harmonize Panel Core Lists & Respect force_direct Across All Cores
# ===========================================================================

def test_chisel_node_specs_respects_force_direct():
    """Verify build_chisel_node_specs treats force_direct=True as is_reverse=False."""
    tunnel = DummyTunnel(
        id="tun-chisel-dir",
        core="chisel",
        type="tcp",
        is_reverse=None,
        spec={"ports": [8080], "force_direct": True}
    )
    iran_ip = "10.0.0.1"
    foreign_ip = "10.0.0.2"

    iran_spec, foreign_spec = build_chisel_node_specs(tunnel, iran_ip, foreign_ip)

    # In direct chisel, Foreign is server, Iran is client
    # return is (client_spec, server_spec) for (iran_spec, foreign_spec)
    assert iran_spec["mode"] == "client"
    assert iran_spec["is_reverse"] is False
    assert iran_spec["force_direct"] is True

    assert foreign_spec["mode"] == "server"
    assert foreign_spec["is_reverse"] is False
    assert foreign_spec["force_direct"] is True
    assert foreign_spec["reverse_only"] is False

    assert tunnel.is_reverse is False
    assert tunnel.spec["is_reverse"] is False
    assert tunnel.spec["force_direct"] is True


def test_chisel_node_specs_reverse_mode_defaults():
    """Verify build_chisel_node_specs sets is_reverse=True and force_direct=False in reverse mode."""
    tunnel = DummyTunnel(
        id="tun-chisel-rev",
        core="chisel",
        type="tcp",
        is_reverse=True,
        spec={"ports": [8080]}
    )
    iran_ip = "10.0.0.1"
    foreign_ip = "10.0.0.2"

    iran_spec, foreign_spec = build_chisel_node_specs(tunnel, iran_ip, foreign_ip)

    assert iran_spec["mode"] == "server"
    assert iran_spec["is_reverse"] is True
    assert iran_spec["force_direct"] is False
    assert iran_spec["reverse_only"] is True

    assert foreign_spec["mode"] == "client"
    assert foreign_spec["is_reverse"] is True
    assert foreign_spec["force_direct"] is False

    assert tunnel.is_reverse is True
    assert tunnel.spec["is_reverse"] is True
    assert tunnel.spec["force_direct"] is False


def test_frp_node_specs_respects_force_direct():
    """Verify build_frp_node_specs computes is_reverse=False when force_direct=True."""
    tunnel = DummyTunnel(
        id="tun-frp-dir",
        core="frp",
        type="tcp",
        is_reverse=None,
        spec={"ports": [8443], "bind_port": 7000, "force_direct": True}
    )
    iran_ip = "194.5.200.10"
    foreign_ip = "185.10.50.20"

    iran_spec, foreign_spec = build_frp_node_specs(tunnel, iran_ip, foreign_ip)

    # In direct mode: Foreign is Server & Provider, Iran is Client Visitor
    assert foreign_spec["mode"] == "server"
    assert foreign_spec["is_provider"] is True
    assert foreign_spec["is_reverse"] is False
    assert foreign_spec["force_direct"] is True

    assert iran_spec["mode"] == "client"
    assert iran_spec["is_visitor"] is True
    assert iran_spec["is_reverse"] is False
    assert iran_spec["force_direct"] is True

    assert tunnel.is_reverse is False
    assert tunnel.spec["is_reverse"] is False
    assert tunnel.spec["force_direct"] is True


def test_frp_node_specs_reverse_mode_sets_server_spec_is_reverse():
    """Verify build_frp_node_specs explicitly sets server_spec['is_reverse']=True and force_direct=False."""
    tunnel = DummyTunnel(
        id="tun-frp-rev",
        core="frp",
        type="tcp",
        is_reverse=True,
        spec={"ports": [8443], "bind_port": 7000}
    )
    iran_ip = "194.5.200.10"
    foreign_ip = "185.10.50.20"

    iran_spec, foreign_spec = build_frp_node_specs(tunnel, iran_ip, foreign_ip)

    # In reverse mode: Iran is Server (frps), Foreign is Client (frpc)
    assert iran_spec["mode"] == "server"
    assert iran_spec["is_reverse"] is True
    assert iran_spec["force_direct"] is False

    assert foreign_spec["mode"] == "client"
    assert foreign_spec["is_reverse"] is True
    assert foreign_spec["force_direct"] is False

    assert tunnel.is_reverse is True
    assert tunnel.spec["is_reverse"] is True
    assert tunnel.spec["force_direct"] is False


@pytest.mark.asyncio
async def test_persist_generated_spec_commits_when_directionality_mutated():
    """Verify persist_generated_spec saves mutations when spec_snapshot was taken before directionality resolution."""
    from panel.app.spec_builder import persist_generated_spec

    tunnel = DummyTunnel(
        id="tun-sync-test",
        core="frp",
        spec={"ports": [8080], "token": "static-token", "bind_port": 7000},
        is_reverse=None
    )

    # 1. Simulate panel startup snapshot taken BEFORE directionality resolution
    initial_snapshot = copy.deepcopy(tunnel.spec)

    # 2. Harmonized resolution block runs
    tunnel.is_reverse = True
    tunnel.spec["is_reverse"] = True
    tunnel.spec["force_direct"] = False

    # 3. persist_generated_spec is called
    mock_session = AsyncMock()
    mock_session.is_modified = MagicMock(return_value=False)
    committed = await persist_generated_spec(mock_session, tunnel, initial_snapshot)

    assert committed is True
    assert mock_session.commit.called


@pytest.mark.asyncio
async def test_persist_generated_spec_commits_when_session_marks_dirty():
    """Verify persist_generated_spec commits when session detects model is dirty even if spec dict matches snapshot."""
    from panel.app.spec_builder import persist_generated_spec

    tunnel = DummyTunnel(
        id="tun-dirty-test",
        core="gost",
        spec={"ports": [8080]},
        is_reverse=None
    )
    spec_snapshot = copy.deepcopy(tunnel.spec)
    tunnel.is_reverse = False

    mock_session = AsyncMock()
    mock_session.is_modified = MagicMock(return_value=True)

    committed = await persist_generated_spec(mock_session, tunnel, spec_snapshot)
    assert committed is True
    assert mock_session.commit.called


def test_chisel_spec_builder_persists_custom_sni_without_is_reverse_attribute():
    """Verify chisel spec builder synchronizes custom_sni into spec even if tunnel has no is_reverse attribute."""
    class NoIsReverseTunnel:
        def __init__(self):
            self.id = "tun-no-attr"
            self.core = "chisel"
            self.type = "tcp"
            self.spec = {
                "ports": [8080],
                "custom_sni": "stealth.cloudflare.com",
                "custom_host": "stealth.cloudflare.com",
                "backend_url": "https://example.com"
            }

    tunnel = NoIsReverseTunnel()
    iran_spec, foreign_spec = build_chisel_node_specs(tunnel, "10.0.0.1", "10.0.0.2")

    # Custom SNI and backend URL must be preserved in spec
    assert tunnel.spec.get("custom_sni") == "stealth.cloudflare.com"
    assert tunnel.spec.get("custom_host") == "stealth.cloudflare.com"
    assert tunnel.spec.get("backend_url") == "https://example.com"


def test_string_boolean_force_direct_in_spec_builder():
    """Verify string boolean 'false' does NOT trigger force_direct, while 'true' does."""
    from panel.app.spec_builder import _resolve_tunnel_directionality

    # 'false' string should NOT force direct mode
    tunnel_false = DummyTunnel(
        id="tun-str-false",
        core="frp",
        spec={"force_direct": "false"}
    )
    assert _resolve_tunnel_directionality(tunnel_false, tunnel_false.spec) is True

    # 'true' string SHOULD force direct mode
    tunnel_true = DummyTunnel(
        id="tun-str-true",
        core="frp",
        spec={"force_direct": "true"}
    )
    assert _resolve_tunnel_directionality(tunnel_true, tunnel_true.spec) is False


def test_rathole_replaces_legacy_mock_keys_with_valid_base64():
    """Verify build_rathole_node_specs overwrites existing legacy mock strings with valid 32-byte keys."""
    tunnel = DummyTunnel(
        id="tun-rathole-mock-repair",
        core="rathole",
        spec={
            "use_noise": True,
            "ports": [8080],
            "server_private_key": "mock-server-priv",
            "server_public_key": "mock-server-pub",
            "client_private_key": "mock-client-priv",
            "client_public_key": "mock-client-pub",
        }
    )

    s_spec, c_spec = build_rathole_node_specs(tunnel, "10.0.0.1", "10.0.0.2")

    for k in ["server_private_key", "server_public_key", "client_private_key", "client_public_key"]:
        key_val = tunnel.spec[k]
        assert "mock" not in key_val
        raw = base64.b64decode(key_val)
        assert len(raw) == 32


@pytest.mark.asyncio
async def test_inspect_tunnel_health_supports_string_and_explicit_flags():
    """Verify inspect_tunnel_health respects explicit is_reverse/force_direct arguments and string booleans."""
    manager = AdapterManager()
    tunnel_id = "tun-str-health-test"
    manager.tunnel_configs[tunnel_id] = {
        "core": "frp",
        "spec": {
            "mode": "server",
            "force_direct": "false",  # string 'false' must NOT force direct
            "bind_port": 7000,
            "ports": [8080],
        }
    }

    # In reverse mode (force_direct is false), service port 8080 MUST be verified
    with patch("node.app.core_adapters.is_port_listening_locally", return_value=False), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="server",
            ports=[8080],
            control_port=7000,
            proto="tcp"
        )
        assert st["healthy"] is False
        assert any(p["port"] == 8080 for p in st["missing_ports"])

    # Now pass explicit force_direct=True argument: service ports should be skipped on server
    with patch("node.app.core_adapters.is_port_listening_locally", return_value=True), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st_direct = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="server",
            ports=[8080],
            control_port=7000,
            proto="tcp",
            force_direct=True
        )
        assert st_direct["healthy"] is True
        assert len(st_direct["missing_ports"]) == 0


# ===========================================================================
# 2. FRP Reverse Server Health Check Blindness Fix
# ===========================================================================

@pytest.mark.asyncio
async def test_inspect_tunnel_health_frp_reverse_server_checks_service_ports():
    """Verify FRP reverse server inspects service ports on the entry node (not skipped)."""
    manager = AdapterManager()
    tunnel_id = "tun-frp-rev-health"

    manager.tunnel_configs[tunnel_id] = {
        "core": "frp",
        "spec": {
            "mode": "server",
            "is_reverse": True,
            "bind_port": 7000,
            "ports": [8080, 8081],
        }
    }

    # Simulate: bind_port 7000 and service port 8080 are listening, but 8081 is NOT listening
    def fake_is_listening(port, proto="tcp"):
        return port in (7000, 8080)

    with patch("node.app.core_adapters.is_port_listening_locally", side_effect=fake_is_listening), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="server",
            ports=[8080, 8081],
            control_port=7000,
            proto="tcp"
        )

        assert st["process_running"] is True
        # Since port 8081 is down, tunnel must be reported UNHEALTHY
        assert st["healthy"] is False

        listening_ports = [p["port"] for p in st["listening_ports"]]
        assert 7000 in listening_ports
        assert 8080 in listening_ports

        missing_ports = [p["port"] for p in st["missing_ports"]]
        assert 8081 in missing_ports


@pytest.mark.asyncio
async def test_inspect_tunnel_health_frp_reverse_server_default_directionality():
    """Verify FRP reverse server with omitted is_reverse in spec defaults to reverse and checks service ports."""
    manager = AdapterManager()
    tunnel_id = "tun-frp-rev-default"

    # spec has NO 'is_reverse' and NO 'force_direct' (standard multi-node default)
    manager.tunnel_configs[tunnel_id] = {
        "core": "frp",
        "spec": {
            "mode": "server",
            "bind_port": 7000,
            "ports": [9090],
        }
    }

    # Simulate: control port 7000 is listening, service port 9090 is missing
    def fake_is_listening(port, proto="tcp"):
        return port == 7000

    with patch("node.app.core_adapters.is_port_listening_locally", side_effect=fake_is_listening), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="server",
            ports=[9090],
            control_port=7000,
            proto="tcp"
        )

        assert st["process_running"] is True
        # Service port 9090 must NOT be skipped; it should be flagged missing
        assert st["healthy"] is False
        assert any(p["port"] == 9090 for p in st["missing_ports"])


@pytest.mark.asyncio
async def test_inspect_tunnel_health_frp_direct_server_skips_service_ports():
    """Verify FRP direct server (foreign) skips service ports and only validates control_port."""
    manager = AdapterManager()
    tunnel_id = "tun-frp-direct-foreign"

    manager.tunnel_configs[tunnel_id] = {
        "core": "frp",
        "spec": {
            "mode": "server",
            "force_direct": True,
            "is_reverse": False,
            "bind_port": 7000,
            "ports": [9090],  # Port 9090 is on Iran client, NOT foreign server
        }
    }

    # Mock: only 7000 is listening, 9090 is not
    def fake_is_listening(port, proto="tcp"):
        return port == 7000

    with patch("node.app.core_adapters.is_port_listening_locally", side_effect=fake_is_listening), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="server",
            ports=[9090],
            control_port=7000,
            proto="tcp"
        )

        assert st["process_running"] is True
        # Must be healthy because foreign server in direct mode only terminates control_port
        assert st["healthy"] is True
        assert len(st["missing_ports"]) == 0
        listening_ports = [p["port"] for p in st["listening_ports"]]
        assert 7000 in listening_ports
        assert 9090 not in listening_ports


@pytest.mark.asyncio
async def test_inspect_tunnel_health_frp_reverse_client_skips_service_ports():
    """Verify FRP reverse client (foreign) does not attempt to bind/inspect service ports locally."""
    manager = AdapterManager()
    tunnel_id = "tun-frp-rev-foreign-client"

    manager.tunnel_configs[tunnel_id] = {
        "core": "frp",
        "spec": {
            "mode": "client",
            "is_reverse": True,
            "ports": [8080],
        }
    }

    with patch("node.app.core_adapters.is_port_listening_locally", return_value=False), \
         patch("node.app.core_adapters._is_tunnel_pid_alive", return_value=True):
        st = await manager.inspect_tunnel_health(
            tunnel_id=tunnel_id,
            tunnel_core="frp",
            mode="client",
            ports=[8080],
            proto="tcp"
        )

        assert st["process_running"] is True
        # Foreign client in reverse mode does NOT listen on service ports
        assert st["healthy"] is True
        assert len(st["missing_ports"]) == 0


# ===========================================================================
# 3. Fix Rathole Noise Protocol Fallback Keys
# ===========================================================================

def test_safe_noise_keypair_returns_valid_32_byte_base64():
    """Verify _safe_noise_keypair returns valid 32-byte Base64-encoded keys."""
    priv_b64, pub_b64 = _safe_noise_keypair()

    assert isinstance(priv_b64, str)
    assert isinstance(pub_b64, str)

    priv_bytes = base64.b64decode(priv_b64)
    pub_bytes = base64.b64decode(pub_b64)

    assert len(priv_bytes) == 32, f"Expected 32 bytes, got {len(priv_bytes)}"
    assert len(pub_bytes) == 32, f"Expected 32 bytes, got {len(pub_bytes)}"


def test_safe_noise_keypair_fallback_when_utils_unavailable(monkeypatch):
    """Verify fallback to local generation or os.urandom when generate_noise_keypair is None."""
    import panel.app.spec_builder as sb
    monkeypatch.setattr(sb, "generate_noise_keypair", None)

    priv_b64, pub_b64 = sb._safe_noise_keypair()

    priv_bytes = base64.b64decode(priv_b64)
    pub_bytes = base64.b64decode(pub_b64)

    assert len(priv_bytes) == 32
    assert len(pub_bytes) == 32


def test_safe_noise_keypair_fallback_when_cryptography_fails(monkeypatch):
    """Verify fallback to os.urandom if cryptography raises an exception."""
    import panel.app.spec_builder as sb
    monkeypatch.setattr(sb, "generate_noise_keypair", None)

    with patch("cryptography.hazmat.primitives.asymmetric.x25519.X25519PrivateKey.generate", side_effect=RuntimeError("crypto error")):
        priv_b64, pub_b64 = sb._safe_noise_keypair()

        priv_bytes = base64.b64decode(priv_b64)
        pub_bytes = base64.b64decode(pub_b64)

        assert len(priv_bytes) == 32
        assert len(pub_bytes) == 32


def test_build_rathole_node_specs_never_outputs_mock_strings(monkeypatch):
    """Verify build_rathole_node_specs generates valid 32-byte base64 keys and never mock strings."""
    import panel.app.spec_builder as sb
    # Simulate generate_noise_keypair being unavailable
    monkeypatch.setattr(sb, "generate_noise_keypair", None)

    tunnel = DummyTunnel(
        id="tun-rathole-noise",
        core="rathole",
        type="tcp",
        spec={"use_noise": True, "ports": [8080]}
    )

    server_spec, client_spec = sb.build_rathole_node_specs(tunnel, "10.0.0.1", "10.0.0.2")

    for key_name in ["server_private_key", "server_public_key", "client_private_key", "client_public_key"]:
        val = tunnel.spec.get(key_name)
        assert val is not None
        assert "mock" not in val
        raw = base64.b64decode(val)
        assert len(raw) == 32

    assert "mock" not in server_spec.get("local_private_key", "")
    assert "mock" not in server_spec.get("remote_public_key", "")
    assert "mock" not in client_spec.get("local_private_key", "")
    assert "mock" not in client_spec.get("remote_public_key", "")


@pytest.mark.asyncio
async def test_check_port_conflicts_respects_force_direct_for_chisel_and_frp():
    """Verify check_port_conflicts does not mistakenly treat force_direct Chisel/FRP as reverse tunnels."""
    from panel.app.routers.tunnels import check_port_conflicts
    from fastapi import HTTPException

    class MockScalarResult:
        def __init__(self, items):
            self._items = items
        def all(self):
            return self._items

    class MockQueryResult:
        def __init__(self, items):
            self._items = items
        def scalars(self):
            return MockScalarResult(self._items)
        def scalar_one_or_none(self):
            return None

    # Existing tunnel: chisel in direct mode (foreign is server binding control_port 7000, iran is client binding svc 8080)
    existing = DummyTunnel(
        id="ex-tun-chisel-dir",
        name="ChiselDirect",
        core="chisel",
        is_reverse=None,  # column not populated yet
        foreign_node_id="foreign-node",
        iran_node_id="iran-node",
        spec={"force_direct": True, "control_port": 7000, "ports": [8080]}
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MockQueryResult([existing]))

    # Checking control_port 7000 on IRAN node for a new tunnel should NOT clash because
    # in direct mode, control port 7000 is bound on the FOREIGN node.
    await check_port_conflicts(
        db=mock_db,
        spec={"control_port": 7000},
        iran_node_id="iran-node",
        foreign_node_id="foreign-node-2",
        node_id="iran-node",
        core="gost"
    )

    # But checking control_port 7000 on the FOREIGN node MUST raise HTTPException (conflict)
    with pytest.raises(HTTPException) as exc_info:
        await check_port_conflicts(
            db=mock_db,
            spec={"control_port": 7000},
            iran_node_id="iran-node-2",
            foreign_node_id="foreign-node",
            node_id="foreign-node",
            core="gost",
            is_reverse=False
        )
    assert exc_info.value.status_code == 400
    assert "7000" in exc_info.value.detail


def test_gost_node_specs_respects_force_direct_and_string_booleans():
    """Verify build_gost_node_specs harmonizes with Chisel/FRP and respects string booleans."""
    from panel.app.spec_builder import build_gost_node_specs

    # 1. force_direct = True forces direct mode (Iran is client, Foreign is server)
    tun_dir = DummyTunnel(id="tun-gost-dir", core="gost", type="tcp", spec={"force_direct": True, "ports": [8080]})
    iran_spec, foreign_spec = build_gost_node_specs(tun_dir, "10.0.0.1", "10.0.0.2")
    assert iran_spec["mode"] == "client"
    assert iran_spec["is_reverse"] is False
    assert iran_spec["force_direct"] is True
    assert foreign_spec["mode"] == "server"
    assert foreign_spec["is_reverse"] is False
    assert foreign_spec["force_direct"] is True

    # 2. String boolean "true" forces direct mode
    tun_str_true = DummyTunnel(id="tun-gost-str-true", core="gost", type="tcp", spec={"force_direct": "true", "ports": [8080]})
    iran_spec, foreign_spec = build_gost_node_specs(tun_str_true, "10.0.0.1", "10.0.0.2")
    assert iran_spec["is_reverse"] is False
    assert iran_spec["force_direct"] is True

    # 3. String boolean "false" does NOT force direct mode (defaults to reverse for multi-node)
    tun_str_false = DummyTunnel(
        id="tun-gost-str-false",
        core="gost",
        type="tcp",
        foreign_node_id="foreign-node",
        iran_node_id="iran-node",
        spec={"force_direct": "false", "ports": [8080]}
    )
    iran_spec, foreign_spec = build_gost_node_specs(tun_str_false, "10.0.0.1", "10.0.0.2")
    assert iran_spec["mode"] == "server"
    assert iran_spec["is_reverse"] is True
    assert iran_spec["force_direct"] is False

    # 4. is_reverse = "false" string forces direct mode
    tun_rev_false = DummyTunnel(
        id="tun-gost-rev-false",
        core="gost",
        type="tcp",
        is_reverse="false",
        foreign_node_id="foreign-node",
        iran_node_id="iran-node",
        spec={"ports": [8080]}
    )
    iran_spec, foreign_spec = build_gost_node_specs(tun_rev_false, "10.0.0.1", "10.0.0.2")
    assert iran_spec["is_reverse"] is False
    assert iran_spec["force_direct"] is True


@pytest.mark.asyncio
async def test_check_port_conflicts_respects_existing_tunnel_force_direct_over_db_is_reverse():
    """Verify check_port_conflicts respects force_direct even if DB row had is_reverse=True."""
    from panel.app.routers.tunnels import check_port_conflicts
    from fastapi import HTTPException

    class MockScalarResult:
        def __init__(self, items):
            self._items = items
        def all(self):
            return self._items

    class MockQueryResult:
        def __init__(self, items):
            self._items = items
        def scalars(self):
            return MockScalarResult(self._items)
        def scalar_one_or_none(self):
            return None

    # Tunnel was previously marked is_reverse=True in DB, but its spec has force_direct=True
    existing = DummyTunnel(
        id="ex-direct-overrides-db",
        name="DirectWithStaleDbFlag",
        core="frp",
        is_reverse=True,  # stale DB column
        foreign_node_id="foreign-node",
        iran_node_id="iran-node",
        spec={"force_direct": True, "control_port": 7500, "ports": [8080]}
    )

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MockQueryResult([existing]))

    # Control port 7500 is bound on foreign node in direct mode, so checking Iran node should succeed
    await check_port_conflicts(
        db=mock_db,
        spec={"control_port": 7500},
        iran_node_id="iran-node",
        foreign_node_id="other-foreign",
        node_id="iran-node",
        core="gost"
    )

    # Checking Foreign node MUST detect collision on control port 7500
    with pytest.raises(HTTPException) as exc_info:
        await check_port_conflicts(
            db=mock_db,
            spec={"control_port": 7500},
            iran_node_id="other-iran",
            foreign_node_id="foreign-node",
            node_id="foreign-node",
            core="gost",
            is_reverse=False
        )
    assert exc_info.value.status_code == 400
    assert "7500" in exc_info.value.detail


def test_rathole_noise_keys_distinctness():
    """Verify build_rathole_node_specs regenerates keys when server and client keys are identical."""
    import panel.app.spec_builder as sb
    tunnel = DummyTunnel(
        id="tun-rathole-collision",
        core="rathole",
        type="tcp",
        spec={
            "use_noise": True,
            "ports": [8080],
            # Pass same key for local and remote
            "local_private_key": base64.b64encode(b"A" * 32).decode("utf-8"),
            "remote_public_key": base64.b64encode(b"B" * 32).decode("utf-8")
        }
    )

    server_spec, client_spec = sb.build_rathole_node_specs(tunnel, "10.0.0.1", "10.0.0.2")

    # Keys must be regenerated and distinct
    assert server_spec["local_private_key"] != client_spec["local_private_key"]
    assert server_spec["remote_public_key"] != client_spec["remote_public_key"]
    assert tunnel.spec["server_private_key"] != tunnel.spec["client_private_key"]
    assert tunnel.spec["server_public_key"] != tunnel.spec["client_public_key"]


@pytest.mark.asyncio
async def test_node_client_verify_tunnel_forwards_is_reverse_and_force_direct():
    """Verify NodeClient.verify_tunnel_on_node forwards is_reverse and force_direct when passed."""
    from panel.app.node_client import NodeClient
    client = NodeClient()
    client.send_to_node = AsyncMock(return_value={"status": "success", "data": {"healthy": True}})

    await client.verify_tunnel_on_node(
        node_id="node-123",
        tunnel_id="tunnel-789",
        core="frp",
        mode="client",
        ports=[8080],
        control_port=7000,
        proto="tcp",
        is_reverse=False,
        force_direct=True
    )

    client.send_to_node.assert_called_once_with(
        "node-123",
        "/api/agent/tunnels/verify",
        {
            "tunnel_id": "tunnel-789",
            "core": "frp",
            "mode": "client",
            "ports": [8080],
            "control_port": 7000,
            "proto": "tcp",
            "is_reverse": False,
            "force_direct": True
        }
    )


