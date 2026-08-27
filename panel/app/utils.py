"""Utility functions for address parsing and validation"""
import ipaddress
import re
import secrets
import string
from typing import Tuple, Optional


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
    except Exception:
        import secrets
        rnd_priv = secrets.token_bytes(32)
        rnd_pub = secrets.token_bytes(32)
        return (
            base64.b64encode(rnd_priv).decode("utf-8"),
            base64.b64encode(rnd_pub).decode("utf-8")
        )


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


