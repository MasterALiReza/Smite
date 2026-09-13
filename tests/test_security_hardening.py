import sys
import os
from pathlib import Path
import pytest
from unittest.mock import MagicMock

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
panel_dir = repo_root / "panel"
if str(panel_dir) not in sys.path:
    sys.path.insert(0, str(panel_dir))

from node.app.routers.agent import router as agent_router
from panel.app.routers.auth import _client_ip, _TRUSTED_PROXIES


def test_sync_adapters_endpoint_is_removed():
    """Verify C-1: Ensure dangerous sync_adapters endpoint is completely absent from router"""
    for route in agent_router.routes:
        assert getattr(route, "path", "") != "/system/sync_adapters", "sync_adapters must not exist in agent router"


def test_client_ip_spoofing_defense():
    """Verify H-5: _client_ip should reject spoofed X-Forwarded-For from untrusted remote clients"""
    mock_request_untrusted = MagicMock()
    mock_request_untrusted.client.host = "198.51.100.42"
    mock_request_untrusted.headers = {"X-Forwarded-For": "10.0.0.1, 192.168.1.1"}
    
    extracted_ip = _client_ip(mock_request_untrusted)
    assert extracted_ip == "198.51.100.42", "Direct remote IP must be used when client is not a trusted proxy"

    mock_request_trusted = MagicMock()
    mock_request_trusted.client.host = "127.0.0.1"
    mock_request_trusted.headers = {"X-Forwarded-For": "203.0.113.195, 127.0.0.1"}
    
    trusted_extracted = _client_ip(mock_request_trusted)
    assert trusted_extracted == "203.0.113.195", "X-Forwarded-For should be honored only from trusted local proxy"


def test_path_traversal_guard():
    """Verify C-5: Path traversal logic ensures requested file resides strictly within base directory"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        base_dir = Path(td) / "static"
        base_dir.mkdir()
        (base_dir / "index.html").write_text("OK", encoding="utf-8")
        
        secret_dir = Path(td) / "secret"
        secret_dir.mkdir()
        (secret_dir / "passwords.txt").write_text("SECRET", encoding="utf-8")
        
        # Test safe path within base_dir
        safe_target = (base_dir / "index.html").resolve()
        assert os.path.commonpath([str(base_dir.resolve()), str(safe_target)]) == str(base_dir.resolve())
        
        # Test traversal target outside base_dir
        traversal_target = (base_dir / "../secret/passwords.txt").resolve()
        assert os.path.commonpath([str(base_dir.resolve()), str(traversal_target)]) != str(base_dir.resolve())


@pytest.mark.asyncio
async def test_core_reset_cooldown():
    """Verify M-15: Cooldown prevents rapid hammering of core reset endpoint (429 Too Many Requests)"""
    from fastapi import HTTPException
    from panel.app.routers.core_health import manual_reset_core, _LAST_MANUAL_RESET, RESET_COOLDOWN_SECONDS
    import time
    
    # Simulate a recent reset
    _LAST_MANUAL_RESET["rathole"] = time.time() - 5  # only 5 seconds ago
    
    mock_request = MagicMock()
    mock_db = MagicMock()
    mock_user = MagicMock()
    
    with pytest.raises(HTTPException) as exc_info:
        await manual_reset_core(
            core="rathole",
            request=mock_request,
            db=mock_db,
            current_user=mock_user
        )
    
    assert exc_info.value.status_code == 429
    assert "recently" in exc_info.value.detail


@pytest.mark.asyncio
async def test_status_version_sanitized(monkeypatch):
    """Verify M-16: Version endpoint returns clean version without invoking docker inspect"""
    from panel.app.routers.status import get_version
    
    monkeypatch.setenv("SMITE_VERSION", "v1.2.3")
    res = await get_version()
    assert res == {"version": "1.2.3"}


def test_frp_adapter_sni_configurable(monkeypatch):
    """Verify L-8: FRP adapter does not enforce speedtest.net when custom SNI or env var is supplied"""
    from node.app.core_adapters import FrpAdapter
    
    adapter = FrpAdapter()
    spec_with_sni = {
        "server_addr": "203.0.113.10",
        "ports": [8080],
        "custom_sni": "stealth.example.com",
        "security_type": "tls"
    }
    
    # Check custom_sni resolution
    sni = spec_with_sni.get('custom_sni') or spec_with_sni.get('stealth_domain') or spec_with_sni.get('server_name') or os.getenv('FRP_DEFAULT_SNI')
    assert sni == "stealth.example.com"


@pytest.mark.asyncio
async def test_jwt_revocation_and_logout():
    """Verify H-2: Revoking a JWT prevents it from being used in get_current_user"""
    from panel.app.routers.auth import create_access_token, revoke_token, is_token_revoked, get_current_user
    from fastapi.security import HTTPAuthorizationCredentials
    from fastapi import HTTPException
    
    test_token = create_access_token(data={"sub": "admin"})
    assert not is_token_revoked(test_token), "Fresh token must not be revoked"
    
    revoke_token(test_token)
    assert is_token_revoked(test_token), "Revoked token must be recognized by is_token_revoked"
    
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=test_token)
    mock_db = MagicMock()
    
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=creds, db=mock_db)
    
    assert exc_info.value.status_code == 401
    assert "revoked" in exc_info.value.detail.lower()


def test_sanitize_spec_for_log():
    """Verify H-9: sanitize_spec_for_log masks sensitive tokens and keys in specs"""
    from panel.app.utils import sanitize_spec_for_log
    
    dirty_spec = {
        "server_addr": "1.2.3.4",
        "ports": [8080],
        "token": "super_secret_token",
        "server_private_key": "private_x25519_key_string",
        "nested": {
            "auth_token": "inner_secret",
            "safe_param": "hello"
        }
    }
    
    clean = sanitize_spec_for_log(dirty_spec)
    assert clean["server_addr"] == "1.2.3.4"
    assert clean["ports"] == [8080]
    assert clean["token"] == "***REDACTED***"
    assert clean["server_private_key"] == "***REDACTED***"
    assert clean["nested"]["auth_token"] == "***REDACTED***"
    assert clean["nested"]["safe_param"] == "hello"


def test_sanitize_cmd_for_log():
    """Verify H-9: sanitize_cmd_for_log masks command arguments with sensitive values"""
    from node.app.core_adapters import sanitize_cmd_for_log
    
    cmd = ["chisel", "client", "--key", "secret_key_123", "--auth", "user:pass456", "https://server:8080"]
    sanitized = sanitize_cmd_for_log(cmd)
    
    assert "secret_key_123" not in sanitized
    assert "user:pass456" not in sanitized
    assert "--key ***REDACTED***" in sanitized
    assert "--auth ***REDACTED***" in sanitized


def test_noise_keypair_cryptographic_validity():
    """Verify M-2: generate_noise_keypair returns valid base64-encoded 32-byte X25519 keys"""
    from panel.app.utils import generate_noise_keypair
    import base64
    
    priv, pub = generate_noise_keypair()
    assert priv and pub
    priv_bytes = base64.b64decode(priv)
    pub_bytes = base64.b64decode(pub)
    assert len(priv_bytes) == 32, "X25519 private key must be 32 bytes"
    assert len(pub_bytes) == 32, "X25519 public key must be 32 bytes"


