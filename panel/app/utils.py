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

