"""
SSL & Domain Manager Service for Smite Panel
Provides:
  - Server public IP discovery with multi-provider fallback
  - Pre-flight DNS & IP check (anti-lockout and rate-limit shield)
  - Cloudflare proxy detection
  - Native x509 certificate parsing via cryptography
  - Private key & certificate match validation
  - Let's Encrypt automated issuance & renewal (webroot & standalone)
  - Custom certificate (Cloudflare Origin CA / wildcard) installation
  - Safe rollback and uninstallation
  - Background auto-renewal worker with Telegram notifications
"""

import os
import re
import sys
import shutil
import socket
import hashlib
import logging
import asyncio
import ipaddress
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone

import httpx
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, ec, dsa, ed25519

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Settings
from app.config import settings

logger = logging.getLogger(__name__)

# Strict RFC 1035 / 1123 domain regex: letters, numbers, hyphens, dots. Disallows leading/trailing hyphens or flags.
DOMAIN_REGEX = re.compile(
    r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$'
)
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

# Memory & payload limits to prevent denial-of-service
MAX_CERT_SIZE = 256 * 1024  # 256 KB
MAX_KEY_SIZE = 64 * 1024    # 64 KB

# Known Cloudflare IPv4 networks for proxy detection
CLOUDFLARE_IPV4_CIDRS = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
]
_CF_NETWORKS = [ipaddress.ip_network(cidr) for cidr in CLOUDFLARE_IPV4_CIDRS]


def _get_base_dir() -> Path:
    """Get project base directory"""
    return Path(__file__).resolve().parent.parent.parent


def get_cert_paths() -> tuple[Path, Path]:
    """Return resolved paths to server.crt and server.key"""
    cert_p = Path(settings.https_cert_path)
    if not cert_p.is_absolute():
        cert_p = Path(os.getcwd()) / cert_p
    key_p = Path(settings.https_key_path)
    if not key_p.is_absolute():
        key_p = Path(os.getcwd()) / key_p
    return cert_p, key_p


async def get_server_public_ip() -> Optional[str]:
    """Detect server public IPv4 using reliable external lookups with fallback"""
    lookup_urls = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    async with httpx.AsyncClient(timeout=3.5) as client:
        for url in lookup_urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    candidate = resp.text.strip()
                    # Validate that it is a valid IPv4
                    ipaddress.IPv4Address(candidate)
                    return candidate
            except Exception as e:
                logger.debug(f"IP lookup failed for {url}: {e}")
                continue
    return None


def is_cloudflare_ip(ip_str: str) -> bool:
    """Check if an IP belongs to Cloudflare CDN ranges"""
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in _CF_NETWORKS)
    except Exception:
        return False


def sanitize_domain(domain: str) -> str:
    """Clean domain string of protocols, paths, ports, and whitespace"""
    d = domain.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    if "/" in d:
        d = d.split("/", 1)[0]
    if ":" in d:
        d = d.split(":", 1)[0]
    return d.rstrip(".")


def validate_domain_name(domain: str) -> str:
    """
    Validate and return sanitized FQDN domain name strictly conforming to RFC 1035 / 1123.
    Rejects spaces, special characters, malicious flags, and invalid formats.
    """
    clean = sanitize_domain(domain)
    if not clean:
        raise ValueError("Domain name cannot be empty.")
    if len(clean) > 253:
        raise ValueError(f"Domain name exceeds maximum length of 253 characters ({len(clean)} chars).")
    if not DOMAIN_REGEX.match(clean):
        raise ValueError(f"Invalid domain name format: '{domain}'. Must be a valid hostname (e.g. panel.example.com).")
    return clean


def validate_email_address(email: Optional[str]) -> Optional[str]:
    """
    Validate admin email format strictly against RFC patterns.
    Returns cleaned email or None.
    """
    if not email:
        return None
    clean = email.strip()
    if not clean:
        return None
    if len(clean) > 254:
        raise ValueError("Email address exceeds maximum length of 254 characters.")
    if not EMAIL_REGEX.match(clean):
        raise ValueError(f"Invalid email address format: '{email}'.")
    return clean


async def check_domain_dns(domain: str) -> Dict[str, Any]:
    """
    Pre-flight DNS check:
      1. Resolves candidate domain via DNS
      2. Detects server public IP
      3. Verifies IP match
      4. Detects Cloudflare proxy status
    """
    clean_domain = sanitize_domain(domain)
    if not clean_domain or not DOMAIN_REGEX.match(clean_domain) or len(clean_domain) > 253:
        return {
            "domain": clean_domain or domain,
            "status": "invalid_domain",
            "server_ip": None,
            "resolved_ips": [],
            "matches": False,
            "is_cloudflare": False,
            "warning": "Domain name is invalid. Must be a valid hostname (e.g. panel.example.com).",
            "message": "Please enter a valid domain name without special characters or spaces."
        }

    loop = asyncio.get_running_loop()
    resolved_ips: List[str] = []

    try:
        addr_info = await loop.getaddrinfo(clean_domain, 80, family=socket.AF_INET)
        for item in addr_info:
            ip = item[4][0]
            if ip not in resolved_ips:
                resolved_ips.append(ip)
    except socket.gaierror as e:
        logger.warning(f"DNS lookup failed for {clean_domain}: {e}")
        return {
            "domain": clean_domain,
            "status": "unresolved",
            "server_ip": await get_server_public_ip(),
            "resolved_ips": [],
            "matches": False,
            "is_cloudflare": False,
            "warning": "DNS resolution failed. Domain does not exist or DNS records have not propagated yet.",
            "message": f"Could not resolve '{clean_domain}'. Ensure you created an A record pointing to your server IP."
        }
    except Exception as e:
        logger.error(f"Unexpected error resolving DNS for {clean_domain}: {e}")
        return {
            "domain": clean_domain,
            "status": "error",
            "server_ip": None,
            "resolved_ips": [],
            "matches": False,
            "is_cloudflare": False,
            "warning": str(e),
            "message": "Error occurred during DNS resolution."
        }

    server_ip = await get_server_public_ip()
    matches = bool(server_ip and server_ip in resolved_ips)
    has_cf = any(is_cloudflare_ip(ip) for ip in resolved_ips)

    if not resolved_ips:
        status = "unresolved"
        warning = "DNS resolution failed. Domain does not exist or DNS records have not propagated yet."
        message = f"Could not resolve '{clean_domain}'. Ensure you created an A record pointing to your server IP."
    elif has_cf:
        status = "cloudflare_proxied"
        warning = "Cloudflare Proxy (Orange Cloud) detected. Let's Encrypt HTTP-01 challenge might be challenged by Cloudflare. Use 'Custom SSL' with a Cloudflare Origin Certificate, or temporarily set DNS to 'DNS-Only' (Gray Cloud)."
        message = "Domain is routed through Cloudflare Proxy."
    elif matches:
        status = "ready"
        warning = None
        message = f"DNS correctly points to this server ({server_ip}). Ready for SSL issuance!"
    else:
        status = "mismatch"
        resolved_str = ", ".join(resolved_ips) if resolved_ips else "none"
        warning = f"Domain resolves to [{resolved_str}], but this server's public IP is [{server_ip or 'unknown'}]."
        message = "DNS A-record does not match server public IP. Update your DNS records before proceeding."

    return {
        "domain": clean_domain,
        "status": status,
        "server_ip": server_ip,
        "resolved_ips": resolved_ips,
        "matches": matches,
        "is_cloudflare": has_cf,
        "warning": warning,
        "message": message
    }


def parse_certificate_pem(cert_pem: str) -> Dict[str, Any]:
    """Parse x509 PEM certificate and return metadata and expiration days"""
    try:
        cert_bytes = cert_pem.strip().encode("utf-8")
        cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
        
        # Subject CN
        subject_cns = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        subject_name = subject_cns[0].value if subject_cns else ""
        
        # Issuer CN & Org
        issuer_cns = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        issuer_orgs = cert.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
        issuer_str = issuer_cns[0].value if issuer_cns else (issuer_orgs[0].value if issuer_orgs else "Unknown")
        
        # Dates (support newer cryptography without naive/aware deprecation)
        try:
            not_before = cert.not_valid_before_utc.replace(tzinfo=None)
            not_after = cert.not_valid_after_utc.replace(tzinfo=None)
        except AttributeError:
            not_before = cert.not_valid_before
            not_after = cert.not_valid_after
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        days_remaining = (not_after - now).days
        not_yet_valid = now < not_before
        expired = now > not_after
        
        # SANs
        sans: List[str] = []
        try:
            san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            sans = [str(name.value) for name in san_ext.value]  # type: ignore
        except Exception:
            pass
        
        # Fingerprint
        fp = cert.fingerprint(hashes.SHA256()).hex().upper()
        formatted_fp = ":".join(fp[i:i+2] for i in range(0, len(fp), 2))
        
        if not_yet_valid:
            status = "not_yet_valid"
        elif expired:
            status = "expired"
        elif days_remaining <= 15:
            status = "expiring_soon"
        else:
            status = "valid"
            
        return {
            "valid": True,
            "subject": subject_name,
            "issuer": issuer_str,
            "not_valid_before": not_before.isoformat() + "Z",
            "not_valid_after": not_after.isoformat() + "Z",
            "days_remaining": max(0, days_remaining),
            "not_yet_valid": not_yet_valid,
            "expired": expired,
            "within_validity": not (not_yet_valid or expired),
            "san": sans,
            "fingerprint": formatted_fp,
            "status": status
        }
    except Exception as e:
        logger.error(f"Failed to parse x509 certificate: {e}")
        return {
            "valid": False,
            "error": str(e),
            "status": "invalid"
        }


def validate_cert_and_key(cert_pem: str, key_pem: str) -> bool:
    """Validate that the private key matches the public key in the certificate and size limits"""
    cert_bytes = cert_pem.strip().encode("utf-8")
    key_bytes = key_pem.strip().encode("utf-8")

    if len(cert_bytes) > MAX_CERT_SIZE:
        raise ValueError(f"Certificate payload exceeds maximum size limit of {MAX_CERT_SIZE // 1024} KB.")
    if len(key_bytes) > MAX_KEY_SIZE:
        raise ValueError(f"Private key payload exceeds maximum size limit of {MAX_KEY_SIZE // 1024} KB.")

    try:
        cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
        key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
        
        cert_pub = cert.public_key()
        key_pub = key.public_key()
        
        cert_pub_bytes = cert_pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        key_pub_bytes = key_pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        if cert_pub_bytes != key_pub_bytes:
            raise ValueError("The provided private key does not match the certificate.")
        return True
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Certificate and key validation failed: {e}")
        raise ValueError(f"SSL key pair validation failed: {e}")


def _certificate_dns_names(cert: x509.Certificate) -> List[str]:
    """Extract DNS SANs (falling back to subject CN only when no SANs exist)"""
    names: List[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_oid(x509.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        names.extend(
            str(name.value).lower().rstrip(".")
            for name in san_ext.value
            if isinstance(name, x509.DNSName)
        )
    except x509.ExtensionNotFound:
        pass
    if not names:
        names.extend(
            attr.value.lower().rstrip(".")
            for attr in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
            if attr.value
        )
    return names


def certificate_covers_domain(cert_pem: str, domain: str) -> bool:
    """Return True when the certificate SANs (or CN fallback) cover the domain, including wildcards"""
    clean = sanitize_domain(domain)
    if not clean:
        return False
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.strip().encode("utf-8"), default_backend())
    except Exception as e:
        logger.warning(f"Could not inspect certificate SANs: {e}")
        return False

    for name in _certificate_dns_names(cert):
        if name == clean:
            return True
        if name.startswith("*.") and clean.count(".") == name.count("."):
            if clean.endswith(name[1:]):
                return True
    return False


def _validate_certificate_for_domain(cert_pem: str, key_pem: str, domain: str) -> Dict[str, Any]:
    """Validate key pair, validity window and domain coverage before installation"""
    validate_cert_and_key(cert_pem, key_pem)
    parsed = parse_certificate_pem(cert_pem)
    if not parsed.get("valid"):
        raise ValueError(f"Invalid certificate: {parsed.get('error')}")
    if parsed.get("not_yet_valid"):
        raise ValueError(f"Certificate is not valid yet (valid from {parsed.get('not_valid_before')}).")
    if parsed.get("expired"):
        raise ValueError(f"Certificate expired on {parsed.get('not_valid_after')}.")
    if not certificate_covers_domain(cert_pem, domain):
        sans = ", ".join(parsed.get("san") or []) or "none"
        raise ValueError(
            f"Certificate does not cover domain '{domain}'. Certificate DNS names: {sans}."
        )
    return parsed


def get_active_certificate_info() -> Dict[str, Any]:
    """Inspect active server certificate file on disk"""
    cert_path, _ = get_cert_paths()
    if not cert_path.exists() or cert_path.stat().st_size == 0:
        return {"installed": False, "status": "not_installed"}
    try:
        content = cert_path.read_text(encoding="utf-8")
        parsed = parse_certificate_pem(content)
        parsed["installed"] = parsed.get("valid", False)
        return parsed
    except Exception as e:
        logger.warning(f"Could not read cert file {cert_path}: {e}")
        return {"installed": False, "status": "read_error", "error": str(e)}


def reload_nginx_service():
    """Attempt to reload Nginx in Docker or host system without dropping connections"""
    # 1. Try docker container smite-nginx
    if shutil.which("docker"):
        try:
            res = subprocess.run(
                ["docker", "exec", "smite-nginx", "nginx", "-s", "reload"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False
            )
            if res.returncode == 0:
                logger.info("Successfully reloaded smite-nginx container")
                return True
        except Exception as e:
            logger.debug(f"Docker nginx reload attempted: {e}")

    # 2. Try host nginx
    if shutil.which("nginx"):
        try:
            res = subprocess.run(["nginx", "-s", "reload"], capture_output=True, text=True, timeout=5, check=False)
            if res.returncode == 0:
                logger.info("Successfully reloaded host nginx")
                return True
        except Exception as e:
            logger.debug(f"Host nginx reload attempted: {e}")

    return False


def _update_env_ssl(enabled: bool, domain: str) -> bool:
    """Best-effort .env update; returns False for read-only mounts so callers can report a restart requirement"""
    candidate_paths = [
        Path("/app/config/.env"),
        Path(".env"),
        Path("/opt/smite/.env"),
        _get_base_dir() / ".env"
    ]
    for env_path in candidate_paths:
        if not env_path.exists():
            continue
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            updated_lines = []
            found_https = False
            found_domain = False
            found_ssl_domain = False

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("HTTPS_ENABLED="):
                    updated_lines.append(f"HTTPS_ENABLED={'true' if enabled else 'false'}")
                    found_https = True
                elif stripped.startswith("PANEL_DOMAIN="):
                    updated_lines.append(f"PANEL_DOMAIN={domain}")
                    found_domain = True
                elif stripped.startswith("SMITE_SSL_DOMAIN="):
                    updated_lines.append(f"SMITE_SSL_DOMAIN={domain}")
                    found_ssl_domain = True
                else:
                    updated_lines.append(line)

            if not found_https:
                updated_lines.append(f"HTTPS_ENABLED={'true' if enabled else 'false'}")
            if not found_domain and domain:
                updated_lines.append(f"PANEL_DOMAIN={domain}")
            if not found_ssl_domain and domain:
                updated_lines.append(f"SMITE_SSL_DOMAIN={domain}")

            env_path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
            logger.info(f"Updated SSL settings in {env_path}")
            return True
        except Exception as e:
            logger.warning(
                f"Could not update {env_path} (read-only mount or permissions): {e}. "
                "Update HTTPS_ENABLED/SMITE_SSL_DOMAIN manually and restart the stack."
            )
            return False
    logger.warning("No writable .env file found; SSL environment changes could not be persisted.")
    return False


async def save_ssl_db_settings(
    enabled: bool,
    domain: str,
    email: Optional[str] = None,
    mode: str = "letsencrypt",
    auto_renew: bool = True
):
    """Persist SSL configuration to database Settings table"""
    cert_info = get_active_certificate_info()
    data = {
        "enabled": enabled,
        "domain": domain,
        "email": email or "",
        "mode": mode,
        "auto_renew": auto_renew,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "cert_info": cert_info
    }
    
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Settings).where(Settings.key == "ssl"))
        setting = res.scalar_one_or_none()
        if setting:
            setting.value = data
            setting.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            setting = Settings(key="ssl", value=data)
            db.add(setting)
        await db.commit()
    
    settings.https_enabled = enabled
    settings.panel_domain = domain
    return _update_env_ssl(enabled, domain)


async def get_ssl_db_settings() -> Dict[str, Any]:
    """Retrieve SSL configuration from database Settings table"""
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Settings).where(Settings.key == "ssl"))
        setting = res.scalar_one_or_none()
        if setting and setting.value:
            return setting.value
    return {
        "enabled": settings.https_enabled,
        "domain": settings.panel_domain or "",
        "email": "",
        "mode": "letsencrypt",
        "auto_renew": True,
        "cert_info": get_active_certificate_info()
    }


def archive_existing_certs():
    """Archive current server.crt and server.key into certs/archive/"""
    cert_path, key_path = get_cert_paths()
    if cert_path.exists() or key_path.exists():
        archive_dir = cert_path.parent / "archive" / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            if cert_path.exists():
                shutil.copy2(cert_path, archive_dir / "server.crt")
            if key_path.exists():
                shutil.copy2(key_path, archive_dir / "server.key")
            logger.info(f"Archived previous certificates to {archive_dir}")
        except Exception as e:
            logger.warning(f"Could not archive previous certificates: {e}")


def _fsync_directory(path: Path):
    """Best-effort directory fsync so renames survive a crash (unsupported on Windows)"""
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        dir_fd = os.open(str(path), flags)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _atomic_write_bytes(path: Path, data: bytes, mode: Optional[int] = None):
    """Write to a sibling temp file, fsync, then atomically replace the target"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _read_certificate_pair() -> tuple[Optional[bytes], Optional[bytes]]:
    cert_path, key_path = get_cert_paths()
    cert_bytes = cert_path.read_bytes() if cert_path.exists() else None
    key_bytes = key_path.read_bytes() if key_path.exists() else None
    return cert_bytes, key_bytes


def _restore_certificate_pair(cert_bytes: Optional[bytes], key_bytes: Optional[bytes]):
    """Restore a previously captured certificate pair (removing files that did not exist)"""
    cert_path, key_path = get_cert_paths()
    try:
        if cert_bytes is None:
            if cert_path.exists():
                cert_path.unlink()
        else:
            _atomic_write_bytes(cert_path, cert_bytes, 0o644)
        if key_bytes is None:
            if key_path.exists():
                key_path.unlink()
        else:
            _atomic_write_bytes(key_path, key_bytes, 0o600)
        logger.warning("Restored the previous certificate pair after a failed update")
    except Exception as e:
        logger.error(f"Failed to restore previous certificate pair: {e}", exc_info=True)


def _install_certificate_pair(cert_pem: str, key_pem: str) -> tuple[Path, Path, Optional[bytes], Optional[bytes]]:
    """Atomically install cert/key with rollback to the previous pair on any write failure"""
    cert_path, key_path = get_cert_paths()
    cert_bytes = (cert_pem.strip() + "\n").encode("utf-8")
    key_bytes = (key_pem.strip() + "\n").encode("utf-8")
    old_cert, old_key = _read_certificate_pair()
    archive_existing_certs()
    try:
        _atomic_write_bytes(cert_path, cert_bytes, 0o644)
        _atomic_write_bytes(key_path, key_bytes, 0o600)
    except Exception as e:
        _restore_certificate_pair(old_cert, old_key)
        raise RuntimeError(f"Failed to write certificate files; previous pair restored: {e}")
    return cert_path, key_path, old_cert, old_key


def _nginx_config_test() -> tuple[Optional[bool], str]:
    """Run nginx -t via docker or host, returning (result, output) with None when untestable"""
    attempts = []
    if shutil.which("docker"):
        attempts.append(["docker", "exec", "smite-nginx", "nginx", "-t"])
    if shutil.which("nginx"):
        attempts.append(["nginx", "-t"])
    for cmd in attempts:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        except Exception as e:
            logger.debug(f"Nginx config test could not run ({' '.join(cmd)}): {e}")
            continue
        output = (res.stderr or res.stdout or "").strip()
        if res.returncode == 0:
            return True, output
        logger.warning(f"Nginx config test failed ({' '.join(cmd)}): {output}")
        return False, output
    return None, "Nginx tooling is not reachable from the panel runtime."


def test_nginx_config() -> Optional[bool]:
    """True when nginx accepts its config, False when it rejects it, None when untestable"""
    return _nginx_config_test()[0]


def _nginx_certificate_paths() -> Optional[List[str]]:
    """Return ssl_certificate paths from the running nginx config, or None when unavailable"""
    attempts = []
    if shutil.which("docker"):
        attempts.append(["docker", "exec", "smite-nginx", "nginx", "-T"])
    if shutil.which("nginx"):
        attempts.append(["nginx", "-T"])
    for cmd in attempts:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        except Exception:
            continue
        if res.returncode != 0:
            continue
        paths: List[str] = []
        for line in res.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("ssl_certificate "):
                value = stripped[len("ssl_certificate "):].strip().rstrip(";").strip()
                if value and value not in paths:
                    paths.append(value)
        return paths
    return None


def _active_nginx_serves_certificate(cert_path: Path) -> Optional[bool]:
    """True when a configured nginx certificate matches the installed file content,
    False when nginx uses different certificates, None when it cannot be verified"""
    paths = _nginx_certificate_paths()
    if paths is None:
        return None
    try:
        target_hash = hashlib.sha256(cert_path.read_bytes()).hexdigest()
    except OSError:
        return False
    for active in paths:
        candidate = Path(active)
        try:
            if candidate.exists() and hashlib.sha256(candidate.read_bytes()).hexdigest() == target_hash:
                return True
        except OSError:
            pass
        if shutil.which("docker") and active.startswith("/"):
            try:
                res = subprocess.run(
                    ["docker", "exec", "smite-nginx", "sha256sum", active],
                    capture_output=True, text=True, timeout=8, check=False
                )
                if res.returncode == 0 and res.stdout.split() and res.stdout.split()[0].lower() == target_hash:
                    return True
            except Exception:
                continue
    return False


def _activate_certificate_change(
    cert_path: Path,
    cert_backup: Optional[bytes],
    key_backup: Optional[bytes]
) -> Dict[str, str]:
    """Verify nginx accepts the new pair and report whether a reload or a full restart is required"""
    test_ok, detail = _nginx_config_test()
    if test_ok is False:
        if _active_nginx_serves_certificate(cert_path) is not False:
            _restore_certificate_pair(cert_backup, key_backup)
            raise RuntimeError(
                "Nginx rejected its configuration after the certificate update; "
                f"the previous certificate pair was restored. Nginx output: {detail or 'configuration test failed'}"
            )
        return {
            "activation": "restart_required",
            "activation_message": "Certificate files were updated, but nginx reported a configuration problem unrelated to this certificate path. Restart the panel/nginx stack and inspect 'nginx -t'."
        }

    serves = _active_nginx_serves_certificate(cert_path)
    if serves is True and reload_nginx_service():
        return {
            "activation": "reloaded",
            "activation_message": "Certificate files updated and nginx reloaded successfully."
        }
    if serves is False:
        return {
            "activation": "restart_required",
            "activation_message": "Certificate files updated, but nginx is serving a different certificate path. A full panel/nginx restart is required to activate this certificate (for custom certificates, ensure nginx is configured to use the custom pair)."
        }
    return {
        "activation": "restart_required",
        "activation_message": "Certificate files updated. Nginx reload could not be verified from the panel runtime; restart the panel/nginx stack to activate the new certificate."
    }


def install_custom_ssl(domain: str, cert_pem: str, key_pem: str) -> Dict[str, Any]:
    """Validate, atomically install and verify a custom certificate pair"""
    clean_domain = validate_domain_name(domain)
    parsed = _validate_certificate_for_domain(cert_pem, key_pem, clean_domain)

    cert_path, _key_path, old_cert, old_key = _install_certificate_pair(cert_pem, key_pem)
    result = dict(parsed)
    result.update(_activate_certificate_change(cert_path, old_cert, old_key))
    return result


async def issue_letsencrypt_cert(domain: str, email: Optional[str] = None) -> Dict[str, Any]:
    """
    Issue Let's Encrypt certificate using Certbot
    Supports Webroot mode (if Nginx /var/www/certbot is available) or Standalone mode
    """
    clean_domain = validate_domain_name(domain)
    valid_email = validate_email_address(email)

    certbot_bin = shutil.which("certbot")
    if not certbot_bin:
        raise RuntimeError(
            "Certbot binary is not installed on this system. "
            "Please install it using 'apt-get install -y certbot', "
            "or use the 'Custom SSL' tab to paste a Cloudflare Origin CA certificate."
        )

    cmd = [
        certbot_bin,
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "--cert-name", clean_domain,
        "-d", clean_domain
    ]

    if valid_email:
        cmd.extend(["--email", valid_email])
    else:
        cmd.append("--register-unsafely-without-email")

    # Webroot /var/www/certbot is shared with nginx and the ACME fallback route.
    candidate_webroots = [
        Path("/var/www/certbot"),
        Path("/var/www/html"),
        Path("/var/www"),
    ]
    webroot_path = None
    for cand in candidate_webroots:
        if not cand.is_dir():
            continue
        try:
            fd, probe = tempfile.mkstemp(prefix=".smite-acme-", dir=str(cand))
            os.close(fd)
            os.unlink(probe)
            webroot_path = cand
            break
        except OSError as e:
            logger.debug(f"Webroot {cand} is not writable: {e}")

    if webroot_path:
        cmd.extend(["--webroot", "-w", str(webroot_path)])
    else:
        cmd.append("--standalone")

    logger.info(f"Running certbot command: {' '.join(cmd)}")
    
    loop = asyncio.get_running_loop()
    
    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    try:
        proc = await loop.run_in_executor(None, _run)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Certbot timed out after 120 seconds. Ensure port 80 is accessible from the internet.")

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        logger.error(f"Certbot failed: {err_msg}")
        raise RuntimeError(f"Let's Encrypt issuance failed: {err_msg}")

    # Certbot outputs to /etc/letsencrypt/live/{domain}/
    live_dir = Path(f"/etc/letsencrypt/live/{clean_domain}")
    fullchain = live_dir / "fullchain.pem"
    privkey = live_dir / "privkey.pem"

    if not fullchain.exists() or not privkey.exists():
        raise RuntimeError(f"Certbot completed but certificate files not found in {live_dir}")

    cert_pem = fullchain.read_text(encoding="utf-8")
    key_pem = privkey.read_text(encoding="utf-8")
    parsed = _validate_certificate_for_domain(cert_pem, key_pem, clean_domain)

    cert_path, _key_path, old_cert, old_key = _install_certificate_pair(cert_pem, key_pem)
    cert_info = dict(parsed)
    cert_info.update(_activate_certificate_change(cert_path, old_cert, old_key))
    return cert_info


async def renew_letsencrypt_cert(force: bool = True) -> Dict[str, Any]:
    """Renew the active Let's Encrypt certificate (forced for manual renewals, due-only for auto-renew)"""
    certbot_bin = shutil.which("certbot")
    if not certbot_bin:
        raise RuntimeError("Certbot binary is not installed.")

    cfg = await get_ssl_db_settings()
    domain = cfg.get("domain") or ""
    if not domain:
        raise RuntimeError("No active SSL domain is configured for renewal.")
    clean_domain = validate_domain_name(domain)

    cmd = [certbot_bin, "renew", "--non-interactive", "--cert-name", clean_domain]
    if force:
        cmd.append("--force-renewal")

    loop = asyncio.get_running_loop()

    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    try:
        proc = await loop.run_in_executor(None, _run)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Certbot renewal timed out after 180 seconds.")
    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"Certbot renewal failed: {err_msg}")

    live_dir = Path(f"/etc/letsencrypt/live/{clean_domain}")
    fullchain = live_dir / "fullchain.pem"
    privkey = live_dir / "privkey.pem"
    if not fullchain.exists() or not privkey.exists():
        raise RuntimeError(f"Certbot completed but certificate files not found in {live_dir}")

    new_cert_pem = fullchain.read_text(encoding="utf-8")
    new_key_pem = privkey.read_text(encoding="utf-8")
    parsed = _validate_certificate_for_domain(new_cert_pem, new_key_pem, clean_domain)

    cert_path, key_path = get_cert_paths()
    try:
        current_cert = cert_path.read_text(encoding="utf-8").strip()
    except OSError:
        current_cert = None
    try:
        current_key = key_path.read_text(encoding="utf-8").strip()
    except OSError:
        current_key = None

    renewed = not (
        current_cert == new_cert_pem.strip()
        and current_key == new_key_pem.strip()
    )

    cert_info = dict(parsed)
    if renewed:
        cert_path, _key_path, old_cert, old_key = _install_certificate_pair(new_cert_pem, new_key_pem)
        cert_info.update(_activate_certificate_change(cert_path, old_cert, old_key))
    else:
        cert_info.update({
            "activation": "none",
            "activation_message": "Certificate is not due for renewal; no changes were applied."
        })
    cert_info["renewed"] = renewed

    await save_ssl_db_settings(
        enabled=True,
        domain=clean_domain,
        email=cfg.get("email"),
        mode="letsencrypt",
        auto_renew=cfg.get("auto_renew", True)
    )
    return cert_info


async def remove_ssl_cert() -> Dict[str, Any]:
    """Disable SSL, archive and remove the active pair, and report the required restart"""
    archive_existing_certs()
    cert_path, key_path = get_cert_paths()
    
    # Remove active cert files; the running panel/nginx keep their already-loaded copy
    try:
        if cert_path.exists():
            cert_path.unlink()
        if key_path.exists():
            key_path.unlink()
    except Exception as e:
        logger.warning(f"Error removing certificate files: {e}")

    env_updated = await save_ssl_db_settings(
        enabled=False,
        domain="",
        email="",
        mode="letsencrypt",
        auto_renew=False
    )

    message = (
        "SSL disabled in the panel and certificate files removed. "
        "The running panel and nginx keep serving the previously loaded certificate until the stack is restarted; "
        "restarting nginx with SMITE_SSL_DOMAIN unset/empty (or removing /etc/letsencrypt/live entries) is required to serve HTTP only. "
        "Active node tunnels are unaffected."
    )
    if env_updated is False:
        message += " The .env file is read-only in this deployment; update HTTPS_ENABLED/SMITE_SSL_DOMAIN manually before restarting."
    return {
        "status": "success",
        "activation": "restart_required",
        "activation_message": message,
        "message": message
    }


async def _check_and_renew_active_cert():
    """Inspect active SSL certificate and renew if <= 30 days remain"""
    cfg = await get_ssl_db_settings()
    if not cfg.get("enabled") or not cfg.get("auto_renew"):
        return

    # Only auto-renew automated Let's Encrypt certificates
    if cfg.get("mode") != "letsencrypt":
        return

    cert_info = get_active_certificate_info()
    if not cert_info.get("installed") or not cert_info.get("valid"):
        return

    days = cert_info.get("days_remaining", 999)
    domain = cfg.get("domain", "")

    if days <= 30:
        logger.info(f"SSL certificate for {domain} has {days} days remaining. Triggering auto-renewal...")
        try:
            new_info = await renew_letsencrypt_cert(force=False)
            if not new_info.get("renewed", False):
                logger.info(f"SSL certificate for {domain} is not due for renewal; no files changed.")
                return
            new_days = new_info.get("days_remaining", 0)
            activation_note = ""
            if new_info.get("activation") == "restart_required":
                activation_note = f"\n\n⚠️ {new_info.get('activation_message', 'A restart is required to activate the new certificate.')}"
            logger.info(f"SSL auto-renewal successful for {domain}. New expiration: {new_days} days.")
            
            # Notify Telegram admins
            try:
                from app.telegram_bot import telegram_bot
                if hasattr(telegram_bot, "send_admin_alert"):
                    await telegram_bot.send_admin_alert(
                        f"🛡️ [Smite Panel] SSL Certificate Auto-Renewed!\n\n"
                        f"• Domain: {domain}\n"
                        f"• Days Remaining: {new_days} days\n"
                        f"• Activation: {new_info.get('activation', 'unknown')}"
                        f"{activation_note}"
                    )
            except Exception as te:
                logger.debug(f"Could not dispatch telegram alert: {te}")
                
        except Exception as ren_err:
            logger.error(f"SSL auto-renewal failed for {domain}: {ren_err}")
            try:
                from app.telegram_bot import telegram_bot
                if hasattr(telegram_bot, "send_admin_alert"):
                    await telegram_bot.send_admin_alert(
                        f"⚠️ [Smite Panel Warning] SSL Auto-Renewal Failed!\n\n"
                        f"• Domain: {domain}\n"
                        f"• Days Remaining: {days} days\n"
                        f"• Error: {ren_err}\n\n"
                        f"Please review your domain settings or renew manually in Settings."
                    )
            except Exception:
                pass


async def ssl_auto_renew_worker(app):
    """
    Background worker that runs every 12 hours.
    Inspects active SSL certificate and triggers automatic renewal if < 30 days remain.
    Dispatches alerts to Telegram admins if configured.
    """
    logger.info("SSL Auto-Renewal background worker started")
    # Initial startup check after 15 seconds to catch pending renewals on boot
    try:
        await asyncio.sleep(15)
        from app.mutation_guard import mutation_guard
        if mutation_guard.is_engaged():
            logger.warning("Mutation guard engaged: skipping initial SSL auto-renewal check")
        else:
            await _check_and_renew_active_cert()
    except asyncio.CancelledError:
        logger.info("SSL Auto-Renewal background worker cancelled during initial delay")
        return
    except Exception as e:
        logger.warning(f"Initial SSL auto-renewal check error: {e}")

    while True:
        try:
            # Sleep 12 hours in 60s increments to allow graceful shutdown
            for _ in range(720):
                await asyncio.sleep(60)

            from app.mutation_guard import mutation_guard
            if mutation_guard.is_engaged():
                logger.warning("Mutation guard engaged: skipping SSL auto-renewal pass")
                continue

            await _check_and_renew_active_cert()
        except asyncio.CancelledError:
            logger.info("SSL Auto-Renewal background worker cancelled")
            break
        except Exception as e:
            logger.error(f"Error in SSL auto-renewal worker: {e}", exc_info=True)
            await asyncio.sleep(60)
