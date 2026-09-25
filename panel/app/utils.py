"""Utility functions for address parsing and validation"""
import ipaddress
import re
import secrets
import string
from typing import Tuple, Optional, Any


def parse_address_port(address_str: str) -> Tuple[str, Optional[int], bool]:
    """
    Parse an address:port string, handling both IPv4 and IPv6 addresses.
    
    Supports formats:
    - IPv4: "127.0.0.1:8080" -> ("127.0.0.1", 8080, False)
    - IPv6: "[2001:db8::1]:8080" -> ("2001:db8::1", 8080, True)
    - IPv6: "2001:db8::1" -> ("2001:db8::1", None, True)
    - Hostname: "example.com:8080" -> ("example.com", 8080, False)
    
    Args:
        address_str: Address string in format "host:port" or "[ipv6]:port"
        
    Returns:
        Tuple of (host, port, is_ipv6) where port is None if not specified
    """
    if not address_str:
        return ("", None, False)
    
    address_str = address_str.strip()
    
    ipv6_bracket_match = re.match(r'^\[([^\]]+)\](?::(\d+))?$', address_str)
    if ipv6_bracket_match:
        host = ipv6_bracket_match.group(1)
        port_str = ipv6_bracket_match.group(2)
        port = int(port_str) if port_str else None
        return (host, port, True)
    
    try:
        ipaddress.IPv6Address(address_str)
        return (address_str, None, True)
    except (ValueError, ipaddress.AddressValueError):
        pass
    
    if ":" in address_str:
        parts = address_str.rsplit(":", 1)
        if len(parts) == 2:
            host_part = parts[0]
            port_str = parts[1]
            
            try:
                ipaddress.IPv6Address(host_part)
                return (host_part, int(port_str), True)
            except (ValueError, ipaddress.AddressValueError):
                try:
                    port = int(port_str)
                    return (host_part, port, False)
                except ValueError:
                    return (address_str, None, False)
    
    return (address_str, None, False)


def format_address_port(host: str, port: Optional[int] = None) -> str:
    """
    Format host and port into address:port string, handling IPv6 addresses.
    
    Args:
        host: Host address (IPv4, IPv6, or hostname)
        port: Port number (optional)
        
    Returns:
        Formatted string: "host:port" or "[ipv6]:port" or "host"
    """
    if not host:
        return ""
    
    try:
        ipaddress.IPv6Address(host)
        if port is not None:
            return f"[{host}]:{port}"
        return host
    except (ValueError, ipaddress.AddressValueError):
        if port is not None:
            return f"{host}:{port}"
        return host


def is_valid_ip_address(address: str) -> bool:
    """
    Check if a string is a valid IP address (IPv4 or IPv6).
    
    Args:
        address: String to validate
        
    Returns:
        True if valid IP address, False otherwise
    """
    try:
        ipaddress.ip_address(address)
        return True
    except (ValueError, ipaddress.AddressValueError):
        return False


def is_valid_ipv6_address(address: str) -> bool:
    """
    Check if a string is a valid IPv6 address.
    
    Args:
        address: String to validate
        
    Returns:
        True if valid IPv6 address, False otherwise
    """
    try:
        ipaddress.IPv6Address(address)
        return True
    except (ValueError, ipaddress.AddressValueError):
        return False


def generate_token(length: int = 16) -> str:
    """
    Generate a random secure token.
    
    Args:
        length: Length of the token (default: 16)
        
    Returns:
        Random token string
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_noise_keypair() -> tuple[str, str]:
    """
    Generate an X25519 keypair for Rathole Noise protocol (Noise_KK_25519_ChaChaPoly_BLAKE2s).
    
    Returns:
        tuple of (private_key_base64, public_key_base64)
    """
    import base64
    try:
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives import serialization
        
        priv = x25519.X25519PrivateKey.generate()
        pub = priv.public_key()
        
        priv_bytes = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_bytes = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )
        return (
            base64.b64encode(priv_bytes).decode("utf-8"),
            base64.b64encode(pub_bytes).decode("utf-8")
        )
    except ImportError as e:
        raise RuntimeError("cryptography package is required for X25519 noise key generation") from e


def generate_rathole_tls_bundle(
    common_name: str,
    san_list: Optional[list] = None,
    password: Optional[str] = None
) -> tuple[str, str, str]:
    """
    Generate self-signed Root CA and Server Certificate bundle in PKCS#12 (.pfx) format for Rathole native-tls.
    
    Args:
        common_name: Primary hostname or IP for the server certificate.
        san_list: Optional list of alternate DNS names or IPs for SubjectAlternativeName.
        password: Optional password for PKCS#12. If None, a random secure token is generated.
        
    Returns:
        tuple of (pkcs12_base64, password, ca_cert_pem)
    """
    import base64
    import ipaddress
    from datetime import datetime, timedelta, timezone
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import Encoding, BestAvailableEncryption, pkcs12
    except ImportError as e:
        raise RuntimeError("cryptography package is required for Rathole TLS generation") from e

    if not password:
        password = generate_token(20)

    # 1. Root CA
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Smite Rathole Root CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    # 2. Server Certificate
    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    clean_cn = str(common_name).strip("[]")
    srv_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, clean_cn)])

    sans = []
    # Always include 127.0.0.1 and localhost for local testing/monitoring
    sans.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    sans.append(x509.DNSName("localhost"))

    # Add common_name and san_list
    candidates = [clean_cn]
    if san_list:
        for s in san_list:
            if s:
                clean_s = str(s).strip("[]")
                if clean_s not in candidates:
                    candidates.append(clean_s)

    for item in candidates:
        try:
            ip_obj = ipaddress.ip_address(item)
            if not any(isinstance(existing, x509.IPAddress) and existing.value == ip_obj for existing in sans):
                sans.append(x509.IPAddress(ip_obj))
        except ValueError:
            clean_host = item.split(":")[0].strip()
            if clean_host and not any(isinstance(existing, x509.DNSName) and existing.value == clean_host for existing in sans):
                sans.append(x509.DNSName(clean_host))

    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(srv_name)
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    pfx_bytes = pkcs12.serialize_key_and_certificates(
        name=b"rathole",
        key=srv_key,
        cert=srv_cert,
        cas=[ca_cert],
        encryption_algorithm=BestAvailableEncryption(password.encode("utf-8"))
    )

    ca_pem = ca_cert.public_bytes(Encoding.PEM).decode("utf-8")
    pfx_b64 = base64.b64encode(pfx_bytes).decode("utf-8")

    return pfx_b64, password, ca_pem


def sanitize_spec_for_log(spec: Any) -> Any:
    """Sanitize sensitive fields (tokens, keys, passwords) before logging."""
    if not isinstance(spec, dict):
        return spec
    sensitive_keys = {
        "token", "auth_token", "password", "key", "auth",
        "server_private_key", "client_private_key", "noise_key",
        "server_key", "client_key", "tls_pkcs12_b64", "tls_pkcs12_password",
        "tls_ca_cert_pem"
    }
    sanitized = {}
    for k, v in spec.items():
        if isinstance(k, str) and k.lower() in sensitive_keys and v:
            sanitized[k] = "***REDACTED***"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_spec_for_log(v)
        elif isinstance(v, list):
            sanitized[k] = [sanitize_spec_for_log(item) if isinstance(item, dict) else item for item in v]
        else:
            sanitized[k] = v
    return sanitized


async def measure_precise_ping(ip_or_host: Optional[str], fallback_ports: Optional[list] = None) -> Optional[int]:
    """
    Measures true network layer-3 (ICMP) or layer-4 (raw TCP handshake) round-trip latency in milliseconds.
    Returns the exact wire network ping without HTTP/REST application serialization overhead.
    """
    if not ip_or_host:
        return None
        
    import asyncio
    import os
    import re
    import subprocess
    import time

    host = ip_or_host.strip()
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    elif ":" in host and host.count(":") == 1:
        host = host.split(":")[0]
    
    # 1. Try ICMP ping first (matches standard OS terminal ping output)
    try:
        is_win = os.name == 'nt'
        cmd = ["ping", "-n", "1", "-w", "1000", host] if is_win else ["ping", "-c", "1", "-W", "1", host]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
        out = stdout.decode('utf-8', errors='ignore')
        match = re.search(r'time[=<]\s*([0-9.]+)\s*ms', out, re.IGNORECASE)
        if not match:
            match = re.search(r'Average\s*=\s*([0-9]+)ms', out, re.IGNORECASE)
        if match:
            val = float(match.group(1))
            return max(1, round(val))
    except Exception:
        pass

    # 2. Try fast TCP Handshake probe (Layer 4 true RTT - Works if ICMP blocked by firewall)
    candidate_ports = fallback_ports or [443, 80, 22, 8080, 7000]
    for port in candidate_ports:
        t_start = time.perf_counter()
        try:
            conn = asyncio.open_connection(host, port)
            _, writer = await asyncio.wait_for(conn, timeout=1.0)
            elapsed = (time.perf_counter() - t_start) * 1000
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return max(1, round(elapsed))
        except (ConnectionRefusedError, ConnectionResetError):
            # Target OS kernel returned RST packet in exactly 1 RTT
            elapsed = (time.perf_counter() - t_start) * 1000
            if elapsed < 800:
                return max(1, round(elapsed))
        except Exception:
            continue

    return None


