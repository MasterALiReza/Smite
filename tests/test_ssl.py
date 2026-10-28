"""
Tests for SSL & Domain Management:
  - Domain sanitization & IP lookup utilities
  - x509 certificate parsing via cryptography
  - Private key matching & mismatch detection
  - REST endpoints for /api/ssl
"""

import pytest
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

import app.ssl_manager as sm
from app.ssl_manager import (
    sanitize_domain,
    validate_domain_name,
    validate_email_address,
    parse_certificate_pem,
    validate_cert_and_key,
    certificate_covers_domain,
    check_domain_dns,
    is_cloudflare_ip,
    MAX_CERT_SIZE,
    MAX_KEY_SIZE
)


def generate_test_cert_and_key(common_name: str = "panel.test.com", days_valid: int = 90):
    """Generate self-signed certificate and RSA key in-memory for testing"""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Smite Test Lab")
    ])
    
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]),
            critical=False
        )
        .sign(private_key, hashes.SHA256())
    )
    
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")
    
    return cert_pem, key_pem


def generate_cert_with_sans(
    sans=None,
    common_name: str = "panel.test.com",
    not_before: datetime.datetime | None = None,
    not_after: datetime.datetime | None = None,
):
    """Generate a self-signed certificate with explicit SANs and validity window"""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Smite Test Lab"),
    ])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now)
        .not_valid_after(not_after or (now + datetime.timedelta(days=90)))
    )
    if sans:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name) for name in sans]),
            critical=False,
        )
    cert = builder.sign(private_key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return cert_pem, key_pem


@pytest.fixture
def isolated_cert_paths(tmp_path, monkeypatch):
    cert_path = tmp_path / "certs" / "server.crt"
    key_path = tmp_path / "certs" / "server.key"
    monkeypatch.setattr(sm.settings, "https_cert_path", str(cert_path))
    monkeypatch.setattr(sm.settings, "https_key_path", str(key_path))
    return cert_path, key_path


def test_sanitize_domain():
    assert sanitize_domain("  PANEL.EXAMPLE.COM  ") == "panel.example.com"
    assert sanitize_domain("https://my-panel.ir/dashboard") == "my-panel.ir"
    assert sanitize_domain("http://panel.example.com:8000/") == "panel.example.com"
    assert sanitize_domain("sub.domain.co.uk.") == "sub.domain.co.uk"


def test_cloudflare_ip_detection():
    # 172.64.0.1 is in Cloudflare range
    assert is_cloudflare_ip("172.64.0.1") is True
    assert is_cloudflare_ip("104.16.1.1") is True
    # 8.8.8.8 is Google DNS, not Cloudflare
    assert is_cloudflare_ip("8.8.8.8") is False


def test_parse_valid_certificate():
    cert_pem, key_pem = generate_test_cert_and_key("panel.example.com", days_valid=60)
    info = parse_certificate_pem(cert_pem)
    
    assert info["valid"] is True
    assert info["subject"] == "panel.example.com"
    assert info["issuer"] == "panel.example.com" or "Smite" in info["issuer"]
    assert info["days_remaining"] >= 58
    assert "panel.example.com" in info["san"]
    assert info["status"] == "valid"
    assert ":" in info["fingerprint"]


def test_parse_expiring_soon_certificate():
    cert_pem, key_pem = generate_test_cert_and_key("expiring.example.com", days_valid=10)
    info = parse_certificate_pem(cert_pem)
    
    assert info["valid"] is True
    assert info["days_remaining"] <= 10
    assert info["status"] == "expiring_soon"


def test_validate_matching_key_pair():
    cert_pem, key_pem = generate_test_cert_and_key("secure.example.com")
    assert validate_cert_and_key(cert_pem, key_pem) is True


def test_validate_mismatched_key_pair():
    cert_pem, _ = generate_test_cert_and_key("site1.example.com")
    _, other_key_pem = generate_test_cert_and_key("site2.example.com")
    
    with pytest.raises(ValueError, match="does not match"):
        validate_cert_and_key(cert_pem, other_key_pem)


@pytest.mark.asyncio
async def test_dns_precheck_invalid_domain():
    res = await check_domain_dns("")
    assert res["status"] == "invalid_domain"
    assert res["matches"] is False


@pytest.mark.asyncio
async def test_dns_precheck_unresolved_domain(monkeypatch):
    import socket
    async def mock_getaddrinfo(*args, **kwargs):
        raise socket.gaierror("Name or service not known")
    
    import asyncio
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", mock_getaddrinfo)
    
    res = await check_domain_dns("non-existent-domain.xyz")
    assert res["status"] == "unresolved"
    assert res["matches"] is False
    assert "DNS resolution failed" in res["warning"]


@pytest.mark.asyncio
async def test_dns_precheck_ready_match(monkeypatch):
    from unittest.mock import AsyncMock
    import app.ssl_manager as sm
    
    monkeypatch.setattr(sm, "get_server_public_ip", AsyncMock(return_value="5.6.7.8"))
    
    async def mock_getaddrinfo(*args, **kwargs):
        return [(2, 1, 6, '', ('5.6.7.8', 80))]
    
    import asyncio
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", mock_getaddrinfo)
    
    res = await sm.check_domain_dns("panel.myserver.com")
    assert res["status"] == "ready"
    assert res["matches"] is True
    assert res["server_ip"] == "5.6.7.8"
    assert res["warning"] is None


@pytest.mark.asyncio
async def test_dns_precheck_cloudflare_proxied(monkeypatch):
    from unittest.mock import AsyncMock
    import app.ssl_manager as sm
    
    monkeypatch.setattr(sm, "get_server_public_ip", AsyncMock(return_value="5.6.7.8"))
    
    async def mock_getaddrinfo(*args, **kwargs):
        return [(2, 1, 6, '', ('172.64.80.1', 80))]
    
    import asyncio
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", mock_getaddrinfo)
    
    res = await sm.check_domain_dns("cf.myserver.com")
    assert res["status"] == "cloudflare_proxied"
    assert res["is_cloudflare"] is True
    assert "Cloudflare Proxy" in res["warning"]


def test_validate_domain_name_valid():
    assert validate_domain_name("example.com") == "example.com"
    assert validate_domain_name("sub.domain.co.uk") == "sub.domain.co.uk"
    assert validate_domain_name("my-server-01.panel.vpn.org") == "my-server-01.panel.vpn.org"
    assert validate_domain_name("https://panel.example.com:443/") == "panel.example.com"


def test_validate_domain_name_malicious_and_invalid():
    # Shell injection attempts
    with pytest.raises(ValueError, match="Invalid domain name format"):
        validate_domain_name("example.com; rm -rf /")
    with pytest.raises(ValueError, match="Invalid domain name format"):
        validate_domain_name("example.com | cat /etc/passwd")
    with pytest.raises(ValueError, match="Invalid domain name format"):
        validate_domain_name("example.com`whoami`")
    # Command flag attempts
    with pytest.raises(ValueError, match="Invalid domain name format"):
        validate_domain_name("--help")
    with pytest.raises(ValueError, match="Invalid domain name format"):
        validate_domain_name("-v")
    # Empty domain
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_domain_name("")
    # Overly long domain (>253 chars)
    long_domain = "sub." * 65 + "example.com"
    with pytest.raises(ValueError, match="exceeds maximum length"):
        validate_domain_name(long_domain)


def test_validate_email_address():
    assert validate_email_address("admin@example.com") == "admin@example.com"
    assert validate_email_address(None) is None
    assert validate_email_address("  ") is None
    
    with pytest.raises(ValueError, match="Invalid email address format"):
        validate_email_address("not-an-email")
    with pytest.raises(ValueError, match="Invalid email address format"):
        validate_email_address("admin@example.com --flag")


def test_validate_cert_and_key_payload_limits():
    oversized_cert = "A" * (MAX_CERT_SIZE + 1024)
    oversized_key = "B" * (MAX_KEY_SIZE + 1024)

    with pytest.raises(ValueError, match="Certificate payload exceeds maximum size limit"):
        validate_cert_and_key(oversized_cert, "valid_key")

    with pytest.raises(ValueError, match="Private key payload exceeds maximum size limit"):
        validate_cert_and_key("valid_cert", oversized_key)



def test_acme_token_regex_protection():
    from app.routers.ssl import _ACME_TOKEN_REGEX
    # Valid tokens (RFC 8555 base64url)
    assert _ACME_TOKEN_REGEX.match("abcXYZ123-_") is not None
    assert _ACME_TOKEN_REGEX.match("a" * 128) is not None

    # Path traversal attack patterns
    assert _ACME_TOKEN_REGEX.match("../../etc/passwd") is None
    assert _ACME_TOKEN_REGEX.match("..%2F..%2Fetc%2Fpasswd") is None
    assert _ACME_TOKEN_REGEX.match("/etc/shadow") is None
    assert _ACME_TOKEN_REGEX.match("token/with/slashes") is None
    assert _ACME_TOKEN_REGEX.match("token;reboot") is None
    assert _ACME_TOKEN_REGEX.match("") is None


@pytest.mark.asyncio
async def test_acme_challenge_root_endpoint(tmp_path):
    from pathlib import Path
    import main as panel_main
    from fastapi.responses import PlainTextResponse

    test_token = "valid_test_token_acme_12345"
    test_keyauth = "valid_test_token_acme_12345.secret_thumbprint_data"
    
    cert_challenge_dir = Path("./certs/.well-known/acme-challenge")
    cert_challenge_dir.mkdir(parents=True, exist_ok=True)
    token_file = cert_challenge_dir / test_token
    try:
        token_file.write_text(test_keyauth, encoding="utf-8")
        resp = await panel_main.acme_challenge_root(test_token)
        assert isinstance(resp, PlainTextResponse)
        assert resp.status_code == 200
        assert resp.body.decode("utf-8") == test_keyauth
    finally:
        if token_file.exists():
            token_file.unlink()


def test_certificate_covers_domain_exact_and_wildcard():
    cert_pem, _ = generate_cert_with_sans(["panel.example.com", "*.example.org"])

    assert certificate_covers_domain(cert_pem, "panel.example.com") is True
    assert certificate_covers_domain(cert_pem, "https://PANEL.EXAMPLE.COM:443/login") is True
    assert certificate_covers_domain(cert_pem, "api.example.org") is True
    # Wildcards cover exactly one label and never the apex
    assert certificate_covers_domain(cert_pem, "example.org") is False
    assert certificate_covers_domain(cert_pem, "a.b.example.org") is False
    assert certificate_covers_domain(cert_pem, "other.example.net") is False


def test_certificate_covers_domain_cn_fallback():
    cert_pem, _ = generate_cert_with_sans(None, common_name="legacy.example.com")

    assert certificate_covers_domain(cert_pem, "legacy.example.com") is True
    assert certificate_covers_domain(cert_pem, "other.example.com") is False


def test_parse_expired_and_not_yet_valid_certificate():
    now = datetime.datetime.now(datetime.timezone.utc)

    expired_pem, _ = generate_cert_with_sans(
        ["old.example.com"],
        not_before=now - datetime.timedelta(days=30),
        not_after=now - datetime.timedelta(days=1),
    )
    expired = parse_certificate_pem(expired_pem)
    assert expired["status"] == "expired"
    assert expired["expired"] is True
    assert expired["within_validity"] is False

    future_pem, _ = generate_cert_with_sans(
        ["future.example.com"],
        not_before=now + datetime.timedelta(days=5),
        not_after=now + datetime.timedelta(days=95),
    )
    future = parse_certificate_pem(future_pem)
    assert future["status"] == "not_yet_valid"
    assert future["not_yet_valid"] is True
    assert future["within_validity"] is False


def test_validate_certificate_for_domain_rejects_mismatch_and_expired():
    cert_pem, key_pem = generate_cert_with_sans(["panel.example.com"])
    with pytest.raises(ValueError, match="does not cover domain"):
        sm._validate_certificate_for_domain(cert_pem, key_pem, "other.example.com")

    now = datetime.datetime.now(datetime.timezone.utc)
    expired_pem, expired_key = generate_cert_with_sans(
        ["old.example.com"],
        not_before=now - datetime.timedelta(days=30),
        not_after=now - datetime.timedelta(days=1),
    )
    with pytest.raises(ValueError, match="expired"):
        sm._validate_certificate_for_domain(expired_pem, expired_key, "old.example.com")


def test_install_custom_ssl_rejects_domain_mismatch(isolated_cert_paths):
    cert_path, key_path = isolated_cert_paths
    cert_pem, key_pem = generate_cert_with_sans(["panel.example.com"])

    with pytest.raises(ValueError, match="does not cover domain"):
        sm.install_custom_ssl("other.example.com", cert_pem, key_pem)

    assert not cert_path.exists()
    assert not key_path.exists()


def test_install_custom_ssl_restart_required_when_nginx_untestable(isolated_cert_paths, monkeypatch):
    cert_path, key_path = isolated_cert_paths
    cert_pem, key_pem = generate_cert_with_sans(["panel.example.com"], common_name="panel.example.com")
    monkeypatch.setattr(sm, "_nginx_config_test", lambda: (None, "no tooling"))
    monkeypatch.setattr(sm, "_active_nginx_serves_certificate", lambda path: None)

    result = sm.install_custom_ssl("panel.example.com", cert_pem, key_pem)

    assert result["activation"] == "restart_required"
    assert "restart" in result["activation_message"].lower()
    installed = parse_certificate_pem(cert_path.read_text(encoding="utf-8"))
    assert installed["subject"] == "panel.example.com"
    assert "PRIVATE KEY" in key_path.read_text(encoding="utf-8")


def test_install_custom_ssl_rolls_back_when_nginx_rejects(isolated_cert_paths, monkeypatch):
    cert_path, key_path = isolated_cert_paths
    old_cert, old_key = generate_test_cert_and_key("old.example.com")
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(old_cert, encoding="utf-8")
    key_path.write_text(old_key, encoding="utf-8")

    new_cert, new_key = generate_cert_with_sans(["new.example.com"])
    monkeypatch.setattr(sm, "_nginx_config_test", lambda: (False, "invalid config"))
    monkeypatch.setattr(sm, "_active_nginx_serves_certificate", lambda path: True)

    with pytest.raises(RuntimeError, match="previous certificate pair was restored"):
        sm.install_custom_ssl("new.example.com", new_cert, new_key)

    assert cert_path.read_text(encoding="utf-8") == old_cert
    assert key_path.read_text(encoding="utf-8") == old_key


def test_install_certificate_pair_restores_previous_files_on_write_failure(isolated_cert_paths, monkeypatch):
    cert_path, key_path = isolated_cert_paths
    old_cert, old_key = generate_test_cert_and_key("old.example.com")
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(old_cert, encoding="utf-8")
    key_path.write_text(old_key, encoding="utf-8")

    new_cert, new_key = generate_cert_with_sans(["new.example.com"])
    original_write = sm._atomic_write_bytes
    key_attempts = {"count": 0}

    def flaky_write(path, data, mode=None):
        if path.name == "server.key":
            key_attempts["count"] += 1
            if key_attempts["count"] == 1:
                raise OSError("disk full")
        return original_write(path, data, mode)

    monkeypatch.setattr(sm, "_atomic_write_bytes", flaky_write)

    with pytest.raises(RuntimeError, match="previous pair restored"):
        sm._install_certificate_pair(new_cert, new_key)

    assert cert_path.read_text(encoding="utf-8") == old_cert
    assert key_path.read_text(encoding="utf-8") == old_key


@pytest.mark.asyncio
async def test_remove_ssl_cert_removes_files_and_reports_restart(isolated_cert_paths, monkeypatch):
    cert_path, key_path = isolated_cert_paths
    cert_pem, key_pem = generate_test_cert_and_key("panel.example.com")
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_text(cert_pem, encoding="utf-8")
    key_path.write_text(key_pem, encoding="utf-8")

    async def fake_save_ssl_db_settings(**kwargs):
        return False

    monkeypatch.setattr(sm, "save_ssl_db_settings", fake_save_ssl_db_settings)

    result = await sm.remove_ssl_cert()

    assert result["status"] == "success"
    assert result["activation"] == "restart_required"
    assert "restart" in result["message"].lower()
    assert ".env" in result["message"]
    assert not cert_path.exists()
    assert not key_path.exists()



