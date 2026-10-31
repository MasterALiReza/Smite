import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.spec_builder import (
    build_tunnel_node_specs,
    build_rathole_node_specs,
    parse_ports_list,
    is_valid_ipv6,
)
from app.models import utcnow, Node, Tunnel, Settings, CoreResetConfig
from node.app.core_adapters import (
    AdapterManager,
    RatholeAdapter,
    FrpAdapter,
    BackhaulAdapter,
)


class DummyTunnel:
    def __init__(self, id="tun-test-1", core="rathole", type="tcp", spec=None, is_reverse=None, **kwargs):
        self.id = id
        self.core = core
        self.type = type
        self.spec = spec or {}
        self.is_reverse = is_reverse
        for k, v in kwargs.items():
            setattr(self, k, v)


# ===========================================================================
# 1. Watchdog Loop Fast-Crash Backoff Stabilization
# ===========================================================================

@pytest.mark.asyncio
async def test_watchdog_loop_backoff_stabilization_and_fast_crash(tmp_path):
    """Verify that auto-recovering a tunnel does not immediately reset backoff,
    and that a rapid crash retains backoff and increments failures."""
    mgr = AdapterManager()
    mgr.config_dir = tmp_path
    mgr.tunnels_file = tmp_path / "tunnels.json"
    tunnel_id = "tun-flap-1"
    mgr.tunnel_configs[tunnel_id] = {
        "core": "rathole",
        "spec": {"mode": "server", "token": "tok", "ports": [8080], "control_port": 25000}
    }

    mock_adapter = MagicMock()
    # Initially dead
    mock_adapter.status.return_value = {"process_running": False, "active": False}
    mock_adapter.apply = AsyncMock()
    mock_adapter.remove = AsyncMock()
    mgr.get_adapter = MagicMock(return_value=mock_adapter)

    current_time = 1000.0

    def fake_time():
        nonlocal current_time
        return current_time

    cycle_count = 0

    async def controlled_sleep(secs):
        nonlocal cycle_count, current_time
        if secs == 15:
            cycle_count += 1
            if cycle_count == 1:
                # Tick 1: at t=1000, tunnel is dead, should attempt recovery
                current_time = 1000.0
            elif cycle_count == 2:
                # Tick 2: at t=1010 (10s later, before 30s backoff window), tunnel crashed again!
                current_time = 1010.0
                mock_adapter.status.return_value = {"process_running": False, "active": False}
            elif cycle_count == 3:
                # Tick 3: at t=1035 (35s later, after backoff window of 30s), attempt 2 should fire
                current_time = 1035.0
                mock_adapter.status.return_value = {"process_running": False, "active": False}
            elif cycle_count == 4:
                # Stop watchdog
                raise asyncio.CancelledError()
        return

    with patch("time.time", side_effect=fake_time), \
         patch("asyncio.sleep", side_effect=controlled_sleep), \
         patch("random.uniform", return_value=1.0):

        try:
            await mgr._watchdog_loop()
        except asyncio.CancelledError:
            pass

        # At cycle 1 (t=1000): apply called (attempt 1) -> backoff is min(15*2, 120)=30s, next_retry_at = 1000+30=1030
        # At cycle 2 (t=1010): tunnel dead, but now(1010) < next_retry_at(1030) -> SKIPPED recovery (no flap!)
        # At cycle 3 (t=1035): now(1035) >= next_retry_at(1030) -> apply called (attempt 2), backoff becomes min(30*2, 120)=60s
        assert mock_adapter.apply.call_count == 2


@pytest.mark.asyncio
async def test_watchdog_loop_30s_survival_clears_backoff(tmp_path):
    """Verify that surviving healthy for at least 30s resets backoff state."""
    mgr = AdapterManager()
    mgr.config_dir = tmp_path
    mgr.tunnels_file = tmp_path / "tunnels.json"
    tunnel_id = "tun-healthy-1"
    mgr.tunnel_configs[tunnel_id] = {
        "core": "rathole",
        "spec": {"mode": "server", "token": "tok", "ports": [8080], "control_port": 25000}
    }

    mock_adapter = MagicMock()
    mock_adapter.status.return_value = {"process_running": False, "active": False}
    mock_adapter.apply = AsyncMock()
    mock_adapter.remove = AsyncMock()
    mgr.get_adapter = MagicMock(return_value=mock_adapter)

    current_time = 1000.0

    def fake_time():
        nonlocal current_time
        return current_time

    cycle_count = 0

    async def controlled_sleep(secs):
        nonlocal cycle_count, current_time
        if secs == 15:
            cycle_count += 1
            if cycle_count == 1:
                # Cycle 1: dead -> revived at t=1000
                current_time = 1000.0
            elif cycle_count == 2:
                # Cycle 2: alive at t=1015 (15s survived, < 30s) -> should NOT reset backoff yet
                current_time = 1015.0
                mock_adapter.status.return_value = {"process_running": True, "active": True}
            elif cycle_count == 3:
                # Cycle 3: alive at t=1035 (35s survived, >= 30s) -> should RESET backoff
                current_time = 1035.0
                mock_adapter.status.return_value = {"process_running": True, "active": True}
            elif cycle_count == 4:
                # Cycle 4: at t=1050, tunnel dies! Because backoff was reset, retry fires
                current_time = 1050.0
                mock_adapter.status.return_value = {"process_running": False, "active": False}
            elif cycle_count == 5:
                raise asyncio.CancelledError()
        return

    with patch("time.time", side_effect=fake_time), \
         patch("asyncio.sleep", side_effect=controlled_sleep), \
         patch("random.uniform", return_value=1.0):

        try:
            await mgr._watchdog_loop()
        except asyncio.CancelledError:
            pass

        # Applied at cycle 1 (revived), healthy at cycle 2 & 3 (reset), re-applied at cycle 4
        assert mock_adapter.apply.call_count == 2


# ===========================================================================
# 2. Rathole Port Ranges Expansion & Server/Client Spec Harmonization
# ===========================================================================

def test_rathole_spec_builder_port_ranges_expansion():
    """Verify build_rathole_node_specs expands port_ranges into ports list and propagates to both specs."""
    tunnel = DummyTunnel(
        id="tun-rathole-range",
        core="rathole",
        type="tcp",
        port_ranges="8000-8005",
        spec={"token": "test-token"}
    )
    iran_ip = "10.0.0.1"
    foreign_ip = "10.0.0.2"

    server_spec, client_spec = build_rathole_node_specs(tunnel, iran_ip, foreign_ip)

    expected_ports = [8000, 8001, 8002, 8003, 8004, 8005]
    assert server_spec["port_ranges"] == "8000-8005"
    assert client_spec["port_ranges"] == "8000-8005"
    assert server_spec["ports"] == expected_ports
    assert client_spec["ports"] == expected_ports


@pytest.mark.asyncio
async def test_rathole_adapter_port_ranges_expansion_server_and_client(tmp_path):
    """Verify RatholeAdapter.apply expands port_ranges and generates TOML services for all ports."""
    adapter = RatholeAdapter()
    adapter.config_dir = tmp_path
    adapter.remove = AsyncMock()

    spec_server = {
        "mode": "server",
        "token": "tok123",
        "port_ranges": "8000-8005",
        "bind_addr": "0.0.0.0:25000",
        "tunnel_type": "tcp",
    }

    freed_ports = []

    async def mock_free_port(p):
        freed_ports.append(p)

    with patch("node.app.core_adapters.free_port", side_effect=mock_free_port), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=123, returncode=None)

        await adapter.apply("test_rathole_srv", spec_server)

        # Check all ports in range were freed
        for p in range(8000, 8006):
            assert p in freed_ports

        # Inspect generated TOML
        toml_path = tmp_path / "test_rathole_srv.toml"
        assert toml_path.exists()
        content = toml_path.read_text(encoding="utf-8")
        for i, p in enumerate(range(8000, 8006)):
            assert f"[server.services.test_rathole_srv_{i}]" in content
            assert f'bind_addr = "0.0.0.0:{p}"' in content

    # Test Client side
    spec_client = {
        "mode": "client",
        "token": "tok123",
        "port_ranges": ["8000-8005"],
        "remote_addr": "1.2.3.4:25000",
        "tunnel_type": "tcp",
        "target_host": "127.0.0.1",
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=124, returncode=None)

        await adapter.apply("test_rathole_cli", spec_client)

        toml_path = tmp_path / "test_rathole_cli.toml"
        assert toml_path.exists()
        content = toml_path.read_text(encoding="utf-8")
        for i, p in enumerate(range(8000, 8006)):
            assert f"[client.services.test_rathole_cli_{i}]" in content
            assert f'local_addr = "127.0.0.1:{p}"' in content


# ===========================================================================
# 3. IPv6 Systemic Propagation Across Cores
# ===========================================================================

def test_ipv6_systemic_propagation_in_build_tunnel_node_specs():
    """Verify build_tunnel_node_specs propagates use_ipv6=True when node IP or spec has IPv6."""
    tunnel_v4 = DummyTunnel(id="tun-v4", core="rathole", spec={"ports": [8080]})
    server_spec, client_spec = build_tunnel_node_specs(tunnel_v4, "1.2.3.4", "5.6.7.8")
    assert server_spec.get("use_ipv6") is not True

    # Iran node is IPv6
    server_spec, client_spec = build_tunnel_node_specs(tunnel_v4, "2001:db8::1", "5.6.7.8")
    assert server_spec["use_ipv6"] is True
    assert client_spec["use_ipv6"] is True

    # Foreign node is IPv6
    server_spec, client_spec = build_tunnel_node_specs(tunnel_v4, "1.2.3.4", "2001:db8::2")
    assert server_spec["use_ipv6"] is True
    assert client_spec["use_ipv6"] is True

    # Tunnel spec has use_ipv6
    tunnel_v6 = DummyTunnel(id="tun-v6", core="rathole", spec={"ports": [8080], "use_ipv6": True})
    server_spec, client_spec = build_tunnel_node_specs(tunnel_v6, "1.2.3.4", "5.6.7.8")
    assert server_spec["use_ipv6"] is True
    assert client_spec["use_ipv6"] is True


@pytest.mark.asyncio
async def test_ipv6_bindings_in_adapters(tmp_path):
    """Verify IPv6 bind addresses in Rathole server/client, FRP visitor, and Backhaul server."""
    # 1. Rathole server IPv6 binding
    rathole_adapter = RatholeAdapter()
    rathole_adapter.config_dir = tmp_path
    rathole_adapter.remove = AsyncMock()

    spec_rathole_srv = {
        "mode": "server",
        "token": "tok",
        "ports": [9090],
        "bind_addr": "0.0.0.0:25000",
        "use_ipv6": True,
    }

    with patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=111, returncode=None)
        await rathole_adapter.apply("rathole_v6_srv", spec_rathole_srv)
        content = (tmp_path / "rathole_v6_srv.toml").read_text(encoding="utf-8")
        assert 'bind_addr = "[::]:9090"' in content

    # 2. Rathole client IPv6 binding
    spec_rathole_cli = {
        "mode": "client",
        "token": "tok",
        "ports": [9090],
        "remote_addr": "[2001:db8::1]:25000",
        "use_ipv6": True,
    }

    with patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=112, returncode=None)
        await rathole_adapter.apply("rathole_v6_cli", spec_rathole_cli)
        content = (tmp_path / "rathole_v6_cli.toml").read_text(encoding="utf-8")
        assert 'local_addr = "[::1]:9090"' in content

    # 3. FRP visitor IPv6 binding
    frp_adapter = FrpAdapter()
    frp_adapter.config_dir = tmp_path
    frp_adapter._resolve_binary_path = MagicMock(return_value=Path("/usr/local/bin/frpc"))
    frp_adapter._resolve_server_binary_path = MagicMock(return_value=Path("/usr/local/bin/frps"))
    frp_adapter.remove = AsyncMock()

    spec_frp_visitor = {
        "mode": "client",
        "is_visitor": True,
        "ports": [8080],
        "server_addr": "[2001:db8::1]",
        "server_port": 7000,
        "token": "tok",
        "secret_key": "sec",
        "use_ipv6": True,
    }

    with patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=113, returncode=None)
        await frp_adapter.apply("frp_v6_visitor", spec_frp_visitor)
        content = (tmp_path / "frpc_frp_v6_visitor.yaml").read_text(encoding="utf-8")
        assert 'bindAddr: "::"' in content

    # 4. Backhaul server IPv6 binding
    backhaul_adapter = BackhaulAdapter()
    backhaul_adapter.config_dir = tmp_path
    backhaul_adapter._resolve_binary_path = MagicMock(return_value=Path("/usr/local/bin/backhaul"))
    backhaul_adapter.remove = AsyncMock()

    spec_backhaul_srv = {
        "mode": "server",
        "control_port": 3080,
        "ports": [8080],
        "token": "tok",
        "use_ipv6": True,
    }

    with patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=114, returncode=None)
        await backhaul_adapter.apply("backhaul_v6_srv", spec_backhaul_srv)
        content = (tmp_path / "backhaul_v6_srv.toml").read_text(encoding="utf-8")
        assert 'bind_addr = "[::]:3080"' in content


# ===========================================================================
# 4. FRP VHost Web Ports Freeing
# ===========================================================================

@pytest.mark.asyncio
async def test_frp_vhost_web_ports_freeing(tmp_path):
    """Verify FrpAdapter.apply adds vhostHTTPPort/vhostHTTPSPort to ports_to_free before free_ports."""
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    adapter._resolve_server_binary_path = MagicMock(return_value=Path("/usr/local/bin/frps"))
    adapter._resolve_client_binary_path = MagicMock(return_value=Path("/usr/local/bin/frpc"))
    adapter.remove = AsyncMock()

    freed_port_sets = []

    async def mock_free_ports(ports):
        freed_port_sets.append(set(ports))

    # HTTP vhost
    spec_http = {
        "mode": "server",
        "tunnel_type": "http",
        "bind_port": 7000,
        "vhost_http_port": 8080,
        "ports": [80],
        "token": "tok",
    }

    with patch("node.app.core_adapters.free_ports", side_effect=mock_free_ports), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=115, returncode=None)
        await adapter.apply("frp_http", spec_http)
        assert 8080 in freed_port_sets[-1]

    # HTTPS vhost
    spec_https = {
        "mode": "server",
        "tunnel_type": "https",
        "bind_port": 7000,
        "vhost_https_port": 8443,
        "ports": [443],
        "token": "tok",
    }

    with patch("node.app.core_adapters.free_ports", side_effect=mock_free_ports), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=116, returncode=None)
        await adapter.apply("frp_https", spec_https)
        assert 8443 in freed_port_sets[-1]


# ===========================================================================
# 5. FRP CA Certificate Cleanup & Diagnostic Log Guard
# ===========================================================================

@pytest.mark.asyncio
async def test_frp_ca_cert_cleanup_and_log_guard(tmp_path):
    """Verify FrpAdapter.remove cleans ca.pem and preserves logs when purge=False, but deletes logs when purge=True."""
    adapter = FrpAdapter()
    adapter.config_dir = tmp_path
    tunnel_id = "frp_cleanup_test"

    # Setup dummy files
    ca_file = tmp_path / f"frpc_{tunnel_id}_ca.pem"
    ca_file.write_text("DUMMY CA", encoding="utf-8")

    log_file = tmp_path / f"{tunnel_id}.log"
    log_file.write_text("DUMMY LOG", encoding="utf-8")

    provider_log = tmp_path / f"{tunnel_id}_provider.log"
    provider_log.write_text("PROVIDER LOG", encoding="utf-8")

    yaml_file = tmp_path / f"frpc_{tunnel_id}.yaml"
    yaml_file.write_text("bindPort: 7000", encoding="utf-8")

    with patch("node.app.core_adapters.safe_stop_subprocess", new_callable=AsyncMock), \
         patch("node.app.core_adapters.free_ports", new_callable=AsyncMock), \
         patch("node.app.core_adapters._get_verified_tunnel_pid", return_value=None), \
         patch("node.app.core_adapters._remove_tunnel_pid"):

        # 1. Normal remove (purge=False)
        await adapter.remove(tunnel_id, purge=False)

        # CA cert and YAML should be removed
        assert not ca_file.exists()
        assert not yaml_file.exists()
        # Diagnostic logs must be PRESERVED
        assert log_file.exists()
        assert provider_log.exists()

        # 2. Purge remove (purge=True)
        await adapter.remove(tunnel_id, purge=True)

        # Logs should now be deleted
        assert not log_file.exists()
        assert not provider_log.exists()


# ===========================================================================
# 6. Datetime Modernization (utcnow Helper)
# ===========================================================================

def test_utcnow_helper_and_model_defaults():
    """Verify utcnow returns naive UTC datetime and models use it."""
    t1 = utcnow()
    assert isinstance(t1, datetime)
    assert t1.tzinfo is None

    # Verify timestamp close to current UTC time
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now_utc_naive - t1).total_seconds()) < 2.0

    # Verify model Column default is utcnow
    assert Node.registered_at.default.arg.__name__ == "utcnow"
    assert Node.last_seen.default.arg.__name__ == "utcnow"
    assert Tunnel.created_at.default.arg.__name__ == "utcnow"
    assert Tunnel.updated_at.onupdate.arg.__name__ == "utcnow"
    assert Settings.created_at.default.arg.__name__ == "utcnow"
    assert Settings.updated_at.onupdate.arg.__name__ == "utcnow"
    assert CoreResetConfig.created_at.default.arg.__name__ == "utcnow"
    assert CoreResetConfig.updated_at.onupdate.arg.__name__ == "utcnow"

    # Execute default callable and verify return value
    default_val = Node.registered_at.default.arg(None)
    assert isinstance(default_val, datetime)
    assert default_val.tzinfo is None


# ===========================================================================
# 7. Edge Cases & Robustness Verification
# ===========================================================================

def test_parse_ports_list_complex_comma_ranges():
    """Verify parse_ports_list properly handles comma-separated ranges, mixed strings, and lists."""
    assert parse_ports_list("8000-8002, 9000-9002") == [8000, 8001, 8002, 9000, 9001, 9002]
    assert parse_ports_list("80, 443, 8000-8002") == [80, 443, 8000, 8001, 8002]
    assert parse_ports_list(["8000-8002, 9000-9002"]) == [8000, 8001, 8002, 9000, 9001, 9002]
    assert parse_ports_list("8080-8080") == [8080]
    assert parse_ports_list({"ports": ["8000-8002, 9000-9002"]}) == [8000, 8001, 8002, 9000, 9001, 9002]


def test_is_valid_ipv6_rejects_ipv4_ports():
    """Verify is_valid_ipv6 correctly rejects IPv4:port combinations and domains."""
    assert is_valid_ipv6("1.2.3.4:8080") is False
    assert is_valid_ipv6("192.168.1.1:443") is False
    assert is_valid_ipv6("node.example.com:443") is False
    assert is_valid_ipv6("localhost") is False
    assert is_valid_ipv6("127.0.0.1") is False

    assert is_valid_ipv6("::1") is True
    assert is_valid_ipv6("2001:db8::1") is True
    assert is_valid_ipv6("[2001:db8::1]:8080") is True
    assert is_valid_ipv6("fe80::1") is True


@pytest.mark.asyncio
async def test_rathole_and_backhaul_remove_frees_ipv6_ports(tmp_path):
    """Verify that Rathole and Backhaul remove regexes properly match and free IPv6 bound ports."""
    # 1. Rathole IPv6 remove
    rathole_adapter = RatholeAdapter()
    rathole_adapter.config_dir = tmp_path
    tunnel_id = "tun_rathole_v6"
    toml_path = tmp_path / f"{tunnel_id}.toml"
    toml_path.write_text(
        '[server]\nbind_addr = "[::]:9090"\n\n[server.services.srv_0]\nbind_addr = "[::]:9091"\n',
        encoding="utf-8"
    )

    freed = set()
    with patch("node.app.core_adapters.safe_stop_subprocess", new_callable=AsyncMock), \
         patch("node.app.core_adapters._get_verified_tunnel_pid", return_value=None), \
         patch("node.app.core_adapters._remove_tunnel_pid"), \
         patch("node.app.core_adapters.free_ports", side_effect=lambda pts: freed.update(pts)):

        await rathole_adapter.remove(tunnel_id, purge=True)
        assert 9090 in freed
        assert 9091 in freed

    # 2. Backhaul IPv6 remove
    backhaul_adapter = BackhaulAdapter()
    backhaul_adapter.config_dir = tmp_path
    b_tid = "tun_backhaul_v6"
    b_toml = tmp_path / f"{b_tid}.toml"
    b_toml.write_text(
        '[server]\nbind_addr = "[::]:3080"\ntransport = "tcpmux"\n',
        encoding="utf-8"
    )

    freed_b = set()
    with patch("node.app.core_adapters.safe_stop_subprocess", new_callable=AsyncMock), \
         patch("node.app.core_adapters._get_verified_tunnel_pid", return_value=None), \
         patch("node.app.core_adapters._remove_tunnel_pid"), \
         patch("node.app.core_adapters.free_ports", side_effect=lambda pts: freed_b.update(pts)):

        await backhaul_adapter.remove(b_tid, purge=True)
        assert 3080 in freed_b


@pytest.mark.asyncio
async def test_rathole_preflight_and_apply_with_port_ranges_only(tmp_path):
    """Verify RatholeAdapter preflight and apply succeed when spec has port_ranges and no ports list."""
    adapter = RatholeAdapter()
    adapter.config_dir = tmp_path
    adapter.remove = AsyncMock()

    spec = {
        "mode": "server",
        "token": "tok123",
        "port_ranges": "8000-8003",
        "bind_addr": "0.0.0.0:25000",
    }

    # preflight must not raise ValueError
    await adapter.preflight("tun_range_only", spec)

    with patch("node.app.core_adapters.free_port", new_callable=AsyncMock), \
         patch("node.app.core_adapters._spawn_core_subprocess", new_callable=AsyncMock) as mock_spawn, \
         patch("node.app.core_adapters._save_tunnel_pid"), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_spawn.return_value = MagicMock(pid=999, returncode=None)
        await adapter.apply("tun_range_only", spec)

        content = (tmp_path / "tun_range_only.toml").read_text(encoding="utf-8")
        for i, p in enumerate(range(8000, 8004)):
            assert f"[server.services.tun_range_only_{i}]" in content
            assert f'bind_addr = "0.0.0.0:{p}"' in content


@pytest.mark.asyncio
async def test_watchdog_already_running_tunnel_does_not_increment_failures(tmp_path):
    """Verify that if a tunnel is already running by the time the watchdog acquires the lock,
    it is not treated as revived and failure/backoff counters are not incremented."""
    mgr = AdapterManager()
    mgr.config_dir = tmp_path
    mgr.tunnels_file = tmp_path / "tunnels.json"
    tunnel_id = "tun-running-check"
    mgr.tunnel_configs[tunnel_id] = {
        "core": "rathole",
        "spec": {"mode": "server", "token": "tok", "ports": [8080], "control_port": 25000}
    }

    mock_adapter = MagicMock()
    # Initially status() reports False at loop start, but True inside the lock
    mock_adapter.status.side_effect = [
        {"process_running": False, "active": False},  # first check before sleep
        {"process_running": True, "active": True},   # check inside lock (applied by concurrent call)
        {"process_running": True, "active": True},
    ]
    mock_adapter.apply = AsyncMock()
    mgr.get_adapter = MagicMock(return_value=mock_adapter)

    cycle_count = 0
    async def controlled_sleep(secs):
        nonlocal cycle_count
        if secs == 15:
            cycle_count += 1
            if cycle_count >= 2:
                raise asyncio.CancelledError()
        return

    with patch("time.time", return_value=1000.0), \
         patch("asyncio.sleep", side_effect=controlled_sleep), \
         patch("random.uniform", return_value=0.0):

        try:
            await mgr._watchdog_loop()
        except asyncio.CancelledError:
            pass

        # apply should NOT have been called because it was already running inside the lock
        assert mock_adapter.apply.call_count == 0

