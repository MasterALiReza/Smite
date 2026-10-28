"""Tests for the fail-closed mutation guard and its wiring into automatic paths."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECRET_KEY", "0123456789abcdef" * 4)

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
panel_dir = repo_root / "panel"
if str(panel_dir) not in sys.path:
    sys.path.insert(0, str(panel_dir))

from app.config import settings
from app.mutation_guard import (
    GuardError,
    guard_path,
    is_engaged,
    mutation_guard,
    state,
)


@pytest.fixture(autouse=True)
def guard_workspace(tmp_path, monkeypatch):
    """Isolate the guard file, cert paths and cwd for every test."""
    monkeypatch.delenv("SMITE_DISABLE_MUTATIONS", raising=False)
    monkeypatch.chdir(tmp_path)
    certs = tmp_path / "certs"
    monkeypatch.setattr(settings, "mutation_guard_path", str(certs / ".mutation-guard.json"))
    monkeypatch.setattr(settings, "node_cert_path", str(certs / "ca.crt"))
    monkeypatch.setattr(settings, "node_key_path", str(certs / "ca.key"))
    monkeypatch.setattr(settings, "node_server_cert_path", str(certs / "ca-server.crt"))
    monkeypatch.setattr(settings, "node_server_key_path", str(certs / "ca-server.key"))
    return tmp_path


class FakeRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, node=None, rows=None):
        self._node = node
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._node

    def scalars(self):
        return FakeRows(self._rows)


class FakeSession:
    def __init__(self, node):
        self.node = node

    async def execute(self, *_args, **_kwargs):
        return FakeResult(node=self.node, rows=["tunnel-1"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class FakeHTTPResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"status": "ok"}


class FakeAsyncClient:
    payloads = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).payloads.append({"url": url, "json": json, "headers": headers})
        return FakeHTTPResponse()


def _write_guard_file(payload, path=None):
    path = path or guard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------- core guard


def test_missing_file_is_not_engaged():
    current = state()
    assert current["engaged"] is False
    assert current["source"] == "missing"
    assert is_engaged() is False
    assert mutation_guard.is_engaged() is False


def test_engage_requires_reason_and_valid_expiry():
    with pytest.raises(GuardError):
        mutation_guard.engage("   ")
    with pytest.raises(GuardError):
        mutation_guard.engage("drill", expires_in_minutes=0)
    with pytest.raises(GuardError):
        mutation_guard.engage("drill", expires_in_minutes=-5)
    with pytest.raises(GuardError):
        mutation_guard.engage("x" * 501)
    assert not guard_path().exists()


def test_engage_writes_audit_record_and_blocks():
    before = datetime.now(timezone.utc)
    result = mutation_guard.engage("database restore drill", owner="ops", expires_in_minutes=30)
    after = datetime.now(timezone.utc)

    assert result["engaged"] is True
    assert result["source"] == "file"
    assert result["reason"] == "database restore drill"
    assert result["owner"] == "ops"
    assert result["expired"] is False
    assert is_engaged() is True

    engaged_at = datetime.fromisoformat(result["engaged_at"])
    expires_at = datetime.fromisoformat(result["expires_at"])
    assert before <= engaged_at <= after
    assert timedelta(minutes=29) < (expires_at - engaged_at) <= timedelta(minutes=30)

    record = json.loads(guard_path().read_text(encoding="utf-8"))
    assert record["schema"] == 1
    assert record["engaged"] is True
    assert record["released_at"] is None

    if os.name != "nt":
        assert (guard_path().stat().st_mode & 0o777) == 0o600


def test_owner_is_truncated():
    result = mutation_guard.engage("drill", owner="o" * 500)
    assert len(result["owner"]) == 200


def test_atomic_write_leaves_no_temp_files():
    mutation_guard.engage("drill")
    mutation_guard.release("admin")
    leftovers = list(guard_path().parent.glob(".mutation-guard-*"))
    assert leftovers == []


def test_release_needs_explicit_transition_and_preserves_audit():
    mutation_guard.engage("drill", owner="ops", expires_in_minutes=10)
    released = mutation_guard.release("admin")

    assert released["engaged"] is False
    assert released["released_by"] == "admin"
    assert released["released_at"]
    assert released["reason"] == "drill"
    assert released["owner"] == "ops"
    assert is_engaged() is False

    again = mutation_guard.release("someone-else")
    assert again["released_by"] == "admin"
    assert again["released_at"] == released["released_at"]


def test_expired_guard_stays_engaged_until_released():
    mutation_guard.engage("drill", expires_in_minutes=10)
    record = json.loads(guard_path().read_text(encoding="utf-8"))
    record["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    _write_guard_file(record)

    current = state()
    assert current["engaged"] is True
    assert current["expired"] is True
    assert is_engaged() is True
    assert mutation_guard.startup_report()["engaged"] is True

    released = mutation_guard.release("admin")
    assert released["engaged"] is False
    assert released["expired"] is False


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   \n",
        "{not-json",
        "[]",
        {"engaged": False},
        {"schema": 999, "engaged": False},
        {"schema": "1", "engaged": False},
    ],
)
def test_corrupt_or_unknown_schema_fails_closed(payload):
    _write_guard_file(payload)
    current = state()
    assert current["engaged"] is True
    assert current["source"] == "corrupt"
    assert is_engaged() is True


def test_unreadable_path_fails_closed(tmp_path):
    target = guard_path()
    target.mkdir(parents=True)
    current = state()
    assert current["engaged"] is True
    assert current["source"] == "corrupt"


def test_symlink_fails_closed(tmp_path):
    real = tmp_path / "real-guard.json"
    real.write_text(json.dumps({"schema": 1, "engaged": False}), encoding="utf-8")
    target = guard_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(real, target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted on this platform")

    current = state()
    assert current["engaged"] is True
    assert current["source"] == "corrupt"


def test_corrupt_file_can_still_be_released():
    _write_guard_file("{broken")
    assert is_engaged() is True
    released = mutation_guard.release("admin")
    assert released["engaged"] is False
    assert is_engaged() is False


def test_environment_override_engages_and_refuses_release(monkeypatch):
    monkeypatch.setenv("SMITE_DISABLE_MUTATIONS", "TRUE")
    current = state()
    assert current["engaged"] is True
    assert current["source"] == "environment"
    with pytest.raises(GuardError):
        mutation_guard.release("admin")

    monkeypatch.setenv("SMITE_DISABLE_MUTATIONS", "0")
    assert is_engaged() is False


def test_write_failure_raises_guard_error(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(settings, "mutation_guard_path", str(blocked / ".mutation-guard.json"))
    with pytest.raises(GuardError):
        mutation_guard.engage("drill")


# ------------------------------------------------------- wiring into the panel


async def test_enrich_metadata_withholds_authoritative_fields_when_engaged():
    from app.routers.nodes import enrich_node_response_metadata

    mutation_guard.engage("restore drill")
    original = {"region": "ir"}
    enriched = await enrich_node_response_metadata(None, "node-1", original)

    assert enriched == {"region": "ir"}
    assert "active_tunnel_ids" not in enriched
    assert "pending_removals" not in enriched
    assert original == {"region": "ir"}


async def test_enrich_metadata_still_populates_when_not_engaged():
    from app.routers.nodes import enrich_node_response_metadata

    class EnrichDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(rows=["tunnel-1", "tunnel-2"])
            return FakeResult(node=None)

    enriched = await enrich_node_response_metadata(EnrichDB(), "node-1", {"region": "ir"})
    assert enriched["active_tunnel_ids"] == ["tunnel-1", "tunnel-2"]
    assert "pending_removals" not in enriched


async def test_send_to_node_omits_authoritative_ids_when_engaged(monkeypatch):
    import app.node_client as node_client

    node = SimpleNamespace(
        id="node-1",
        node_metadata={"role": "foreign", "api_address": "http://node.example:8888"},
    )
    monkeypatch.setattr(node_client, "AsyncSessionLocal", lambda: FakeSession(node))
    FakeAsyncClient.payloads = []
    monkeypatch.setattr(node_client.httpx, "AsyncClient", FakeAsyncClient)

    mutation_guard.engage("restore drill")
    result = await node_client.NodeClient().send_to_node(
        "node-1", "/tunnels/apply", {"tunnel_id": "t1", "spec": {}}
    )

    assert result == {"status": "ok"}
    assert len(FakeAsyncClient.payloads) == 1
    payload = FakeAsyncClient.payloads[0]["json"]
    assert "authoritative_ids" not in payload
    assert payload["tunnel_id"] == "t1"


async def test_send_to_node_includes_authoritative_ids_when_not_engaged(monkeypatch):
    import app.node_client as node_client

    node = SimpleNamespace(
        id="node-1",
        node_metadata={"role": "foreign", "api_address": "http://node.example:8888"},
    )
    monkeypatch.setattr(node_client, "AsyncSessionLocal", lambda: FakeSession(node))
    FakeAsyncClient.payloads = []
    monkeypatch.setattr(node_client.httpx, "AsyncClient", FakeAsyncClient)

    result = await node_client.NodeClient().send_to_node(
        "node-1", "/tunnels/apply", {"tunnel_id": "t1", "spec": {}}
    )

    assert result == {"status": "ok"}
    assert FakeAsyncClient.payloads[0]["json"]["authoritative_ids"] == ["tunnel-1"]


async def test_reapply_tunnel_safe_is_a_noop_when_engaged():
    from app.tunnel_reapply_manager import tunnel_reapply_manager

    mutation_guard.engage("restore drill")
    applied = await tunnel_reapply_manager._reapply_tunnel_safe(
        SimpleNamespace(id="tunnel-1"), None, None
    )
    assert applied is False


async def test_reset_core_is_a_noop_when_engaged():
    from app.routers.core_health import _reset_core

    mutation_guard.engage("restore drill")
    assert await _reset_core("gost", SimpleNamespace(), None) is None


async def test_manual_reset_returns_409_when_engaged():
    from fastapi import HTTPException

    from app.routers.core_health import manual_reset_core

    mutation_guard.engage("restore drill")
    with pytest.raises(HTTPException) as error:
        await manual_reset_core(
            "gost", request=None, db=None, current_user=SimpleNamespace(username="admin")
        )
    assert error.value.status_code == 409


async def test_trust_material_generation_rules(monkeypatch):
    import app.node_server as node_server

    mutation_guard.engage("restore drill")
    monkeypatch.setattr(node_server, "registered_node_count", _async_return(0))
    assert await node_server.trust_material_generation_allowed(True) is False

    mutation_guard.release("admin")
    assert await node_server.trust_material_generation_allowed(False) is False
    assert await node_server.trust_material_generation_allowed(True) is True

    monkeypatch.setattr(node_server, "registered_node_count", _async_return(2))
    assert await node_server.trust_material_generation_allowed(True) is False


def _async_return(value):
    async def _inner():
        return value

    return _inner


async def test_node_server_start_does_not_mint_ca_without_permission(tmp_path):
    from app.node_server import NodeServer

    server = NodeServer()
    await server.start()

    assert not Path(settings.node_cert_path).exists()
    assert not Path(settings.node_key_path).exists()


async def test_node_server_start_mints_ca_on_fresh_install():
    from app.node_server import NodeServer

    server = NodeServer()
    server.allow_ca_generation = True
    await server.start()

    cert_path = Path(settings.node_cert_path)
    key_path = Path(settings.node_key_path)
    assert cert_path.exists() and cert_path.stat().st_size > 0
    assert key_path.exists() and key_path.stat().st_size > 0
    assert "BEGIN CERTIFICATE" in cert_path.read_text(encoding="utf-8")


async def test_ca_endpoints_refuse_to_mint_under_guard():
    from fastapi import HTTPException

    from app.routers.panel import get_ca_cert, get_server_ca_cert

    mutation_guard.engage("restore drill")
    admin = SimpleNamespace(username="admin")

    with pytest.raises(HTTPException) as ca_error:
        await get_ca_cert(download=False, current_user=admin)
    assert ca_error.value.status_code == 409

    with pytest.raises(HTTPException) as server_error:
        await get_server_ca_cert(download=False, current_user=admin)
    assert server_error.value.status_code == 409

    assert not Path(settings.node_cert_path).exists()
    assert not Path(settings.node_server_cert_path).exists()
