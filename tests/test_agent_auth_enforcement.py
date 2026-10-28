"""Tests for staged agent-API authentication enforcement and node trust rules."""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECRET_KEY", "0123456789abcdef" * 4)

repo_root = Path(__file__).resolve().parent.parent
for entry in (repo_root, repo_root / "panel"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

TOKEN_A = "A" * 43
TOKEN_B = "B" * 43
ENV_TOKEN = "E" * 43


@pytest.fixture
def auth(tmp_path, monkeypatch):
    from node.app import agent_auth

    monkeypatch.setattr(agent_auth, "TOKEN_FILE", tmp_path / "agent_token")
    monkeypatch.setattr(agent_auth, "ENFORCE_FILE", tmp_path / "agent_auth_enforce")
    monkeypatch.setattr(agent_auth, "BOOTSTRAP_FILE", tmp_path / "agent_bootstrap.json")
    monkeypatch.setattr(agent_auth, "TLS_CERT_FILE", tmp_path / "agent_tls.crt")
    monkeypatch.setattr(agent_auth, "TLS_KEY_FILE", tmp_path / "agent_tls.key")
    monkeypatch.setattr(agent_auth.settings, "node_api_token", "")
    monkeypatch.setattr(agent_auth.settings, "agent_auth_mode", "")
    monkeypatch.setattr(agent_auth.settings, "node_bootstrap_secret", "")
    monkeypatch.setattr(agent_auth.settings, "node_bootstrap_proof_ttl_seconds", 900)
    agent_auth._file_token = None
    agent_auth._loaded = False
    yield agent_auth
    agent_auth._file_token = None
    agent_auth._loaded = False


def make_request(method="POST", path="/api/agent/auth/bootstrap", host="203.0.113.10", headers=None):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        client=SimpleNamespace(host=host),
        headers=headers or {},
    )


def bootstrap(token):
    from node.app.routers.agent import TokenBootstrap

    return TokenBootstrap(token=token)


def run(coro):
    import asyncio

    return asyncio.run(coro)


# --------------------------------------------------------------- node agent


def test_first_bootstrap_requires_and_consumes_one_time_proof(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token, verify_node_token

    assert verify_node_token(make_request()) is None
    with pytest.raises(HTTPException) as missing:
        run(bootstrap_token(bootstrap(TOKEN_A), make_request()))
    assert missing.value.status_code == 403

    prepared = auth.get_or_create_bootstrap_proof()
    result = run(bootstrap_token(
        bootstrap(TOKEN_A),
        make_request(headers={"X-Bootstrap-Proof": prepared["proof"]}),
    ))
    assert result["mode"] == "panel" and result["changed"] is True
    assert auth.TOKEN_FILE.read_text(encoding="utf-8") == TOKEN_A
    assert not auth.BOOTSTRAP_FILE.exists()

    with pytest.raises(HTTPException) as error:
        verify_node_token(make_request())
    assert error.value.status_code == 401
    assert verify_node_token(make_request(headers={"X-Node-Token": TOKEN_A})) is None


def test_enforced_without_token_locks_api(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import verify_node_token

    auth.set_enforced()
    assert auth.auth_mode() == "enforce"

    with pytest.raises(HTTPException) as error:
        verify_node_token(make_request())
    assert error.value.status_code == 401

    status_loopback = make_request(method="GET", path="/api/agent/status", host="127.0.0.1")
    assert verify_node_token(status_loopback) is None

    status_remote = make_request(method="GET", path="/api/agent/status", host="203.0.113.10")
    with pytest.raises(HTTPException):
        verify_node_token(status_remote)


def test_agent_auth_mode_env_override(auth, monkeypatch):
    auth.set_enforced()
    monkeypatch.setattr(auth.settings, "agent_auth_mode", "permissive")
    assert auth.auth_mode() == "permissive"
    monkeypatch.setattr(auth.settings, "agent_auth_mode", "enforce")
    auth.clear_enforced()
    assert auth.auth_mode() == "enforce"


def test_bootstrap_requires_independent_secret(auth, monkeypatch):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token

    monkeypatch.setattr(auth.settings, "node_bootstrap_secret", "s" * 40)

    with pytest.raises(HTTPException) as missing:
        run(bootstrap_token(bootstrap(TOKEN_A), make_request()))
    assert missing.value.status_code == 403

    with pytest.raises(HTTPException) as wrong:
        run(bootstrap_token(bootstrap(TOKEN_A), make_request(headers={"X-Bootstrap-Secret": "x" * 40})))
    assert wrong.value.status_code == 403
    assert not auth.TOKEN_FILE.exists()

    result = run(bootstrap_token(bootstrap(TOKEN_A), make_request(headers={"X-Bootstrap-Secret": "s" * 40})))
    assert result["changed"] is True
    assert auth.get_file_token() == TOKEN_A

    # Replay without proving ownership of the now-configured token is rejected.
    with pytest.raises(HTTPException) as replay:
        run(bootstrap_token(bootstrap(TOKEN_B), make_request(headers={"X-Bootstrap-Secret": "s" * 40})))
    assert replay.value.status_code == 401
    assert auth.get_file_token() == TOKEN_A


def test_bootstrap_proof_is_advertised_only_while_unpaired(auth):
    prepared = auth.get_or_create_bootstrap_proof()
    assert prepared and len(prepared["proof"]) >= 32
    assert prepared["expires_at"] > int(time.time())

    auth.set_file_token(TOKEN_A)
    assert auth.get_or_create_bootstrap_proof() is None
    assert auth.TOKEN_FILE.read_text(encoding="utf-8") == TOKEN_A


def test_expired_dynamic_proof_is_rejected_and_removed(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token

    proof = "P" * 43
    auth.BOOTSTRAP_FILE.write_text(
        '{"schema":1,"proof":"' + proof + '","created_at":1,"expires_at":1}',
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as error:
        run(bootstrap_token(
            bootstrap(TOKEN_A),
            make_request(headers={"X-Bootstrap-Proof": proof}),
        ))
    assert error.value.status_code == 403
    assert not auth.BOOTSTRAP_FILE.exists()
    assert not auth.TOKEN_FILE.exists()


def test_enforced_missing_token_requires_local_unlock(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token

    auth.set_enforced()
    with pytest.raises(HTTPException) as error:
        run(bootstrap_token(bootstrap(TOKEN_A), make_request()))
    assert error.value.status_code == 409

    auth.clear_file_token()
    assert auth.is_enforced() is False
    prepared = auth.get_or_create_bootstrap_proof()
    result = run(bootstrap_token(
        bootstrap(TOKEN_A),
        make_request(headers={"X-Bootstrap-Proof": prepared["proof"]}),
    ))
    assert result["changed"] is True


def test_known_token_is_idempotent_and_rotation_needs_current_token(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token

    prepared = auth.get_or_create_bootstrap_proof()
    run(bootstrap_token(
        bootstrap(TOKEN_A),
        make_request(headers={"X-Bootstrap-Proof": prepared["proof"]}),
    ))

    same = run(bootstrap_token(bootstrap(TOKEN_A), make_request(headers={"X-Node-Token": TOKEN_A})))
    assert same["changed"] is False
    assert auth.get_file_token() == TOKEN_A

    with pytest.raises(HTTPException) as error:
        run(bootstrap_token(bootstrap(TOKEN_B), make_request(headers={"X-Node-Token": "Z" * 43})))
    assert error.value.status_code == 401
    assert auth.get_file_token() == TOKEN_A

    rotated = run(bootstrap_token(bootstrap(TOKEN_B), make_request(headers={"X-Node-Token": TOKEN_A})))
    assert rotated["changed"] is True
    assert auth.get_file_token() == TOKEN_B


def test_static_env_token_wins(auth, monkeypatch):
    from fastapi import HTTPException
    from node.app.routers.agent import bootstrap_token

    monkeypatch.setattr(auth.settings, "node_api_token", ENV_TOKEN)

    with pytest.raises(HTTPException) as error:
        run(bootstrap_token(bootstrap(TOKEN_A), make_request()))
    assert error.value.status_code == 409

    result = run(bootstrap_token(bootstrap(TOKEN_A), make_request(headers={"X-Node-Token": ENV_TOKEN})))
    assert result["mode"] == "env"
    assert not auth.TOKEN_FILE.exists()


def test_enforce_endpoint_requires_configured_token(auth):
    from fastapi import HTTPException
    from node.app.routers.agent import enforce_agent_auth

    with pytest.raises(HTTPException) as none:
        run(enforce_agent_auth(make_request(path="/api/agent/auth/enforce")))
    assert none.value.status_code == 409

    auth.set_file_token(TOKEN_A)
    with pytest.raises(HTTPException) as wrong:
        run(enforce_agent_auth(make_request(path="/api/agent/auth/enforce", headers={"X-Node-Token": TOKEN_B})))
    assert wrong.value.status_code == 401
    assert auth.is_enforced() is False

    result = run(enforce_agent_auth(make_request(path="/api/agent/auth/enforce", headers={"X-Node-Token": TOKEN_A})))
    assert result["enforced"] is True
    assert auth.ENFORCE_FILE.exists()
    assert auth.is_enforced() is True


def test_clear_file_token_unlocks_and_removes_flag(auth):
    auth.set_file_token(TOKEN_A)
    auth.set_enforced()
    auth.clear_file_token()

    assert not auth.TOKEN_FILE.exists()
    assert not auth.ENFORCE_FILE.exists()
    assert auth.get_effective_token() == ""
    assert auth.is_enforced() is False


# ------------------------------------------------------------- panel client


def test_https_without_ca_is_refused(monkeypatch, tmp_path):
    from app.config import settings
    from app.node_client import InsecureNodeTransport, NodeClient

    missing = tmp_path / "missing-ca.crt"
    monkeypatch.setattr(settings, "node_cert_path", str(missing))
    monkeypatch.chdir(tmp_path)

    client = NodeClient()
    assert client._verify_for_url("http://node.example:8888") is False
    with pytest.raises(InsecureNodeTransport):
        client._verify_for_url("https://node.example:8888")

    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setattr(settings, "node_cert_path", str(ca))
    assert client._verify_for_url("https://node.example:8888") == str(ca)


def test_direct_node_address_never_downgrades_a_pinned_node():
    from app.node_client import NodeClient

    assert NodeClient._direct_node_address(
        {"api_address": "http://203.0.113.5:8888", "agent_tls_certificate": "cert"}
    ) == "https://203.0.113.5:8888"
    assert NodeClient._direct_node_address(
        {"ip_address": "2001:db8::1", "agent_tls_certificate": "cert"}
    ) == "https://[2001:db8::1]:8888"
    assert NodeClient._direct_node_address(
        {"api_address": "http://203.0.113.5:8888"}
    ) == "http://203.0.113.5:8888"


def test_management_tls_identity_is_stable_and_pinnable(auth, monkeypatch):
    monkeypatch.setattr(auth.settings, "node_ip", "192.0.2.10")
    monkeypatch.setattr(auth.settings, "node_name", "test-node")

    auth.ensure_management_tls_identity()
    first_cert = auth.TLS_CERT_FILE.read_bytes()
    first_key = auth.TLS_KEY_FILE.read_bytes()
    metadata = auth.management_tls_metadata()

    assert metadata["certificate"].encode("ascii") == first_cert
    assert len(metadata["fingerprint"]) == 64
    assert auth.TLS_KEY_FILE.stat().st_size > 0

    auth.ensure_management_tls_identity()
    assert auth.TLS_CERT_FILE.read_bytes() == first_cert
    assert auth.TLS_KEY_FILE.read_bytes() == first_key

    from app.node_client import NodeClient

    context = NodeClient._pinned_context(metadata["certificate"])
    assert context.check_hostname is True
    assert context.verify_mode != 0


def test_incomplete_or_mismatched_tls_identity_fails_closed(auth, monkeypatch):
    monkeypatch.setattr(auth.settings, "node_ip", "192.0.2.10")
    monkeypatch.setattr(auth.settings, "node_name", "identity-node")
    auth.ensure_management_tls_identity()
    cert_a = auth.TLS_CERT_FILE.read_bytes()
    key_a = auth.TLS_KEY_FILE.read_bytes()

    auth.TLS_KEY_FILE.unlink()
    with pytest.raises(RuntimeError):
        auth.ensure_management_tls_identity()
    auth.TLS_KEY_FILE.write_bytes(key_a)

    # A certificate from one identity with the key of another must never be
    # silently replaced (that would rotate the pin without the panel knowing).
    auth.TLS_CERT_FILE.unlink()
    auth.TLS_KEY_FILE.unlink()
    auth.ensure_management_tls_identity()
    other_key = auth.TLS_KEY_FILE.read_bytes()
    auth.TLS_CERT_FILE.write_bytes(cert_a)
    auth.TLS_KEY_FILE.write_bytes(other_key)
    with pytest.raises(RuntimeError):
        auth.ensure_management_tls_identity()


class FakeHTTPResponse:
    status_code = 200

    def json(self):
        return {"status": "success"}


class FakeAsyncClient:
    calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, headers=None):
        type(self).calls.append({"url": url, "headers": headers})
        return FakeHTTPResponse()


async def test_enforce_agent_auth_posts_with_token(monkeypatch):
    import app.node_client as node_client

    FakeAsyncClient.calls = []
    monkeypatch.setattr(node_client.httpx, "AsyncClient", FakeAsyncClient)

    node = SimpleNamespace(
        id="node-1",
        node_metadata={"role": "foreign", "api_address": "http://node.example:8888"},
    )
    status, body = await node_client.NodeClient().enforce_agent_auth(node, TOKEN_A)

    assert status == 200
    assert body == {"status": "success"}
    assert FakeAsyncClient.calls == [
        {"url": "http://node.example:8888/api/agent/auth/enforce", "headers": {"X-Node-Token": TOKEN_A}}
    ]


# ------------------------------------------------------ panel provisioning


class FakeResult:
    def __init__(self, node):
        self._node = node

    def scalar_one_or_none(self):
        return self._node


class FakeSession:
    def __init__(self, node):
        self._node = node

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self._node)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class FakeNodeClient:
    bootstrap_status = 200
    bootstrap_body = {"status": "success", "mode": "panel", "changed": False}
    verify_status = True
    verify_pending_status = True
    enforce_status = 200
    bootstrap_calls = []
    enforce_calls = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def bootstrap_agent_token(self, node, token, proof):
        type(self).bootstrap_calls.append((node.id, token, proof))
        return self.bootstrap_status, dict(self.bootstrap_body)

    async def verify_agent_token(self, node, token, pending=False):
        return self.verify_pending_status if pending else self.verify_status

    async def enforce_agent_auth(self, node, token):
        type(self).enforce_calls.append((node.id, token))
        return self.enforce_status, {}


@pytest.fixture
def provisioning(monkeypatch):
    import app.node_client as node_client
    import app.node_auth_manager as manager_module

    node = SimpleNamespace(
        id="node-1",
        name="Node 1",
        node_metadata={
            "agent_token": TOKEN_A,
            "agent_pending_api_address": "https://node.example:8888",
            "agent_pending_tls_certificate": "test-certificate",
            "agent_pending_tls_fingerprint": "f" * 64,
            "agent_bootstrap_proof": "P" * 43,
            "agent_bootstrap_expires_at": int(time.time()) + 600,
            "agent_transport_mode": "pending_pinned_https",
        },
    )
    updates = []

    async def fake_update_meta(node_id, values, removals=()):
        updates.append((dict(values), tuple(removals)))
        for key, value in values.items():
            node.node_metadata[key] = value
        for key in removals:
            node.node_metadata.pop(key, None)

    FakeNodeClient.bootstrap_calls = []
    FakeNodeClient.enforce_calls = []
    FakeNodeClient.bootstrap_status = 200
    FakeNodeClient.bootstrap_body = {"status": "success", "mode": "panel", "changed": False}
    FakeNodeClient.verify_status = True
    FakeNodeClient.verify_pending_status = True
    FakeNodeClient.enforce_status = 200

    monkeypatch.setattr(node_client, "NodeClient", FakeNodeClient)
    monkeypatch.setattr(manager_module, "AsyncSessionLocal", lambda: FakeSession(node))
    monkeypatch.setattr(manager_module.node_auth_manager, "_update_meta", fake_update_meta)
    yield SimpleNamespace(
        manager=manager_module.node_auth_manager,
        node=node,
        updates=updates,
        client=FakeNodeClient,
    )


async def test_legacy_http_node_waits_without_sending_a_token(provisioning):
    provisioning.node.node_metadata = {
        "api_address": "http://node.example:8888",
        "role": "foreign",
    }

    status = await provisioning.manager.ensure_node("node-1")

    assert status == "awaiting_secure_transport"
    assert provisioning.client.bootstrap_calls == []


async def test_ensure_node_enforces_after_bootstrap(provisioning):
    status = await provisioning.manager.ensure_node("node-1")

    assert status == "ok"
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A
    assert provisioning.node.node_metadata["agent_auth_enforced"] is True
    assert provisioning.node.node_metadata.get("agent_token_pending") is None
    assert provisioning.client.enforce_calls == [("node-1", TOKEN_A)]


async def test_ensure_node_old_agent_stays_permissive(provisioning):
    provisioning.client.enforce_status = 404
    status = await provisioning.manager.ensure_node("node-1")

    assert status == "ok"
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A
    assert "agent_auth_enforced" not in provisioning.node.node_metadata


async def test_ensure_node_401_does_not_reset_or_enforce(provisioning):
    provisioning.client.bootstrap_status = 401
    status = await provisioning.manager.ensure_node("node-1")

    assert status == "conflict"
    assert provisioning.client.enforce_calls == []
    assert "agent_token_error" in provisioning.node.node_metadata
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A


async def test_stale_pinned_token_recovers_with_a_fresh_proof(provisioning):
    provisioning.node.node_metadata["agent_transport_mode"] = "pinned_https"
    provisioning.node.node_metadata["agent_tls_certificate"] = "old-certificate"
    provisioning.node.node_metadata["agent_tls_fingerprint"] = "a" * 64
    provisioning.client.verify_status = False

    status = await provisioning.manager.ensure_node("node-1")

    assert status == "ok"
    assert len(provisioning.client.bootstrap_calls) == 1
    assert provisioning.client.bootstrap_calls[0][2] == "P" * 43
    assert provisioning.node.node_metadata["agent_transport_mode"] == "pinned_https"
    assert provisioning.node.node_metadata["agent_tls_certificate"] == "test-certificate"
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A


async def test_stale_pinned_token_without_a_fresh_proof_stays_closed(provisioning):
    provisioning.node.node_metadata["agent_transport_mode"] = "pinned_https"
    for key in (
        "agent_bootstrap_proof",
        "agent_bootstrap_expires_at",
        "agent_pending_api_address",
        "agent_pending_tls_certificate",
        "agent_pending_tls_fingerprint",
    ):
        provisioning.node.node_metadata.pop(key, None)
    provisioning.client.verify_status = False

    status = await provisioning.manager.ensure_node("node-1")

    assert status == "conflict"
    assert provisioning.client.bootstrap_calls == []
    assert provisioning.client.enforce_calls == []
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A


async def test_ensure_node_env_mode(provisioning, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "node_api_token", ENV_TOKEN)
    provisioning.node.node_metadata.pop("agent_token")
    provisioning.client.bootstrap_status = 409
    status = await provisioning.manager.ensure_node("node-1")

    assert status == "env"
    assert provisioning.node.node_metadata["agent_token_mode"] == "env"
    assert provisioning.client.enforce_calls == []


async def test_enforcement_failure_is_reported_without_unlocking(provisioning):
    provisioning.client.enforce_status = 401
    status = await provisioning.manager.ensure_node("node-1")

    assert status == "ok"
    assert "agent_auth_enforced" not in provisioning.node.node_metadata
    assert "agent_auth_error" in provisioning.node.node_metadata
    # The token is still the known one; no reset/rotation was attempted.
    assert provisioning.node.node_metadata["agent_token"] == TOKEN_A
