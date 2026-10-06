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

from app.ssl_manager import (
    sanitize_domain,
    validate_domain_name,
    validate_email_address,
    parse_certificate_pem,
    validate_cert_and_key,
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



