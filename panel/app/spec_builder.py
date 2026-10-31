"""Centralized Spec Builder Engine for Smite

Consolidates all tunnel configuration spec generation logic for all 5 tunnel cores:
- Rathole
- Backhaul
- Chisel
- FRP
- GOST

Eliminates code duplication across:
- panel/app/routers/tunnels.py
- panel/app/routers/core_health.py
- panel/main.py
- panel/app/tunnel_reapply_manager.py
"""
import base64
import hashlib
import os
import re
import random
import logging
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger(__name__)

try:
    from app.utils import generate_token, generate_noise_keypair, generate_rathole_tls_bundle, format_address_port, is_safe_backend_url
except ImportError:
    try:
        from panel.app.utils import generate_token, generate_noise_keypair, generate_rathole_tls_bundle, format_address_port, is_safe_backend_url
    except ImportError:
        generate_token = None
        generate_noise_keypair = None
        generate_rathole_tls_bundle = None
        format_address_port = None
        is_safe_backend_url = None


def _safe_noise_keypair() -> Tuple[str, str]:
    """Generate a valid 32-byte Base64-encoded X25519 keypair for Rathole Noise protocol.
    If generate_noise_keypair fails or is unavailable, generates valid Base64 keys
    to prevent upstream Rathole x25519-dalek Base64 parser panic.
    """
    if generate_noise_keypair:
        try:
            pair = generate_noise_keypair()
            if isinstance(pair, (tuple, list)) and len(pair) == 2 and _is_valid_base64_32bytes(pair[0]) and _is_valid_base64_32bytes(pair[1]):
                return (pair[0], pair[1])
        except Exception as e:
            logger.warning(f"generate_noise_keypair failed, falling back to local X25519 generation: {e}")
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
            base64.b64encode(pub_bytes).decode("utf-8"),
        )
    except Exception as e:
        logger.warning(f"cryptography X25519 generation failed, falling back to urandom: {e}")
        return (
            base64.b64encode(os.urandom(32)).decode("utf-8"),
            base64.b64encode(os.urandom(32)).decode("utf-8"),
        )

def _to_bool(val: Any) -> Optional[bool]:
    """Safely coerce boolean-like values, rejecting falsey string representations like 'false' or '0'."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off"):
            return False
    return None


def _is_valid_base64_32bytes(val: Any) -> bool:
    """Check if value is a non-empty string that decodes to exactly 32 raw bytes (e.g. X25519 key)."""
    if not val or not isinstance(val, str):
        return False
    try:
        decoded = base64.b64decode(val.strip(), validate=True)
        return len(decoded) == 32
    except Exception:
        return False

if format_address_port is None:
    def format_address_port(host: str, port: Optional[int] = None) -> str:
        if not host:
            return ""
        clean = host.strip("[]")
        if ":" in clean:
            return f"[{clean}]:{port}" if port is not None else f"[{clean}]"
        return f"{host}:{port}" if port is not None else host

if is_safe_backend_url is None:
    def is_safe_backend_url(url: Optional[str]) -> bool:
        if not url or not isinstance(url, str):
            return False
        clean_url = url.strip()
        lower_url = clean_url.lower()
        if not (lower_url.startswith("http://") or lower_url.startswith("https://")):
            return False
        import urllib.parse
        import ipaddress
        try:
            parsed = urllib.parse.urlparse(clean_url)
            hostname = parsed.hostname
            if not hostname:
                return False
            if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                return False
            try:
                ip = ipaddress.ip_address(hostname)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                    return False
            except ValueError:
                pass
            return True
        except Exception:
            return False



def _resolve_tunnel_directionality(tunnel: Any, spec: Dict[str, Any]) -> bool:
    """Resolve authoritative directionality (is_reverse) across tunnel and spec.
    Honors force_direct (spec or tunnel) first to enforce direct mode.
    Handles MagicMock objects and string boolean representations safely.
    """
    tunnel_spec = getattr(tunnel, "spec", None) if hasattr(tunnel, "spec") else None
    if not isinstance(tunnel_spec, dict):
        tunnel_spec = {}

    force_direct = (
        _to_bool(spec.get("force_direct")) is True
        or _to_bool(tunnel_spec.get("force_direct")) is True
        or _to_bool(getattr(tunnel, "force_direct", None)) is True
    )
    if force_direct:
        return False

    raw_is_reverse = _to_bool(getattr(tunnel, "is_reverse", None))
    if raw_is_reverse is not None:
        return raw_is_reverse

    spec_rev = _to_bool(tunnel_spec.get("is_reverse"))
    if spec_rev is not None:
        return spec_rev

    local_spec_rev = _to_bool(spec.get("is_reverse"))
    if local_spec_rev is not None:
        return local_spec_rev

    return True


GAMING_PROFILE = "gaming-v1"


def _has_gaming_profile(tunnel, spec: Dict[str, Any]) -> bool:
    """Low-latency profile marker, set by the panel on create / manual re-apply of
    UDP and gaming tunnels (never by background re-applies, so a running tunnel's
    spec - and therefore its process - is never changed behind the user's back)."""
    return (spec or {}).get("latency_profile") == GAMING_PROFILE


def is_valid_ipv6(addr: str) -> bool:
    """Check if address is IPv6 (handles brackets, optional ports, rejects IPv4:port)"""
    if not addr or not isinstance(addr, str):
        return False
    clean = addr.strip()
    if "://" in clean:
        clean = clean.split("://", 1)[1]
    if clean.startswith("[") and "]" in clean:
        clean = clean[1:clean.index("]")]
    elif ":" in clean:
        if "." in clean and clean.count(":") == 1:
            return False
    try:
        import ipaddress
        return ipaddress.ip_address(clean).version == 6
    except Exception:
        return clean.count(":") >= 2 and " " not in clean


def resolve_effective_sni(tunnel: Any, spec: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Resolve unified SNI / camouflage domain across custom_sni, stealth_domain, and sni."""
    spec_dict = spec if isinstance(spec, dict) else (getattr(tunnel, "spec", None) if isinstance(getattr(tunnel, "spec", None), dict) else {})
    candidates = [
        getattr(tunnel, "custom_sni", None),
        getattr(tunnel, "stealth_domain", None),
        spec_dict.get("custom_sni"),
        spec_dict.get("stealth_domain"),
        spec_dict.get("sni"),
    ]
    for c in candidates:
        if c is not None:
            s = str(c).strip()
            if s:
                return s
    return None


def _cert_covers_identities(cert_pem: Optional[str], dns_names: List[str], ip_addresses: List[str]) -> bool:
    """True when every requested identity is present in the certificate SAN."""
    if not cert_pem:
        return False
    try:
        import ipaddress
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return False
        san_dns = {str(n).lower() for n in san.get_values_for_type(x509.DNSName)}
        san_ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        for name in dns_names:
            if not name:
                continue
            name_clean = str(name).strip().lower()
            try:
                norm_ip = str(ipaddress.ip_address(name_clean.strip("[]")))
                if norm_ip not in san_ips and name_clean not in san_dns:
                    return False
            except ValueError:
                if name_clean not in san_dns:
                    return False
        for ip in ip_addresses:
            if not ip:
                continue
            try:
                normalized_ip = str(ipaddress.ip_address(str(ip).strip("[]")))
            except ValueError:
                if str(ip).lower() not in san_dns:
                    return False
                continue
            if normalized_ip not in san_ips:
                return False
        return True
    except Exception:
        return False


def parse_ports_list(spec_or_ports: Any) -> List[int]:
    """Extract integer ports from various spec input formats (int, str, dict, ranges, comma-separated)"""
    if isinstance(spec_or_ports, dict):
        raw_ports = spec_or_ports.get("ports")
        if not raw_ports:
            raw_ports = spec_or_ports.get("listen_port") or spec_or_ports.get("remote_port") or spec_or_ports.get("public_port")
    else:
        raw_ports = spec_or_ports

    if raw_ports is None:
        return []

    if isinstance(raw_ports, (list, tuple, set)):
        candidates = list(raw_ports)
    else:
        candidates = [raw_ports]

    flattened = []
    for item in candidates:
        if item is None:
            continue
        if isinstance(item, str) and ("," in item or "\n" in item or "\r" in item):
            for part in re.split(r'[\r\n,]+', item):
                p_str = part.strip()
                if p_str:
                    flattened.append(p_str)
        else:
            flattened.append(item)

    clean_ports: List[int] = []
    for p in flattened:
        if isinstance(p, dict):
            val = p.get("remote") or p.get("remote_port") or p.get("local") or p.get("local_port") or p.get("port")
            if val is not None and str(val).isdigit():
                port_num = int(val)
                if 1 <= port_num <= 65535 and port_num not in clean_ports:
                    clean_ports.append(port_num)
        elif isinstance(p, str) and "=" in p:
            sides = [s.strip() for s in p.split("=")]
            for side in sides:
                if ":" in side:
                    side = side.rsplit(":", 1)[-1].strip()
                if side.isdigit():
                    port_num = int(side)
                    if 1 <= port_num <= 65535 and port_num not in clean_ports:
                        clean_ports.append(port_num)
                elif "-" in side:
                    parts = side.split("-", 1)
                    if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                        if 1 <= start_p <= end_p <= 65535 and (end_p - start_p) <= 500:
                            for r_p in range(start_p, end_p + 1):
                                if r_p not in clean_ports:
                                    clean_ports.append(r_p)
        elif isinstance(p, str) and "-" in p:
            parts = p.split("-", 1)
            if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                if 1 <= start_p <= end_p <= 65535 and (end_p - start_p) <= 500:
                    for r_p in range(start_p, end_p + 1):
                        if r_p not in clean_ports:
                            clean_ports.append(r_p)
        elif isinstance(p, (int, str)) and str(p).strip().isdigit():
            port_num = int(str(p).strip())
            if 1 <= port_num <= 65535 and port_num not in clean_ports:
                clean_ports.append(port_num)
    return clean_ports


def build_rathole_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Rathole core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    transport = (
        server_spec.get("transport_type")
        or server_spec.get("transport")
        or getattr(tunnel, "transport_type", None)
        or "tcp"
    )
    tunnel_type = getattr(tunnel, "type", None) or server_spec.get("tunnel_type") or "tcp"

    token = server_spec.get("token")
    if not token:
        token = generate_token() if generate_token else "default-token"
        server_spec["token"] = token
        if tunnel.spec is not None:
            tunnel.spec["token"] = token

    ports = parse_ports_list(spec)
    port_ranges = getattr(tunnel, "port_ranges", None) or spec.get("port_ranges")
    if port_ranges:
        ranges_ports = parse_ports_list(port_ranges)
        for rp in ranges_ports:
            if rp not in ports:
                ports.append(rp)
        server_spec["port_ranges"] = port_ranges
        client_spec["port_ranges"] = port_ranges
    if not ports:
        proxy_port = server_spec.get("remote_port") or server_spec.get("listen_port")
        if proxy_port and str(proxy_port).isdigit():
            ports = [int(proxy_port)]


    # Control port assignment
    control_port = server_spec.get("control_port")
    try:
        ctrl_p = int(control_port) if control_port else 0
    except (ValueError, TypeError):
        ctrl_p = 0
    if ctrl_p < 1024 or ctrl_p > 65535 or ctrl_p in ports:
        port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
        control_port = 25000 + (port_hash % 25000)
        while control_port in ports:
            control_port += 1
        if tunnel.spec is not None:
            tunnel.spec["control_port"] = control_port
    else:
        control_port = ctrl_p

    # Noise protocol support
    use_noise = (
        server_spec.get("noise")
        or server_spec.get("use_noise", False)
        or transport.lower() == "noise"
    )
    if use_noise:
        server_priv = spec.get("server_private_key") or spec.get("local_private_key")
        server_pub = spec.get("server_public_key") or spec.get("remote_public_key")
        client_priv = spec.get("client_private_key") or spec.get("local_private_key")
        client_pub = spec.get("client_public_key") or spec.get("remote_public_key")

        valid_keys = (
            _is_valid_base64_32bytes(server_priv)
            and _is_valid_base64_32bytes(server_pub)
            and _is_valid_base64_32bytes(client_priv)
            and _is_valid_base64_32bytes(client_pub)
            and (server_priv != client_priv)
            and (server_pub != client_pub)
        )
        if not valid_keys:
            s_priv, s_pub = _safe_noise_keypair()
            c_priv, c_pub = _safe_noise_keypair()
            server_priv, server_pub = s_priv, s_pub
            client_priv, client_pub = c_priv, c_pub
            if hasattr(tunnel, "spec") and tunnel.spec is not None and isinstance(tunnel.spec, dict):
                tunnel.spec["server_private_key"] = server_priv
                tunnel.spec["server_public_key"] = server_pub
                tunnel.spec["client_private_key"] = client_priv
                tunnel.spec["client_public_key"] = client_pub

        server_spec["local_private_key"] = server_priv
        server_spec["remote_public_key"] = client_pub
        client_spec["local_private_key"] = client_priv
        client_spec["remote_public_key"] = server_pub

    server_spec["bind_addr"] = f"0.0.0.0:{control_port}"
    server_spec["control_port"] = control_port
    server_spec["ports"] = ports
    server_spec["transport_type"] = transport
    server_spec["transport"] = transport
    server_spec["tunnel_type"] = tunnel_type
    server_spec["type"] = tunnel_type
    server_spec["token"] = token

    transport_lower = transport.lower()
    if transport_lower in ("websocket", "ws", "wss"):
        use_tls = (transport_lower == "wss") or bool(server_spec.get("websocket_tls") or server_spec.get("tls"))
        server_spec["websocket_tls"] = use_tls
        client_spec["websocket_tls"] = use_tls

        if hasattr(tunnel, "custom_sni") or hasattr(tunnel, "stealth_domain"):
            custom_sni = (
                (tunnel.custom_sni.strip() if getattr(tunnel, "custom_sni", None) else None)
                or (tunnel.stealth_domain.strip() if getattr(tunnel, "stealth_domain", None) else None)
            )
        else:
            custom_sni = (
                server_spec.get("custom_sni")
                or server_spec.get("stealth_domain")
            )
        if custom_sni:
            client_spec["custom_sni"] = custom_sni
            server_spec["custom_sni"] = custom_sni
        else:
            server_spec.pop("custom_sni", None)
            server_spec.pop("stealth_domain", None)
            client_spec.pop("custom_sni", None)
            client_spec.pop("stealth_domain", None)
            if getattr(tunnel, "spec", None) is not None and isinstance(tunnel.spec, dict):
                tunnel.spec.pop("custom_sni", None)
                tunnel.spec.pop("stealth_domain", None)

        host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
        proto = "wss://" if use_tls else "ws://"
        client_spec["remote_addr"] = f"{proto}{host_part}:{control_port}"

        if use_tls:
            pfx_b64 = spec.get("tls_pkcs12_b64")
            pfx_pwd = spec.get("tls_pkcs12_password")
            ca_pem = spec.get("tls_ca_cert_pem")

            if not (pfx_b64 and pfx_pwd and ca_pem):
                san_list = [iran_node_ip]
                if custom_sni and custom_sni != iran_node_ip:
                    san_list.append(custom_sni)

                common_name = custom_sni or iran_node_ip
                if generate_rathole_tls_bundle:
                    pfx_b64, pfx_pwd, ca_pem = generate_rathole_tls_bundle(common_name=common_name, san_list=san_list)
                else:
                    pfx_b64, pfx_pwd, ca_pem = "mock-pfx", "mock-pwd", "-----BEGIN CERTIFICATE-----\nmock\n-----END CERTIFICATE-----"

                if getattr(tunnel, "spec", None) is not None:
                    tunnel.spec["tls_pkcs12_b64"] = pfx_b64
                    tunnel.spec["tls_pkcs12_password"] = pfx_pwd
                    tunnel.spec["tls_ca_cert_pem"] = ca_pem

            server_spec["tls_pkcs12_b64"] = pfx_b64
            server_spec["tls_pkcs12_password"] = pfx_pwd
            client_spec["tls_ca_cert_pem"] = ca_pem
            if not custom_sni:
                client_spec["custom_sni"] = iran_node_ip
    else:
        host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
        client_spec["remote_addr"] = f"{host_part}:{control_port}"

    client_spec["control_port"] = control_port
    client_spec["transport_type"] = transport
    client_spec["transport"] = transport
    client_spec["tunnel_type"] = tunnel_type
    client_spec["type"] = tunnel_type
    client_spec["token"] = token
    client_spec["ports"] = ports

    if getattr(tunnel, "spec", None) is not None:
        tunnel.spec["remote_addr"] = client_spec["remote_addr"]
        tunnel.spec["control_port"] = control_port
        tunnel.spec["ports"] = ports
        if port_ranges:
            tunnel.spec["port_ranges"] = port_ranges

    return server_spec, client_spec



def build_backhaul_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Backhaul core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    # Model attributes inheritance
    tunnel_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or server_spec.get("type")
        or server_spec.get("tunnel_type")
        or "tcp"
    ).lower()

    raw_transport = (
        getattr(tunnel, "transport_type", None)
        or server_spec.get("transport")
        or server_spec.get("transport_type")
        or server_spec.get("type")
        or "tcpmux"
    )
    raw_transport_str = str(raw_transport).lower()

    security_type = (
        getattr(tunnel, "security_type", None)
        or server_spec.get("security_type")
        or "none"
    ).lower()

    if hasattr(tunnel, "custom_sni") or hasattr(tunnel, "stealth_domain"):
        custom_sni = (
            (tunnel.custom_sni.strip() if getattr(tunnel, "custom_sni", None) else None)
            or (tunnel.stealth_domain.strip() if getattr(tunnel, "stealth_domain", None) else None)
        )
    else:
        custom_sni = (
            server_spec.get("custom_sni")
            or server_spec.get("stealth_domain")
        )
    if custom_sni:
        server_spec["custom_sni"] = custom_sni
        client_spec["custom_sni"] = custom_sni
    else:
        server_spec.pop("custom_sni", None)
        server_spec.pop("stealth_domain", None)
        client_spec.pop("custom_sni", None)
        client_spec.pop("stealth_domain", None)
        if getattr(tunnel, "spec", None) is not None and isinstance(tunnel.spec, dict):
            tunnel.spec.pop("custom_sni", None)
            tunnel.spec.pop("stealth_domain", None)

    server_spec["security_type"] = security_type
    client_spec["security_type"] = security_type

    port_ranges = getattr(tunnel, "port_ranges", None) or server_spec.get("port_ranges")
    if port_ranges:
        server_spec["port_ranges"] = port_ranges
        client_spec["port_ranges"] = port_ranges

    # Differentiate between:
    # 1. Pure UDP request (raw_transport == "udp"): Upstream Musixal/Backhaul has no "udp" transport
    #    and crashes with exit code 1 if transport="udp". It must be mapped to transport="tcp" with accept_udp=True.
    # 2. UDP-over-TCP encapsulation (accept_udp = True with transport = "tcp")
    is_pure_udp = raw_transport_str == "udp"
    is_udp_over_tcp = (
        server_spec.get("accept_udp") is True
        or (tunnel_type in ("udp", "tcp+udp"))
        or (server_spec.get("type") in ("udp", "tcp+udp"))
        or is_pure_udp
    )

    if is_pure_udp:
        # Upstream Backhaul does not have a raw transport = "udp". Map to TCP with accept_udp = true.
        transport = "tcp"
    elif raw_transport_str in {"tcp", "tcpmux", "ws", "wss", "wsmux", "wssmux"}:
        transport = raw_transport_str
    else:
        transport = "tcpmux"

    # Upstream Musixal/Backhaul only implements accept_udp for transport="tcp";
    # tcpmux/ws/wss/wsmux/wssmux never open a UDP listener. Map UDP-capable
    # tunnels onto tcp so UDP actually works.
    if is_udp_over_tcp and transport != "tcp":
        explicit_udp = tunnel_type in ("udp", "tcp+udp") or is_pure_udp
        if transport in {"ws", "wss", "wsmux", "wssmux"} and not explicit_udp:
            # Stale accept_udp flag on a TCP-only tunnel: drop it.
            is_udp_over_tcp = False
            server_spec["accept_udp"] = False
            client_spec["accept_udp"] = False
            if getattr(tunnel, "spec", None) is not None:
                tunnel.spec["accept_udp"] = False
        else:
            logger.info(
                f"Backhaul tunnel {getattr(tunnel, 'id', '?')}: transport '{transport}' "
                "does not support UDP upstream; switching to 'tcp' with accept_udp=true"
            )
            transport = "tcp"

    if is_udp_over_tcp:
        server_spec["accept_udp"] = True
        client_spec["accept_udp"] = True
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["accept_udp"] = True

    # WSS and TLS resolution
    use_tls = (
        transport in ("wss", "wssmux")
        or security_type == "tls"
        or bool(server_spec.get("tls"))
    )
    if use_tls:
        if transport == "ws":
            transport = "wss"
        elif transport == "wsmux":
            transport = "wssmux"
        server_spec["transport"] = transport
        client_spec["transport"] = transport

        tls_cert_pem = server_spec.get("tls_cert_pem")
        tls_key_pem = server_spec.get("tls_key_pem")
        if not (tls_cert_pem and tls_key_pem):
            try:
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                import datetime
                import ipaddress

                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, custom_sni or iran_node_ip or "backhaul-tunnel"),
                ])
                alt_names = []
                if custom_sni:
                    alt_names.append(x509.DNSName(custom_sni))
                if iran_node_ip:
                    clean_ip = iran_node_ip.strip("[]")
                    try:
                        alt_names.append(x509.IPAddress(ipaddress.ip_address(clean_ip)))
                    except ValueError:
                        alt_names.append(x509.DNSName(clean_ip))

                builder = x509.CertificateBuilder().subject_name(
                    subject
                ).issuer_name(
                    issuer
                ).public_key(
                    key.public_key()
                ).serial_number(
                    x509.random_serial_number()
                ).not_valid_before(
                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
                ).not_valid_after(
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
                )
                if alt_names:
                    builder = builder.add_extension(
                        x509.SubjectAlternativeName(alt_names),
                        critical=False,
                    )
                cert = builder.sign(key, hashes.SHA256())

                tls_key_pem = key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode('utf-8')
                tls_cert_pem = cert.public_bytes(
                    encoding=serialization.Encoding.PEM
                ).decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not generate in-memory cert for backhaul: {e}")

        if tls_cert_pem and tls_key_pem:
            server_spec["tls_cert_pem"] = tls_cert_pem
            server_spec["tls_key_pem"] = tls_key_pem
            client_spec["tls_cert_pem"] = tls_cert_pem
            if getattr(tunnel, "spec", None) is not None:
                tunnel.spec["tls_cert_pem"] = tls_cert_pem
                tunnel.spec["tls_key_pem"] = tls_key_pem

        client_spec["insecure"] = True
        server_spec["insecure"] = True

    token = server_spec.get("token")
    if not token:
        token = generate_token() if generate_token else "default-token"
        server_spec["token"] = token
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["token"] = token

    target_host = server_spec.get("target_host", "127.0.0.1")

    ports = server_spec.get("ports", [])
    if port_ranges:
        ranges_list = []
        if isinstance(port_ranges, str):
            ranges_list = [r.strip() for r in port_ranges.split(",") if r.strip()]
        elif isinstance(port_ranges, list):
            ranges_list = [str(r).strip() for r in port_ranges if str(r).strip()]
        for r_entry in ranges_list:
            if r_entry not in ports:
                if isinstance(ports, list):
                    ports.append(r_entry)
                else:
                    ports = [ports, r_entry]

    if not ports or len(ports) == 0:
        public_port = server_spec.get("public_port") or server_spec.get("remote_port") or server_spec.get("listen_port")
        target_port = server_spec.get("target_port") or public_port
        if public_port:
            p_str = str(public_port).strip()
            if target_port and p_str != str(target_port).strip():
                target_port_num = int(target_port) if str(target_port).strip().isdigit() else target_port
                ports = [f"{p_str}={format_address_port(str(target_host), target_port_num)}"]
            elif '-' in p_str:
                # Port range like 27000-27050
                ports = [p_str]
            else:
                ports = (
                    [f"{p_str}={format_address_port(str(target_host), int(p_str))}"]
                    if p_str.isdigit() else [p_str]
                )
    else:
        processed_ports = []
        for p in ports:
            if not p:
                continue
            if isinstance(p, str):
                p_clean = p.strip()
                if '=' in p_clean:
                    # Keep the mapping intact: the node adapter expands the legacy
                    # "A-B=host:A-B" shorthand into supported 1:1 mappings so the
                    # target host is never dropped, and rejects unsupported forms
                    # before the running tunnel is stopped.
                    processed_ports.append(p_clean)
                elif p_clean.isdigit():
                    processed_ports.append(
                        f"{p_clean}={format_address_port(str(target_host), int(p_clean))}"
                    )
                elif '-' in p_clean:
                    # Native Backhaul port range (e.g. 27000-27050). The node
                    # expands it 1:1 when a non-default target host is configured.
                    processed_ports.append(p_clean)
                else:
                    processed_ports.append(p_clean)
            elif isinstance(p, (int, float)):
                p_int = int(p)
                processed_ports.append(
                    f"{p_int}={format_address_port(str(target_host), p_int)}"
                )
            elif isinstance(p, dict):
                local = p.get("local") or p.get("listen_port") or p.get("public_port")
                tgt_host = p.get("target_host") or target_host
                tgt_port = p.get("target_port") or p.get("remote_port") or local
                if local:
                    processed_ports.append(f"{local}={tgt_host}:{tgt_port}")
            else:
                processed_ports.append(str(p).strip())
        ports = processed_ports

    # Extract all proxy ports (including port ranges) to avoid control port collision
    proxy_ports = set()
    for p in ports:
        if isinstance(p, str) and "=" in p:
            lp = p.split("=")[0].strip()
            if lp.isdigit():
                proxy_ports.add(int(lp))
            elif "-" in lp:
                parts = lp.split("-")
                if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                    s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                    if s_p <= e_p and (e_p - s_p) <= 2000:
                        proxy_ports.update(range(s_p, e_p + 1))
        elif isinstance(p, str) and "-" in p:
            parts = p.split("-")
            if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                if s_p <= e_p and (e_p - s_p) <= 2000:
                    proxy_ports.update(range(s_p, e_p + 1))
        elif str(p).isdigit():
            proxy_ports.add(int(p))

    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    assigned_control = server_spec.get("control_port")
    if (
        not assigned_control
        or not str(assigned_control).isdigit()
        or int(assigned_control) < 1024
        or int(assigned_control) in proxy_ports
    ):
        control_port = 25000 + (port_hash % 25000)
        while control_port in proxy_ports:
            control_port += 1
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["control_port"] = control_port
    else:
        control_port = int(assigned_control)

    bind_ip = server_spec.get("bind_ip") or server_spec.get("listen_ip") or "0.0.0.0"
    server_spec["bind_addr"] = format_address_port(bind_ip, control_port)
    server_spec["control_port"] = control_port
    server_spec["transport"] = transport
    server_spec["type"] = transport
    server_spec["tunnel_type"] = tunnel_type
    server_spec["ports"] = ports
    server_spec["token"] = token

    # Check for Gaming Mode (1-Click Optimization for competitive online gaming)
    is_gaming_mode = bool(
        getattr(tunnel, "gaming_mode", False)
        or server_spec.get("gaming_mode")
        or client_spec.get("gaming_mode")
        or _has_gaming_profile(tunnel, server_spec)
    )
    if is_gaming_mode:
        server_spec["gaming_mode"] = True
        client_spec["gaming_mode"] = True

    # Network stability & Backhaul v0.7.2 tuning options
    server_options = dict(server_spec.get("server_options") or {})
    client_options = dict(client_spec.get("client_options") or {})

    # Apply Gaming Mode presets if enabled and not explicitly customized
    if is_gaming_mode:
        server_options.setdefault("nodelay", True)
        client_options.setdefault("nodelay", True)
        server_options.setdefault("channel_size", 8192)
        client_options.setdefault("channel_size", 8192)
        server_options.setdefault("keepalive_period", 12)
        client_options.setdefault("keepalive_period", 12)
        server_options.setdefault("heartbeat", 12)
        client_options.setdefault("heartbeat", 12)
        server_options.setdefault("mux_framesize", 4096)
        client_options.setdefault("mux_framesize", 4096)
        server_options.setdefault("mux_streambuffer", 2097152)
        client_options.setdefault("mux_streambuffer", 2097152)
        server_options.setdefault("mss", 1380)
        client_options.setdefault("mss", 1380)
        client_options.setdefault("aggressive_pool", True)
    else:
        server_options.setdefault("mux_recievebuffer", 16777216)
        client_options.setdefault("mux_recievebuffer", 16777216)
        server_options.setdefault("mux_streambuffer", 4194304)
        client_options.setdefault("mux_streambuffer", 4194304)

    # Use SMUX v2 by default for multiplexed transports to avoid HOL blocking
    if transport in ("tcpmux", "wsmux", "wssmux"):
        server_options.setdefault("mux_version", 2)
        client_options.setdefault("mux_version", 2)

    # Secure defaults: disable unauthenticated web dashboard and sniffer
    server_options.setdefault("web_port", 0)
    server_options.setdefault("sniffer", False)
    client_options.setdefault("web_port", 0)
    client_options.setdefault("sniffer", False)

    for opts in (server_options, client_options):
        kp = opts.get("keepalive_period")
        if kp is None or isinstance(kp, bool) or not isinstance(kp, (int, float)) or kp <= 0:
            opts["keepalive_period"] = 12 if is_gaming_mode else 20
        hb = opts.get("heartbeat")
        if hb is None or isinstance(hb, bool) or not isinstance(hb, (int, float)) or hb <= 0:
            opts["heartbeat"] = 12 if is_gaming_mode else 20
        if is_udp_over_tcp:
            opts["accept_udp"] = True

    # Normalize numeric and boolean keys to exact types
    backhaul_numeric_keys = {
        "keepalive_period", "heartbeat", "channel_size", "mux_con", "web_port",
        "mss", "so_rcvbuf", "so_sndbuf", "mux_version", "mux_framesize",
        "mux_recievebuffer", "mux_streambuffer", "connection_pool", "retry_interval",
        "dial_timeout", "mux_session"
    }
    backhaul_boolean_keys = {
        "nodelay", "skip_optz", "sniffer", "proxy_protocol", "aggressive_pool", "accept_udp", "insecure"
    }
    for opts in (server_options, client_options):
        for nk in backhaul_numeric_keys:
            if nk in opts and opts[nk] is not None and opts[nk] != "":
                try:
                    opts[nk] = int(opts[nk])
                except (ValueError, TypeError):
                    pass
        for bk in backhaul_boolean_keys:
            if bk in opts and opts[bk] is not None:
                if isinstance(opts[bk], str):
                    opts[bk] = opts[bk].lower() in ("true", "1", "yes")
                else:
                    opts[bk] = bool(opts[bk])

    # Propagate advanced v0.7.2 options to server & client specs
    v072_keys = [
        "mss", "so_rcvbuf", "so_sndbuf", "proxy_protocol", "skip_optz",
        "channel_size", "mux_version", "mux_framesize", "mux_recievebuffer",
        "mux_streambuffer", "mux_con", "sniffer", "web_port",
        "mux_session", "edge_ip", "retry_interval", "dial_timeout", "sniffer_log"
    ]
    for k in v072_keys:
        if k in server_options and k not in server_spec:
            server_spec[k] = server_options[k]
        elif k in server_spec and k not in server_options:
            server_options[k] = server_spec[k]
        if k in client_options and k not in client_spec:
            client_spec[k] = client_options[k]
        elif k in client_spec and k not in client_options:
            client_options[k] = client_spec[k]

    server_spec["server_options"] = server_options
    client_spec["client_options"] = client_options
    server_spec["keepalive_period"] = server_options["keepalive_period"]
    server_spec["heartbeat"] = server_options["heartbeat"]
    client_spec["keepalive_period"] = client_options["keepalive_period"]
    client_spec["heartbeat"] = client_options["heartbeat"]

    transport_lower = transport.lower()
    host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
    if transport_lower in ("wss", "wssmux") or use_tls:
        client_spec["remote_addr"] = f"wss://{host_part}:{control_port}"
    elif transport_lower in ("ws", "wsmux"):
        client_spec["remote_addr"] = f"ws://{host_part}:{control_port}"
    else:
        client_spec["remote_addr"] = f"{host_part}:{control_port}"

    client_spec["control_port"] = control_port
    client_spec["transport"] = transport
    client_spec["type"] = transport
    client_spec["tunnel_type"] = tunnel_type
    client_spec["ports"] = ports
    client_spec["token"] = token

    if getattr(tunnel, "spec", None) is not None:
        tunnel.spec["remote_addr"] = client_spec["remote_addr"]
        tunnel.spec["control_port"] = control_port

    return server_spec, client_spec


def build_chisel_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server and client specs for Chisel core with WSS, UDP, and Anti-DPI Camouflage.
    Returns (iran_spec, foreign_spec):
    - Reverse tunnel (is_reverse=True): Iran is server (reverse_only=True), Foreign is client.
    - Direct tunnel (is_reverse=False): Foreign is server (reverse_only=False), Iran is client.
    """
    spec = tunnel.spec.copy() if (hasattr(tunnel, "spec") and tunnel.spec) else {}

    # 0. Directionality (Reverse vs Direct)
    is_reverse = _resolve_tunnel_directionality(tunnel, spec)

    # 1. Transport & TLS Resolution
    transport = (
        getattr(tunnel, "transport_type", None)
        or spec.get("transport")
        or spec.get("transport_type")
        or "ws"
    ).lower()
    use_tls = (transport in ("wss", "https", "tls")) or bool(
        spec.get("websocket_tls") or spec.get("tls")
    )
    effective_transport = "wss" if use_tls else "ws"

    # 2. Tunnel Type (tcp, udp, tcp+udp, all, socks5)
    tunnel_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or spec.get("type")
        or spec.get("tunnel_type")
        or "tcp"
    ).lower()
    if tunnel_type not in ["tcp", "udp", "tcp+udp", "all", "socks5"]:
        tunnel_type = "tcp"

    # 3. Port parsing, Port Ranges, and Control Port allocation
    ports = parse_ports_list(spec)
    port_ranges = getattr(tunnel, "port_ranges", None) or spec.get("port_ranges")
    if port_ranges:
        ranges_ports = parse_ports_list(port_ranges)
        for rp in ranges_ports:
            if rp not in ports:
                ports.append(rp)
    if not ports:
        listen_port = spec.get("listen_port") or spec.get("remote_port")
        if listen_port and str(listen_port).isdigit():
            ports = [int(listen_port)]
        else:
            ports = [8080]

    tunnel_id_str = str(getattr(tunnel, "id", "") or "default-chisel")
    port_hash = int(hashlib.sha256(tunnel_id_str.encode()).hexdigest()[:8], 16)
    first_port = ports[0] if ports else 8080
    raw_control_port = spec.get("control_port")
    try:
        ctrl_p = int(raw_control_port) if raw_control_port else 0
    except (ValueError, TypeError):
        ctrl_p = 0
    if ctrl_p < 1024 or ctrl_p > 65535 or ctrl_p in ports:
        server_control_port = 25000 + (port_hash % 25000)
        while server_control_port in ports:
            server_control_port += 1
    else:
        server_control_port = ctrl_p

    # 4. Authentication (Normalized to user:pass for Chisel binary compatibility)
    auth = spec.get("auth") or spec.get("token") or spec.get("auth_token")
    if not auth:
        auth = generate_token() if generate_token else "default-auth"
    if ":" not in auth:
        auth = f"smite:{auth}"

    # 5. Anti-DPI & Stealth (Custom SNI, Host Header, Decoy Backend)
    custom_sni = resolve_effective_sni(tunnel, spec)
    if hasattr(tunnel, "custom_host"):
        custom_host = tunnel.custom_host.strip() if tunnel.custom_host else None
    else:
        custom_host = spec.get("custom_host") or spec.get("hostname")
    raw_backend = (
        getattr(tunnel, "backend_url", None)
        or spec.get("backend_url")
        or spec.get("backend")
        or spec.get("decoy_url")
    )
    backend_url = raw_backend if is_safe_backend_url(raw_backend) else None
    user_agent = getattr(tunnel, "user_agent", None) or spec.get("user_agent")
    custom_headers = getattr(tunnel, "custom_headers", None) or spec.get("custom_headers")

    # 6. Stability Tuning (Keepalive, Max Retry Interval, Proxy, Key)
    gaming_profile = _has_gaming_profile(tunnel, spec)
    keepalive = getattr(tunnel, "keepalive", None) or spec.get("keepalive") or ("10s" if gaming_profile else "15s")
    # Reconnect backoff cap: after a network blip the client is back in <=3s instead of <=10s
    max_retry_interval = spec.get("max_retry_interval") or ("3s" if gaming_profile else "10s")
    max_retry_count = spec.get("max_retry_count")
    proxy = spec.get("proxy") or spec.get("upstream_proxy")
    key = spec.get("key")
    fingerprint = spec.get("fingerprint")

    # 7. TLS Certificate Generation with SAN
    tls_cert_pem = spec.get("tls_cert_pem")
    tls_key_pem = spec.get("tls_key_pem")
    server_target_ip = iran_node_ip if is_reverse else foreign_node_ip
    if use_tls:
        expected_dns = [custom_sni] if custom_sni else []
        if not (tls_cert_pem and tls_key_pem) or not _cert_covers_identities(
            tls_cert_pem, expected_dns, [server_target_ip]
        ):
            tls_cert_pem = None
            tls_key_pem = None
    if use_tls and not (tls_cert_pem and tls_key_pem):
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            import datetime
            import ipaddress

            key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cn_val = custom_sni or server_target_ip or "chisel-tunnel"
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, cn_val),
            ])

            san_list = []
            if custom_sni:
                san_list.append(x509.DNSName(custom_sni))
            if server_target_ip:
                try:
                    san_list.append(x509.IPAddress(ipaddress.ip_address(server_target_ip)))
                except ValueError:
                    san_list.append(x509.DNSName(server_target_ip))
            san_list.append(x509.DNSName("chisel-tunnel"))
            san_list.append(x509.DNSName("localhost"))
            try:
                san_list.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
            except Exception:
                pass

            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key_obj.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
                .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
                .sign(key_obj, hashes.SHA256())
            )

            tls_key_pem = key_obj.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ).decode('utf-8')
            tls_cert_pem = cert.public_bytes(
                encoding=serialization.Encoding.PEM
            ).decode('utf-8')
        except Exception as e:
            logger.warning(f"Could not generate in-memory cert with SAN for chisel: {e}")

    if use_tls and not (tls_cert_pem and tls_key_pem):
        logger.warning(
            f"Chisel WSS tunnel {getattr(tunnel, 'id', 'unknown')} missing in-memory certificate; "
            "allowing node-side generation"
        )

    # Build server spec
    server_spec_dict = spec.copy()
    server_spec_dict["mode"] = "server"
    server_spec_dict["server_port"] = server_control_port
    server_spec_dict["control_port"] = server_control_port
    server_spec_dict["reverse_port"] = first_port
    server_spec_dict["ports"] = ports
    if port_ranges:
        server_spec_dict["port_ranges"] = port_ranges
    server_spec_dict["transport"] = effective_transport
    server_spec_dict["transport_type"] = effective_transport
    server_spec_dict["websocket_tls"] = use_tls
    server_spec_dict["type"] = tunnel_type
    server_spec_dict["tunnel_type"] = tunnel_type
    server_spec_dict["auth"] = auth
    server_spec_dict["auth_token"] = auth
    server_spec_dict["token"] = auth
    server_spec_dict["reverse_only"] = is_reverse
    server_spec_dict["is_reverse"] = is_reverse
    server_spec_dict["force_direct"] = not is_reverse
    server_spec_dict["keepalive"] = keepalive
    if key:
        server_spec_dict["key"] = key
    if backend_url:
        server_spec_dict["backend_url"] = backend_url
    else:
        server_spec_dict.pop("backend_url", None)
        server_spec_dict.pop("backend", None)
        server_spec_dict.pop("decoy_url", None)
    if custom_sni:
        server_spec_dict["custom_sni"] = custom_sni
    else:
        server_spec_dict.pop("custom_sni", None)
        server_spec_dict.pop("stealth_domain", None)
    if custom_host:
        server_spec_dict["custom_host"] = custom_host
    else:
        server_spec_dict.pop("custom_host", None)
    if use_tls and tls_cert_pem and tls_key_pem:
        server_spec_dict["tls_cert_pem"] = tls_cert_pem
        server_spec_dict["tls_key_pem"] = tls_key_pem
    server_spec_dict["credentials_in_env"] = True
    server_spec_dict["use_authfile"] = True

    # Build client spec
    client_spec_dict = spec.copy()
    client_spec_dict["mode"] = "client"
    client_spec_dict["credentials_in_env"] = True
    client_spec_dict["server_port"] = server_control_port
    client_spec_dict["control_port"] = server_control_port
    client_spec_dict["reverse_port"] = first_port
    client_spec_dict["ports"] = ports
    if port_ranges:
        client_spec_dict["port_ranges"] = port_ranges
    client_spec_dict["transport"] = effective_transport
    client_spec_dict["transport_type"] = effective_transport
    client_spec_dict["websocket_tls"] = use_tls
    client_spec_dict["type"] = tunnel_type
    client_spec_dict["tunnel_type"] = tunnel_type
    client_spec_dict["auth"] = auth
    client_spec_dict["auth_token"] = auth
    client_spec_dict["token"] = auth
    client_spec_dict["is_reverse"] = is_reverse
    client_spec_dict["force_direct"] = not is_reverse
    client_spec_dict["keepalive"] = keepalive
    client_spec_dict["max_retry_interval"] = max_retry_interval
    if max_retry_count is not None:
        client_spec_dict["max_retry_count"] = max_retry_count
    if key:
        client_spec_dict["key"] = key
    if proxy:
        client_spec_dict["proxy"] = proxy
    if fingerprint:
        client_spec_dict["fingerprint"] = fingerprint
    if custom_sni:
        client_spec_dict["custom_sni"] = custom_sni
    else:
        client_spec_dict.pop("custom_sni", None)
        client_spec_dict.pop("stealth_domain", None)
    if custom_host:
        client_spec_dict["custom_host"] = custom_host
    else:
        client_spec_dict.pop("custom_host", None)
    if user_agent:
        client_spec_dict["user_agent"] = user_agent
    if custom_headers:
        client_spec_dict["custom_headers"] = custom_headers
    if use_tls:
        explicit_skip_verify = spec.get("tls_skip_verify")
        # Verify the generated cert unless the operator explicitly opted out.
        client_spec_dict["tls_skip_verify"] = (
            bool(explicit_skip_verify) if explicit_skip_verify is not None else False
        )
        if tls_cert_pem:
            client_spec_dict["tls_ca_cert_pem"] = tls_cert_pem

    # Resolve server_url for client
    target_ip = iran_node_ip if is_reverse else foreign_node_ip
    host_part = f"[{target_ip}]" if is_valid_ipv6(target_ip) else target_ip
    proto = "https://" if use_tls else "http://"
    client_spec_dict["server_url"] = f"{proto}{host_part}:{server_control_port}"

    # 8. Sync state back to tunnel.spec for persistent DB storage
    if hasattr(tunnel, "spec") and tunnel.spec is not None and isinstance(tunnel.spec, dict):
        tunnel.spec["auth"] = auth
        tunnel.spec["auth_token"] = auth
        tunnel.spec["token"] = auth
        tunnel.spec["control_port"] = server_control_port
        tunnel.spec["ports"] = ports
        if port_ranges:
            tunnel.spec["port_ranges"] = port_ranges
        tunnel.spec["transport"] = effective_transport
        tunnel.spec["transport_type"] = effective_transport
        tunnel.spec["type"] = tunnel_type
        tunnel.spec["tunnel_type"] = tunnel_type
        tunnel.spec["is_reverse"] = is_reverse
        tunnel.spec["force_direct"] = not is_reverse
        if custom_sni:
            tunnel.spec["custom_sni"] = custom_sni
        else:
            tunnel.spec.pop("custom_sni", None)
            tunnel.spec.pop("stealth_domain", None)
        if custom_host:
            tunnel.spec["custom_host"] = custom_host
        else:
            tunnel.spec.pop("custom_host", None)
        if backend_url:
            tunnel.spec["backend_url"] = backend_url
        else:
            tunnel.spec.pop("backend_url", None)
            tunnel.spec.pop("backend", None)
            tunnel.spec.pop("decoy_url", None)
        if keepalive:
            tunnel.spec["keepalive"] = keepalive
        if use_tls and tls_cert_pem and tls_key_pem:
            tunnel.spec["tls_cert_pem"] = tls_cert_pem
            tunnel.spec["tls_key_pem"] = tls_key_pem

    if hasattr(tunnel, "is_reverse"):
        try:
            tunnel.is_reverse = is_reverse
        except Exception:
            pass

    # Return (iran_spec, foreign_spec)
    if is_reverse:
        return server_spec_dict, client_spec_dict
    else:
        return client_spec_dict, server_spec_dict


def build_frp_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for FRP core with WSS, QUIC, Strict Auth, Health Checks, Bandwidth Limits, and VHost routing"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    # 0. Directionality (Reverse vs Direct)
    is_reverse = _resolve_tunnel_directionality(tunnel, spec)

    # 1. Service / Proxy Type (tcp, udp, tcp+udp, http, https)
    tunnel_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or server_spec.get("tunnel_type")
        or server_spec.get("type")
        or "tcp"
    ).lower()
    if tunnel_type not in ["tcp", "udp", "tcp+udp", "http", "https"]:
        tunnel_type = "tcp"

    # 2. Ports and Proxy List parsing (extract first so control port avoids collision)
    port_ranges = getattr(tunnel, "port_ranges", None) or server_spec.get("port_ranges")
    raw_ports = server_spec.get("ports")
    if raw_ports and isinstance(raw_ports, list) and len(raw_ports) > 0:
        ports = list(raw_ports)
    else:
        ports = parse_ports_list(server_spec)
        if port_ranges:
            ranges_ports = parse_ports_list(port_ranges)
            for rp in ranges_ports:
                if rp not in ports:
                    ports.append(rp)
        if not ports:
            local_port = server_spec.get("local_port")
            remote_port = server_spec.get("remote_port") or server_spec.get("listen_port")
            if remote_port and local_port:
                ports = [int(local_port)]
            elif remote_port:
                ports = [int(remote_port)]
            elif local_port:
                ports = [int(local_port)]
            else:
                ports = [80] if tunnel_type == "http" else ([443] if tunnel_type == "https" else [8080])

    service_port_ints = set()
    first_service_port = None
    for p in ports:
        if isinstance(p, int):
            service_port_ints.add(p)
            if first_service_port is None:
                first_service_port = p
        elif isinstance(p, str) and p.isdigit():
            service_port_ints.add(int(p))
            if first_service_port is None:
                first_service_port = int(p)
        elif isinstance(p, dict):
            for k in ("remote", "remote_port", "local", "local_port", "port"):
                v = p.get(k)
                if v and str(v).isdigit():
                    service_port_ints.add(int(v))
                    if first_service_port is None:
                        first_service_port = int(v)

    # 3. Control / Bind Port allocation (Strictly distinct from service ports)
    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    raw_bind = server_spec.get("bind_port") or server_spec.get("control_port")
    try:
        bind_p = int(raw_bind) if raw_bind else 0
    except (ValueError, TypeError):
        bind_p = 0
    if bind_p < 1024 or bind_p > 65535 or bind_p in service_port_ints:
        bind_port = 7000 + (port_hash % 1000)
        while bind_port in service_port_ints:
            bind_port += 1
    else:
        bind_port = bind_p

    # VHost external port allocation for HTTP / HTTPS (must not collide with bind_port)
    vhost_http_port = server_spec.get("vhost_http_port") or (first_service_port if first_service_port and first_service_port != bind_port else (bind_port + 1 if bind_port == 80 else 80))
    vhost_https_port = server_spec.get("vhost_https_port") or (first_service_port if first_service_port and first_service_port != bind_port else (bind_port + 1 if bind_port == 443 else 443))

    # 4. Authentication (Token & Stricter Scopes)
    token = server_spec.get("token") or server_spec.get("auth_token")
    if not token:
        token = generate_token() if generate_token else "default-token"
    server_spec["token"] = token
    client_spec["token"] = token

    strict_auth = server_spec.get("strict_auth", True)
    if strict_auth:
        scopes = server_spec.get("auth_additional_scopes") or ["HeartBeats", "NewWorkConns"]
        server_spec["auth_additional_scopes"] = scopes
        client_spec["auth_additional_scopes"] = scopes

    # 5. Transport Protocol Resolution (tcp, kcp, quic, websocket)
    raw_transport = (
        getattr(tunnel, "transport_type", None)
        or server_spec.get("transport_type")
        or server_spec.get("transport")
        or server_spec.get("protocol")
        or "tcp"
    ).lower()

    if raw_transport in ["websocket", "ws"]:
        # Plain WebSocket (ws://) unless the security selector enables TLS.
        transport_proto = "websocket"
    elif raw_transport in ["wss", "https"]:
        # Keep WSS as its own protocol: FRP applies TLS before the WebSocket
        # handshake, which is a different stack from websocket + tls.
        transport_proto = "wss"
    elif raw_transport == "quic":
        transport_proto = "quic"
    elif raw_transport == "kcp":
        transport_proto = "kcp"
    else:
        transport_proto = "tcp"

    explicit_security = (getattr(tunnel, "security_type", None) or server_spec.get("security_type") or "").lower()
    if raw_transport in ["wss", "https"]:
        use_tls = True
        security_type = explicit_security or "tls"
    elif raw_transport in ["ws", "websocket"]:
        use_tls = (explicit_security in ["tls", "force_tls"]) or bool(server_spec.get("tls_enable", False))
        security_type = "tls" if use_tls else "none"
    elif transport_proto == "quic":
        use_tls = True
        security_type = "tls"
    elif transport_proto == "kcp":
        # FRP KCP operates over UDP and cannot be wrapped in TLS.
        use_tls = False
        security_type = "none"
        if explicit_security in ["tls", "force_tls"]:
            logger.warning(
                f"FRP tunnel {getattr(tunnel, 'id', '')}: KCP transport operates over UDP and cannot be wrapped in TLS; falling back to security_type='none'"
            )
    else: # tcp
        if explicit_security == "none":
            use_tls = False
            security_type = "none"
        else:
            use_tls = (explicit_security in ["tls", "force_tls"]) or bool(server_spec.get("tls_enable", True))
            security_type = explicit_security or ("tls" if use_tls else "none")

    custom_sni = resolve_effective_sni(tunnel, server_spec)

    # In-memory self-signed TLS certificates for WSS / TLS server
    tls_cert_pem = server_spec.get("tls_cert_pem")
    tls_key_pem = server_spec.get("tls_key_pem")
    if use_tls:
        server_identity_ip = iran_node_ip if is_reverse else foreign_node_ip
        dns_candidates = [
            d for d in [
                custom_sni,
                getattr(tunnel, "custom_sni", None),
                getattr(tunnel, "stealth_domain", None),
                server_spec.get("custom_sni"),
                server_spec.get("stealth_domain"),
                server_spec.get("sni"),
            ]
            if d and str(d).strip()
        ]
        expected_dns = list(dict.fromkeys([str(d).strip() for d in dns_candidates]))
        if not (tls_cert_pem and tls_key_pem) or not _cert_covers_identities(
            tls_cert_pem, expected_dns, [server_identity_ip]
        ):
            # Stored cert predates a SNI/endpoint change; regenerate so the client
            # can verify the server against the CA we hand it in the same pass.
            tls_cert_pem = None
            tls_key_pem = None
        if not (tls_cert_pem and tls_key_pem):
            try:
                import ipaddress
                import datetime
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa

                target_server_ip = iran_node_ip if is_reverse else foreign_node_ip
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                cn = custom_sni or target_server_ip or "frp-tunnel"
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, cn),
                ])
                builder = x509.CertificateBuilder().subject_name(
                    subject
                ).issuer_name(
                    issuer
                ).public_key(
                    key.public_key()
                ).serial_number(
                    x509.random_serial_number()
                ).not_valid_before(
                    datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
                ).not_valid_after(
                    datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650)
                )

                san_items = []
                for d in expected_dns:
                    try:
                        san_items.append(x509.IPAddress(ipaddress.ip_address(d)))
                    except ValueError:
                        san_items.append(x509.DNSName(d))
                for s_ip in [target_server_ip, iran_node_ip, foreign_node_ip, "127.0.0.1", "::1", "localhost"]:
                    if s_ip:
                        try:
                            ip_obj = x509.IPAddress(ipaddress.ip_address(s_ip))
                            if ip_obj not in san_items:
                                san_items.append(ip_obj)
                        except ValueError:
                            dns_obj = x509.DNSName(s_ip)
                            if dns_obj not in san_items:
                                san_items.append(dns_obj)
                if not san_items:
                    san_items.append(x509.DNSName("frp-tunnel"))
                builder = builder.add_extension(x509.SubjectAlternativeName(san_items), critical=False)
                cert = builder.sign(key, hashes.SHA256())

                tls_key_pem = key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode('utf-8')
                tls_cert_pem = cert.public_bytes(
                    encoding=serialization.Encoding.PEM
                ).decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not generate in-memory cert for FRP: {e}")

        if tls_cert_pem and tls_key_pem:
            server_spec["tls_cert_pem"] = tls_cert_pem
            server_spec["tls_key_pem"] = tls_key_pem
            if tunnel.spec is not None:
                tunnel.spec["tls_cert_pem"] = tls_cert_pem
                tunnel.spec["tls_key_pem"] = tls_key_pem

    # 6. Reliability, Health Checks & Bandwidth Shaping
    # Avoid double encryption / compression overhead if transport is already TLS/QUIC/WSS
    is_transport_encrypted = use_tls or transport_proto in ["quic", "wss"]
    default_encryption = False if is_transport_encrypted else True
    default_compression = False if (tunnel_type in ["udp", "tcp+udp"] or getattr(tunnel, "gaming_mode", False) or is_transport_encrypted) else True
    use_encryption = bool(server_spec.get("use_encryption", default_encryption))
    use_compression = bool(server_spec.get("use_compression", default_compression))

    # Internal health check configuration
    # FRP ONLY supports 'tcp' and 'http' health checks. It has NO UDP health check.
    # Health checks must NOT be enabled by default to prevent premature unregistering of proxies
    # before target services (e.g. Xray/V2Ray) are active.
    if tunnel_type == "udp":
        enable_health_check = False
        health_check_type = None
    else:
        enable_health_check = bool(
            server_spec.get("enable_health_check", False)
            or server_spec.get("health_check_type")
        )
        health_check_type = server_spec.get("health_check_type") or ("tcp" if enable_health_check else None)
    health_check_interval = int(server_spec.get("health_check_interval_s") or 10)
    health_check_timeout = int(server_spec.get("health_check_timeout_s") or 3)
    health_check_max_failed = int(server_spec.get("health_check_max_failed") or 3)
    health_check_path = str(server_spec.get("health_check_path") or "/").strip()
    if not health_check_path.startswith("/"):
        health_check_path = f"/{health_check_path}"
    health_check_path = health_check_path.replace('"', '').replace('\n', '').replace('\r', '')

    # Bandwidth limit per proxy: normalize units (FRP schema only accepts KB and MB)
    if hasattr(tunnel, "rate_limit_mbps"):
        rate_limit_mbps = tunnel.rate_limit_mbps
    else:
        rate_limit_mbps = server_spec.get("rate_limit_mbps")
    if rate_limit_mbps is not None:
        try:
            rate_limit_mbps = float(rate_limit_mbps)
            if rate_limit_mbps <= 0:
                rate_limit_mbps = None
        except (ValueError, TypeError):
            rate_limit_mbps = None

    if hasattr(tunnel, "rate_limit_mbps") and rate_limit_mbps is None:
        raw_bw = None
    else:
        raw_bw = server_spec.get("bandwidth_limit")
    if not raw_bw and rate_limit_mbps and float(rate_limit_mbps) > 0:
        # Mbps (megabits) -> FRP bytes/sec units: 1 Mbps = 125 KB/s
        raw_bw = f"{max(1, int(float(rate_limit_mbps) * 125))}KB"

    bandwidth_limit = None
    if raw_bw:
        clean_bw = str(raw_bw).strip().upper().replace(" ", "")
        if clean_bw.isdigit():
            clean_bw = f"{clean_bw}MB"
        import re
        m = re.match(r'^(\d+)(KB|MB|GB|B)$', clean_bw)
        if m:
            val_num, unit = int(m.group(1)), m.group(2)
            if unit == "GB":
                bandwidth_limit = f"{val_num * 1024}MB"
            elif unit == "B":
                bandwidth_limit = f"{max(1, val_num // 1024)}KB"
            else:
                bandwidth_limit = f"{val_num}{unit}"
    bandwidth_limit_mode = server_spec.get("bandwidth_limit_mode", "client")
    if bandwidth_limit_mode not in ["client", "server"]:
        bandwidth_limit_mode = "client"

    # Proxy protocol version (v1, v2, none)
    proxy_protocol_version = server_spec.get("proxy_protocol_version") or getattr(tunnel, "proxy_protocol_version", None)
    if proxy_protocol_version not in ["v1", "v2"]:
        proxy_protocol_version = None

    # Custom domains for HTTP/HTTPS vhost routing
    custom_domains = server_spec.get("custom_domains") or []
    if isinstance(custom_domains, str):
        custom_domains = [d.strip() for d in custom_domains.replace(",", "\n").split("\n") if d.strip()]
    if not custom_domains and (getattr(tunnel, "custom_host", None) or server_spec.get("custom_host")):
        custom_domains = [getattr(tunnel, "custom_host", None) or server_spec.get("custom_host")]

    # Target host resolution
    target_host = getattr(tunnel, "target_host", None) or server_spec.get("target_host") or server_spec.get("local_ip") or "127.0.0.1"

    # Deterministic secretKey for Visitor / STCP / SUDP authentication
    tunnel_id_str = str(getattr(tunnel, "id", "") or "default-frp")
    secret_key = hashlib.sha256(f"{tunnel_id_str}:{token}".encode()).hexdigest()[:24]

    # 7. Assembling Server & Client Specs
    server_spec["bind_port"] = bind_port
    server_spec["control_port"] = bind_port
    server_spec["vhost_http_port"] = vhost_http_port
    server_spec["vhost_https_port"] = vhost_https_port
    server_spec["token"] = token
    server_spec["secret_key"] = secret_key
    server_spec["transport_type"] = transport_proto
    server_spec["transport"] = transport_proto
    server_spec["security_type"] = security_type
    server_spec["tls_enable"] = use_tls
    server_spec["tunnel_type"] = tunnel_type
    server_spec["type"] = tunnel_type
    server_spec["ports"] = ports
    server_spec["use_encryption"] = use_encryption
    server_spec["use_compression"] = use_compression
    server_spec["local_ip"] = target_host
    server_spec["target_host"] = target_host
    server_spec["is_reverse"] = is_reverse
    server_spec["force_direct"] = not is_reverse

    client_spec["server_addr"] = iran_node_ip if is_reverse else foreign_node_ip
    client_spec["server_port"] = bind_port
    client_spec["control_port"] = bind_port
    client_spec["token"] = token
    client_spec["secret_key"] = secret_key
    client_spec["transport_type"] = transport_proto
    client_spec["transport"] = transport_proto
    client_spec["security_type"] = security_type
    client_spec["tls_enable"] = use_tls
    if custom_sni:
        client_spec["custom_sni"] = custom_sni
        server_spec["custom_sni"] = custom_sni
        client_spec["stealth_domain"] = custom_sni
        server_spec["stealth_domain"] = custom_sni
        if tunnel.spec is not None and isinstance(tunnel.spec, dict):
            tunnel.spec["custom_sni"] = custom_sni
            tunnel.spec["stealth_domain"] = custom_sni
    else:
        client_spec.pop("custom_sni", None)
        client_spec.pop("stealth_domain", None)
        server_spec.pop("custom_sni", None)
        server_spec.pop("stealth_domain", None)
    client_spec["use_encryption"] = use_encryption
    client_spec["use_compression"] = use_compression
    client_spec["tunnel_type"] = tunnel_type
    client_spec["type"] = tunnel_type
    client_spec["local_ip"] = target_host
    client_spec["target_host"] = target_host
    client_spec["ports"] = ports
    client_spec["is_reverse"] = is_reverse
    client_spec["force_direct"] = not is_reverse

    if use_tls and tls_cert_pem:
        # Pin the client to the exact self-signed server cert; FRP otherwise
        # falls back to InsecureSkipVerify when no CA file is supplied.
        client_spec["tls_ca_cert_pem"] = tls_cert_pem
    else:
        client_spec.pop("tls_ca_cert_pem", None)

    if health_check_type:
        client_spec["health_check_type"] = health_check_type
        client_spec["health_check_interval_s"] = health_check_interval
        client_spec["health_check_timeout_s"] = health_check_timeout
        client_spec["health_check_max_failed"] = health_check_max_failed
        if health_check_type == "http":
            client_spec["health_check_path"] = health_check_path
    else:
        client_spec.pop("health_check_type", None)
        client_spec.pop("health_check_interval_s", None)
        client_spec.pop("health_check_timeout_s", None)
        client_spec.pop("health_check_max_failed", None)
        client_spec.pop("health_check_path", None)

    if bandwidth_limit:
        client_spec["bandwidth_limit"] = bandwidth_limit
        client_spec["bandwidth_limit_mode"] = bandwidth_limit_mode
    else:
        client_spec.pop("bandwidth_limit", None)
        client_spec.pop("bandwidth_limit_mode", None)
        client_spec.pop("rate_limit_mbps", None)
        server_spec.pop("bandwidth_limit", None)
        server_spec.pop("bandwidth_limit_mode", None)
        server_spec.pop("rate_limit_mbps", None)

    if proxy_protocol_version and tunnel_type in ["tcp", "http", "https"]:
        client_spec["proxy_protocol_version"] = proxy_protocol_version
    else:
        client_spec.pop("proxy_protocol_version", None)

    if custom_domains:
        client_spec["custom_domains"] = custom_domains
        server_spec["custom_domains"] = custom_domains

    # 8. Sync state back into tunnel.spec for persistent DB storage
    if tunnel.spec is not None:
        tunnel.spec["is_reverse"] = is_reverse
        tunnel.spec["force_direct"] = not is_reverse
        tunnel.spec["secret_key"] = secret_key
        tunnel.spec["token"] = token
        tunnel.spec["bind_port"] = bind_port
        tunnel.spec["control_port"] = bind_port
        tunnel.spec["vhost_http_port"] = vhost_http_port
        tunnel.spec["vhost_https_port"] = vhost_https_port
        tunnel.spec["transport_type"] = transport_proto
        tunnel.spec["transport"] = transport_proto
        tunnel.spec["security_type"] = security_type
        tunnel.spec["tls_enable"] = use_tls
        tunnel.spec["tunnel_type"] = tunnel_type
        tunnel.spec["type"] = tunnel_type
        tunnel.spec["ports"] = ports
        tunnel.spec["use_encryption"] = use_encryption
        tunnel.spec["use_compression"] = use_compression
        if custom_sni:
            tunnel.spec["custom_sni"] = custom_sni
        if health_check_type:
            tunnel.spec["health_check_type"] = health_check_type
            if health_check_type == "http":
                tunnel.spec["health_check_path"] = health_check_path
        else:
            tunnel.spec.pop("health_check_type", None)
            tunnel.spec.pop("health_check_path", None)
        if bandwidth_limit:
            tunnel.spec["bandwidth_limit"] = bandwidth_limit
        else:
            tunnel.spec.pop("bandwidth_limit", None)
            tunnel.spec.pop("bandwidth_limit_mode", None)
            tunnel.spec.pop("rate_limit_mbps", None)
        if proxy_protocol_version and tunnel_type in ["tcp", "http", "https"]:
            tunnel.spec["proxy_protocol_version"] = proxy_protocol_version
        else:
            tunnel.spec.pop("proxy_protocol_version", None)
        if custom_domains:
            tunnel.spec["custom_domains"] = custom_domains

    # 9. Return (iran_spec, foreign_spec)
    if hasattr(tunnel, "is_reverse"):
        try:
            tunnel.is_reverse = is_reverse
        except Exception:
            pass

    if port_ranges:
        server_spec["port_ranges"] = port_ranges
        client_spec["port_ranges"] = port_ranges
        if tunnel.spec is not None:
            tunnel.spec["port_ranges"] = port_ranges

    if is_reverse:
        # Reverse Mode: Iran is Server (frps), Foreign is Client (frpc)
        server_spec["mode"] = "server"
        server_spec["is_reverse"] = True
        server_spec["force_direct"] = False
        client_spec["mode"] = "client"
        client_spec["is_reverse"] = True
        client_spec["force_direct"] = False
        client_spec["server_addr"] = iran_node_ip
        return server_spec, client_spec
    else:
        # Direct Mode: Foreign is Server & Provider (frps + frpc_provider), Iran is Client Visitor (frpc visitor)
        foreign_spec = server_spec.copy()
        foreign_spec["mode"] = "server"
        foreign_spec["is_provider"] = True
        foreign_spec["is_reverse"] = False
        foreign_spec["force_direct"] = True
        foreign_spec["secret_key"] = secret_key
        foreign_spec["bind_port"] = bind_port
        foreign_spec["control_port"] = bind_port
        foreign_spec["token"] = token
        foreign_spec["ports"] = ports
        if port_ranges:
            foreign_spec["port_ranges"] = port_ranges
        foreign_spec["local_ip"] = target_host
        foreign_spec["target_host"] = target_host
        if health_check_type:
            foreign_spec["health_check_type"] = health_check_type
            foreign_spec["health_check_interval_s"] = health_check_interval
            foreign_spec["health_check_timeout_s"] = health_check_timeout
            foreign_spec["health_check_max_failed"] = health_check_max_failed
            if health_check_type == "http":
                foreign_spec["health_check_path"] = health_check_path

        iran_spec = client_spec.copy()
        iran_spec["mode"] = "client"
        iran_spec["is_visitor"] = True
        iran_spec["is_reverse"] = False
        iran_spec["force_direct"] = True
        iran_spec["server_addr"] = foreign_node_ip
        iran_spec["server_port"] = bind_port
        iran_spec["control_port"] = bind_port
        iran_spec["token"] = token
        iran_spec["secret_key"] = secret_key
        iran_spec["ports"] = ports
        if port_ranges:
            iran_spec["port_ranges"] = port_ranges
        iran_spec["bind_addr"] = "0.0.0.0"
        # FRP Visitors do not support healthCheck block
        iran_spec.pop("health_check_type", None)
        iran_spec.pop("health_check_interval_s", None)
        iran_spec.pop("health_check_timeout_s", None)
        iran_spec.pop("health_check_max_failed", None)
        iran_spec.pop("health_check_path", None)

        return iran_spec, foreign_spec


def build_gost_node_specs(
    tunnel,
    iran_node_ip: str,
    foreign_node_ip: str,
    control_port: Optional[int] = None,
    auth_token: Optional[str] = None,
    ports: Optional[List[Any]] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Build server_spec (for iran node) and client_spec (for foreign node) for a GOST tunnel.
    Propagates all spec fields symmetrically and assigns admission control (allowed_ips) to the server node.
    """
    spec = tunnel.spec.copy() if (hasattr(tunnel, "spec") and tunnel.spec) else {}
    if control_port is None:
        raw_port = spec.get("control_port")
        try:
            parsed_port = int(raw_port) if raw_port is not None else None
        except (ValueError, TypeError):
            parsed_port = None

        # If no control_port or legacy 44300 default, allocate a unique deterministic port
        if not parsed_port or parsed_port == 44300 or parsed_port < 1024:
            tunnel_id_str = str(getattr(tunnel, "id", "") or "default-gost")
            port_hash = int(hashlib.sha256(tunnel_id_str.encode()).hexdigest()[:8], 16)
            control_port = 25000 + (port_hash % 25000)
            spec["control_port"] = control_port
            if hasattr(tunnel, "spec") and tunnel.spec is not None and isinstance(tunnel.spec, dict):
                tunnel.spec["control_port"] = control_port
        else:
            control_port = parsed_port
    if auth_token is None:
        auth_token = spec.get("auth_token") or spec.get("token")
        if not auth_token:
            import secrets
            auth_token = f"gost-{secrets.token_hex(12)}"
            spec["auth_token"] = auth_token
            if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
                tunnel.spec["auth_token"] = auth_token
    if ports is None:
        parsed_ports = parse_ports_list(spec)
        ports = parsed_ports if parsed_ports else [8080]

    spec["ports"] = ports
    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        tunnel.spec["ports"] = ports

    is_reverse = _resolve_tunnel_directionality(tunnel, spec)

    spec["is_reverse"] = is_reverse
    spec["force_direct"] = not is_reverse
    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        tunnel.spec["is_reverse"] = is_reverse
        tunnel.spec["force_direct"] = not is_reverse
    if hasattr(tunnel, "is_reverse"):
        try:
            tunnel.is_reverse = is_reverse
        except Exception:
            pass

    cdn_mode = getattr(tunnel, "cdn_mode", False) or spec.get("cdn_mode", False) or False
    gaming_mode = getattr(tunnel, "gaming_mode", False) or spec.get("gaming_mode", False) or False
    if hasattr(tunnel, "custom_host"):
        custom_host = tunnel.custom_host.strip() if tunnel.custom_host else None
    else:
        custom_host = spec.get("custom_host")
    if custom_host is None:
        spec.pop("custom_host", None)
        if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
            tunnel.spec.pop("custom_host", None)

    if hasattr(tunnel, "ws_path"):
        ws_path = tunnel.ws_path.strip() if tunnel.ws_path else None
    else:
        ws_path = spec.get("ws_path")
    if ws_path is None:
        spec.pop("ws_path", None)
        if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
            tunnel.spec.pop("ws_path", None)

    effective_sni = resolve_effective_sni(tunnel, spec)
    custom_sni = effective_sni
    stealth_domain = effective_sni
    if effective_sni is None:
        spec.pop("custom_sni", None)
        spec.pop("stealth_domain", None)
    else:
        spec["custom_sni"] = effective_sni
        spec["stealth_domain"] = effective_sni
        if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
            tunnel.spec["custom_sni"] = effective_sni
            tunnel.spec["stealth_domain"] = effective_sni

    if hasattr(tunnel, "rate_limit_mbps"):
        rate_limit_mbps = tunnel.rate_limit_mbps
    else:
        rate_limit_mbps = spec.get("rate_limit_mbps")
    if rate_limit_mbps is not None:
        try:
            rate_limit_mbps = float(rate_limit_mbps)
            if rate_limit_mbps <= 0:
                rate_limit_mbps = None
        except (ValueError, TypeError):
            rate_limit_mbps = None
    if rate_limit_mbps is None:
        spec.pop("rate_limit_mbps", None)
        if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
            tunnel.spec.pop("rate_limit_mbps", None)
    transport_type = getattr(tunnel, "transport_type", None) or spec.get("transport_type") or spec.get("transport") or "tcp"
    security_type = getattr(tunnel, "security_type", None) or spec.get("security_type") or "none"
    failover_ips = getattr(tunnel, "failover_ips", None) or spec.get("failover_ips")
    port_ranges = getattr(tunnel, "port_ranges", None) or spec.get("port_ranges")
    allowed_ips = getattr(tunnel, "allowed_ips", None) or spec.get("allowed_ips")

    raw_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or spec.get("type")
        or spec.get("tunnel_type")
        or "tcp"
    ).lower()

    if raw_type == "grpc":
        tunnel_type = "tcp"
        transport_type = "grpc"
    elif raw_type in ["tcpmux", "mux"]:
        tunnel_type = "tcp"
        spec["multiplex"] = True
    elif raw_type in ["tcp", "udp", "tcp+udp"]:
        tunnel_type = raw_type
    else:
        tunnel_type = "tcp"

    raw_target = (
        spec.get("target_host")
        or getattr(tunnel, "target_host", None)
        or spec.get("remote_ip")
        or getattr(tunnel, "remote_ip", None)
    )
    if raw_target:
        raw_target_str = str(raw_target).strip()
        if foreign_node_ip and raw_target_str == str(foreign_node_ip).strip():
            target_host = "127.0.0.1"
        else:
            target_host = raw_target_str
    else:
        target_host = "127.0.0.1"

    base_spec = {
        "control_port": control_port,
        "auth_token": auth_token,
        "type": tunnel_type,
        "tunnel_type": tunnel_type,
        "transport": transport_type,
        "transport_type": transport_type,
        "security_type": security_type,
        "ports": ports,
        "cdn_mode": cdn_mode,
        "gaming_mode": gaming_mode,
        "custom_host": custom_host,
        "custom_sni": custom_sni,
        "ws_path": ws_path,
        "stealth_domain": stealth_domain,
        "rate_limit_mbps": rate_limit_mbps,
        "failover_ips": failover_ips,
        "port_ranges": port_ranges,
        "is_reverse": is_reverse,
        "force_direct": not is_reverse,
        "foreign_node_ip": foreign_node_ip,
        "target_host": target_host,
        "remote_ip": target_host,
        "multiplex": bool(spec.get("multiplex") or getattr(tunnel, "multiplex", False)),
        "enable_mux": bool(spec.get("multiplex") or spec.get("enable_mux") or getattr(tunnel, "multiplex", False)),
        "utls_fingerprint": getattr(tunnel, "utls_fingerprint", None),
        "custom_headers": getattr(tunnel, "custom_headers", None),
        "obfuscation_type": getattr(tunnel, "obfuscation_type", None),
        "mux_type": getattr(tunnel, "mux_type", None),
        "relay_hops": getattr(tunnel, "relay_hops", None),
        "bypass_ips": getattr(tunnel, "bypass_ips", None),
        "dns_resolvers": getattr(tunnel, "dns_resolvers", None),
        "selector_strategy": getattr(tunnel, "selector_strategy", None) or spec.get("selector_strategy") or "fifo",
        "keepalive_interval": getattr(tunnel, "keepalive_interval", None) or spec.get("keepalive_interval") or 15,
    }
    if _has_gaming_profile(tunnel, spec):
        base_spec["latency_profile"] = GAMING_PROFILE

    # For UDP datagram tunneling over streaming transports, ensure multiplexing is default
    if tunnel_type in ["udp", "tcp+udp"] and transport_type.lower() not in ["mws", "mwss", "udp", "rudp", "kcp", "quic"]:
        base_spec["multiplex"] = True
        if not base_spec.get("mux_type") or str(base_spec.get("mux_type")).lower() == "yamux":
            base_spec["mux_type"] = "smux"

    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        for k in ["utls_fingerprint", "utls_client", "mux_type", "handler_type", "user_agent", "multiplex", "selector_strategy", "strategy", "keepalive_interval", "max_fails", "fail_timeout"]:
            if k in tunnel.spec:
                base_spec[k] = tunnel.spec[k]

    if str(base_spec.get("mux_type") or "").lower() in ("", "yamux"):
        base_spec["mux_type"] = "smux"

    # Normalize allowed_ips to list
    allowed_list: List[str] = []
    if isinstance(allowed_ips, str):
        allowed_list = [x.strip() for x in allowed_ips.replace("\r", "\n").split("\n") if x.strip()]
    elif isinstance(allowed_ips, list):
        allowed_list = [str(x).strip() for x in allowed_ips if str(x).strip()]

    if is_reverse:
        # Reverse Tunnel: Iran Node is GOST Server, Foreign Node is GOST Client
        server_spec = base_spec.copy()
        server_spec["mode"] = "server"

        client_spec = base_spec.copy()
        client_spec["mode"] = "client"
        client_spec["server_ip"] = iran_node_ip

        if allowed_list:
            allowed_ips_server = list(allowed_list)
            if foreign_node_ip and foreign_node_ip not in allowed_ips_server:
                allowed_ips_server.append(foreign_node_ip)
            server_spec["allowed_ips"] = allowed_ips_server
        else:
            server_spec["allowed_ips"] = None
        client_spec["allowed_ips"] = None
    else:
        # Direct Tunnel: Iran Node is GOST Client, Foreign Node is GOST Server
        server_spec = base_spec.copy()
        server_spec["mode"] = "client"
        server_spec["server_ip"] = foreign_node_ip

        client_spec = base_spec.copy()
        client_spec["mode"] = "server"

        if allowed_list:
            allowed_ips_foreign = list(allowed_list)
            if iran_node_ip and iran_node_ip not in allowed_ips_foreign:
                allowed_ips_foreign.append(iran_node_ip)
            client_spec["allowed_ips"] = allowed_ips_foreign
        else:
            client_spec["allowed_ips"] = None
        server_spec["allowed_ips"] = None

    # TLS trust material. The node binary can self-sign, but its client then has
    # no way to authenticate the listener; distribute one cert so the dialer can
    # verify instead of running with secure=false.
    transport_lc = str(transport_type or "").lower()
    security_lc = str(security_type or "").lower()
    grpc_with_tls = transport_lc == "grpc" and (
        security_lc in ("tls", "utls")
        or bool(custom_sni)
        or bool(spec.get("tls_cert"))
        or bool(spec.get("tls_cert_pem"))
    )
    # Carriers where the tunnel server terminates TLS itself. The adapter
    # promotes tcp/ws/mws/mtcp to tls/wss/mwss/mtls when security is TLS, but
    # UDP/KCP/SSH have no listener-side TLS, so a tunnel CA must not be forced
    # on them (their TLS dialer passes through to the target).
    requires_tls = (
        transport_lc in ("wss", "mwss", "tls", "mtls", "quic")
        or (transport_lc in ("tcp", "ws", "mws", "mtcp") and security_lc in ("tls", "utls"))
        or grpc_with_tls
    )
    if requires_tls:
        gost_server_spec = server_spec if is_reverse else client_spec
        gost_client_spec = client_spec if is_reverse else server_spec
        gost_server_ip = gost_client_spec.get("server_ip")
        tls_cert_pem = spec.get("tls_cert_pem") or spec.get("tls_cert")
        tls_key_pem = spec.get("tls_key_pem") or spec.get("tls_key")
        dns_candidates = [
            d for d in [
                effective_sni,
                getattr(tunnel, "custom_sni", None),
                getattr(tunnel, "stealth_domain", None),
                spec.get("custom_sni"),
                spec.get("stealth_domain"),
                spec.get("sni"),
            ]
            if d and str(d).strip()
        ]
        expected_dns = list(dict.fromkeys([str(d).strip() for d in dns_candidates]))
        if not (tls_cert_pem and tls_key_pem) or not _cert_covers_identities(
            tls_cert_pem, expected_dns, [gost_server_ip]
        ):
            tls_cert_pem = None
            tls_key_pem = None
        if not (tls_cert_pem and tls_key_pem):
            try:
                import datetime
                import ipaddress
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa

                key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                cn_val = effective_sni or gost_server_ip or "gost-tunnel"
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, cn_val),
                ])
                san_list = []
                for d in expected_dns:
                    try:
                        san_list.append(x509.IPAddress(ipaddress.ip_address(d)))
                    except ValueError:
                        san_list.append(x509.DNSName(d))
                if gost_server_ip:
                    try:
                        ip_obj = x509.IPAddress(ipaddress.ip_address(gost_server_ip))
                        if ip_obj not in san_list:
                            san_list.append(ip_obj)
                    except ValueError:
                        dns_obj = x509.DNSName(gost_server_ip)
                        if dns_obj not in san_list:
                            san_list.append(dns_obj)
                san_list.append(x509.DNSName("gost-tunnel"))
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(issuer)
                    .public_key(key_obj.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
                    .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
                    .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
                    .sign(key_obj, hashes.SHA256())
                )
                tls_key_pem = key_obj.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode('utf-8')
                tls_cert_pem = cert.public_bytes(encoding=serialization.Encoding.PEM).decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not generate in-memory cert for GOST: {e}")

        if tls_cert_pem and tls_key_pem:
            gost_server_spec["tls_cert_pem"] = tls_cert_pem
            gost_server_spec["tls_key_pem"] = tls_key_pem
            # Nodes from before the *_pem fields only read these legacy names.
            # Sending both keeps a mixed-version fleet on one certificate while
            # it is being rolled forward.
            gost_server_spec["tls_cert"] = tls_cert_pem
            gost_server_spec["tls_key"] = tls_key_pem
            gost_client_spec["tls_ca_cert_pem"] = tls_cert_pem
            if isinstance(tunnel.spec, dict):
                tunnel.spec["tls_cert_pem"] = tls_cert_pem
                tunnel.spec["tls_key_pem"] = tls_key_pem
            if effective_sni:
                gost_server_spec["custom_sni"] = effective_sni
                gost_server_spec["stealth_domain"] = effective_sni
                gost_client_spec["custom_sni"] = effective_sni
                gost_client_spec["stealth_domain"] = effective_sni
                if isinstance(tunnel.spec, dict):
                    tunnel.spec["custom_sni"] = effective_sni
                    tunnel.spec["stealth_domain"] = effective_sni
        else:
            logger.warning(
                f"GOST TLS tunnel {getattr(tunnel, 'id', 'unknown')} missing in-memory certificate; "
                "allowing node-side generation"
            )

    return server_spec, client_spec


def build_tunnel_node_specs(
    tunnel,
    iran_node_ip: str,
    foreign_node_ip: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Master entry point: generate (server_spec, client_spec) for any tunnel core"""
    core = (tunnel.core or "").lower()
    if core == "rathole":
        server_spec, client_spec = build_rathole_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "backhaul":
        server_spec, client_spec = build_backhaul_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "chisel":
        server_spec, client_spec = build_chisel_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "frp":
        server_spec, client_spec = build_frp_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "gost":
        server_spec, client_spec = build_gost_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    else:
        raise ValueError(f"Unsupported tunnel core: {core}")

    tunnel_spec = getattr(tunnel, "spec", None) or {}
    use_ipv6 = bool(
        is_valid_ipv6(iran_node_ip)
        or is_valid_ipv6(foreign_node_ip)
        or _to_bool(tunnel_spec.get("use_ipv6"))
        or _to_bool(getattr(tunnel, "use_ipv6", False))
    )
    if use_ipv6:
        server_spec["use_ipv6"] = True
        client_spec["use_ipv6"] = True
        if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
            tunnel.spec["use_ipv6"] = True

    return server_spec, client_spec



async def persist_generated_spec(session, tunnel, spec_snapshot) -> bool:
    """Persist values the spec builder generated into tunnel.spec (tokens, keys,
    TLS certificates, control ports) and authoritative directionality (is_reverse).

    Without this, background re-applies that never committed would generate fresh
    certificates/tokens on every run, so the nodes saw a "changed" spec and
    restarted the tunnel each cycle. Returns True when something was saved.
    """
    try:
        spec_changed = (tunnel.spec != spec_snapshot) if hasattr(tunnel, "spec") else False
        is_dirty = False
        if session is not None and hasattr(session, "is_modified"):
            try:
                mod_res = session.is_modified(tunnel)
                import inspect
                if inspect.iscoroutine(mod_res):
                    mod_res.close()
                else:
                    is_dirty = bool(mod_res)
            except Exception:
                is_dirty = False

        if not spec_changed and not is_dirty:
            return False

        if hasattr(tunnel, "_sa_instance_state"):
            from sqlalchemy.orm.attributes import flag_modified
            if spec_changed:
                try:
                    flag_modified(tunnel, "spec")
                except Exception:
                    pass
            if hasattr(tunnel, "is_reverse"):
                try:
                    flag_modified(tunnel, "is_reverse")
                except Exception:
                    pass
        await session.commit()
        return True
    except Exception as e:
        logger.warning(f"Could not persist generated spec values for tunnel {getattr(tunnel, 'id', '?')}: {e}")
        try:
            await session.rollback()
        except Exception:
            pass
        return False
