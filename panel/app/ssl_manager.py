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
import logging
import asyncio
import ipaddress
import subprocess
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
        
        if days_remaining <= 0:
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


def _update_env_ssl(enabled: bool, domain: str):
    """Safely update .env file with HTTPS_ENABLED and PANEL_DOMAIN"""
    candidate_paths = [
        Path(".env"),
        Path("/opt/smite/.env"),
        _get_base_dir() / ".env"
    ]
    for env_path in candidate_paths:
        try:
            if env_path.exists():
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
                break
        except Exception as e:
            logger.warning(f"Could not update {env_path}: {e}")


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
    _update_env_ssl(enabled, domain)


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
        archive_dir.mkdir(parents=True, exist_ok=True)
        if cert_path.exists():
            shutil.copy2(cert_path, archive_dir / "server.crt")
        if key_path.exists():
            shutil.copy2(key_path, archive_dir / "server.key")
        logger.info(f"Archived previous certificates to {archive_dir}")


def install_custom_ssl(domain: str, cert_pem: str, key_pem: str) -> Dict[str, Any]:
    """Install custom certificate and private key safely"""
    clean_domain = validate_domain_name(domain)
    # 1. Validate key pair & x509 certificate (including size limits)
    validate_cert_and_key(cert_pem, key_pem)
    parsed = parse_certificate_pem(cert_pem)
    if not parsed.get("valid"):
        raise ValueError(f"Invalid certificate: {parsed.get('error')}")

    # 2. Archive previous certs
    archive_existing_certs()

    # 3. Write new certs
    cert_path, key_path = get_cert_paths()
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    
    cert_path.write_text(cert_pem.strip() + "\n", encoding="utf-8")
    key_path.write_text(key_pem.strip() + "\n", encoding="utf-8")
    
    try:
        os.chmod(str(key_path), 0o600)
    except Exception:
        pass

    # 4. Attempt nginx reload
    reload_nginx_service()
    
    return parsed


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
        "-d", clean_domain
    ]

    if valid_email:
        cmd.extend(["--email", valid_email])
    else:
        cmd.append("--register-unsafely-without-email")

    # Check candidate webroot directories (Nginx active on host or container)
    candidate_webroots = [
        Path("/var/www/html"),
        Path("/var/www/certbot"),
        Path("/var/www"),
    ]
    webroot_path = None
    for cand in candidate_webroots:
        if cand.exists() and cand.is_dir():
            webroot_path = cand
            break

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

    # Archive existing certs and copy new ones
    archive_existing_certs()
    cert_path, key_path = get_cert_paths()
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(fullchain, cert_path)
    shutil.copy2(privkey, key_path)
    try:
        os.chmod(str(key_path), 0o600)
    except Exception:
        pass

    # Reload Nginx
    reload_nginx_service()

    cert_info = get_active_certificate_info()
    return cert_info


async def renew_letsencrypt_cert() -> Dict[str, Any]:
    """Force renew active Let's Encrypt certificate"""
    certbot_bin = shutil.which("certbot")
    if not certbot_bin:
        raise RuntimeError("Certbot binary is not installed.")

    cmd = [certbot_bin, "renew", "--non-interactive"]
    loop = asyncio.get_running_loop()

    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    proc = await loop.run_in_executor(None, _run)
    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"Certbot renewal failed: {err_msg}")

    # Check active settings to re-copy if live directory updated
    cfg = await get_ssl_db_settings()
    domain = cfg.get("domain")
    if domain:
        live_dir = Path(f"/etc/letsencrypt/live/{domain}")
        fullchain = live_dir / "fullchain.pem"
        privkey = live_dir / "privkey.pem"
        if fullchain.exists() and privkey.exists():
            cert_path, key_path = get_cert_paths()
            shutil.copy2(fullchain, cert_path)
            shutil.copy2(privkey, key_path)

    reload_nginx_service()
    
    cert_info = get_active_certificate_info()
    await save_ssl_db_settings(
        enabled=True,
        domain=domain or "",
        email=cfg.get("email"),
        mode="letsencrypt",
        auto_renew=cfg.get("auto_renew", True)
    )
    return cert_info


async def remove_ssl_cert() -> Dict[str, Any]:
    """Safely disable SSL, archive certificates, and revert to HTTP"""
    archive_existing_certs()
    cert_path, key_path = get_cert_paths()
    
    # Remove active cert files so uvicorn / web falls back cleanly
    try:
        if cert_path.exists():
            cert_path.unlink()
        if key_path.exists():
            key_path.unlink()
    except Exception as e:
        logger.warning(f"Error removing certificate files: {e}")

    await save_ssl_db_settings(
        enabled=False,
        domain="",
        email="",
        mode="letsencrypt",
        auto_renew=False
    )
    
    reload_nginx_service()
    return {"status": "success", "message": "SSL disabled and reverted to HTTP."}


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
            new_info = await renew_letsencrypt_cert()
            new_days = new_info.get("days_remaining", 0)
            logger.info(f"SSL auto-renewal successful for {domain}. New expiration: {new_days} days.")
            
            # Notify Telegram admins
            try:
                from app.telegram_bot import telegram_bot
                if hasattr(telegram_bot, "send_admin_alert"):
                    await telegram_bot.send_admin_alert(
                        f"🛡️ [Smite Panel] SSL Certificate Auto-Renewed!\n\n"
                        f"• Domain: {domain}\n"
                        f"• Days Remaining: {new_days} days\n"
                        f"• Status: Active & Secured"
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
            
            await _check_and_renew_active_cert()
        except asyncio.CancelledError:
            logger.info("SSL Auto-Renewal background worker cancelled")
            break
        except Exception as e:
            logger.error(f"Error in SSL auto-renewal worker: {e}", exc_info=True)
            await asyncio.sleep(60)
