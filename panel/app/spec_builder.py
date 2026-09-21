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
import hashlib
import random
import logging
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger(__name__)


def is_valid_ipv6(addr: str) -> bool:
    """Check if address is IPv6"""
    if not addr:
        return False
    clean = addr.strip("[]")
    return ":" in clean


def parse_ports_list(spec_or_ports: Any) -> List[int]:
    """Extract integer ports from various spec input formats"""
    if isinstance(spec_or_ports, dict):
        raw_ports = spec_or_ports.get("ports") or []
        if not raw_ports:
            single = spec_or_ports.get("listen_port") or spec_or_ports.get("remote_port") or spec_or_ports.get("public_port")
            if single is not None:
                raw_ports = [single]
    elif isinstance(spec_or_ports, (list, tuple)):
        raw_ports = spec_or_ports
    else:
        raw_ports = [spec_or_ports] if spec_or_ports else []

    clean_ports = []
    for p in raw_ports:
        if isinstance(p, dict):
            val = p.get("local") or p.get("remote") or p.get("port")
            if val is not None and str(val).isdigit():
                clean_ports.append(int(val))
        elif isinstance(p, (int, str)) and str(p).isdigit():
            clean_ports.append(int(p))
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
        from app.utils import generate_token
        token = generate_token()
        server_spec["token"] = token
        if tunnel.spec is not None:
            tunnel.spec["token"] = token

    ports = parse_ports_list(spec)
    if not ports:
        proxy_port = server_spec.get("remote_port") or server_spec.get("listen_port")
        if proxy_port and str(proxy_port).isdigit():
            ports = [int(proxy_port)]

    # Control port assignment
    control_port = server_spec.get("control_port")
    if not control_port or int(control_port) < 24000:
        port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
        control_port = 25000 + (port_hash % 25000)
        if tunnel.spec is not None:
            tunnel.spec["control_port"] = control_port

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

        if not (server_priv and server_pub and client_priv and client_pub):
            from app.utils import generate_noise_keypair
            s_priv, s_pub = generate_noise_keypair()
            c_priv, c_pub = generate_noise_keypair()
            server_priv, server_pub = s_priv, s_pub
            client_priv, client_pub = c_priv, c_pub
            if tunnel.spec is not None:
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

    if "websocket_tls" in server_spec:
        server_spec["websocket_tls"] = server_spec["websocket_tls"]
    elif "tls" in server_spec:
        server_spec["websocket_tls"] = server_spec["tls"]

    transport_lower = transport.lower()
    if transport_lower in ("websocket", "ws", "wss"):
        use_tls = bool(server_spec.get("websocket_tls") or server_spec.get("tls") or transport_lower == "wss")
        proto = "wss://" if use_tls else "ws://"
        host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
        client_spec["remote_addr"] = f"{proto}{host_part}:{control_port}"
        client_spec["websocket_tls"] = use_tls
        custom_sni = (
            server_spec.get("custom_sni")
            or server_spec.get("stealth_domain")
            or getattr(tunnel, "custom_sni", None)
            or getattr(tunnel, "stealth_domain", None)
        )
        if custom_sni:
            client_spec["custom_sni"] = custom_sni
            server_spec["custom_sni"] = custom_sni
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

    return server_spec, client_spec


def build_backhaul_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Backhaul core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    transport = (
        server_spec.get("transport")
        or server_spec.get("transport_type")
        or server_spec.get("type")
        or "tcp"
    )
    token = server_spec.get("token")
    if not token:
        from app.utils import generate_token
        token = generate_token()
        server_spec["token"] = token
        if tunnel.spec is not None:
            tunnel.spec["token"] = token

    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    control_port = (
        server_spec.get("control_port")
        or server_spec.get("listen_port")
        or (3080 + (port_hash % 1000))
    )
    target_host = server_spec.get("target_host", "127.0.0.1")

    ports = server_spec.get("ports", [])
    if not ports or len(ports) == 0:
        public_port = server_spec.get("public_port") or server_spec.get("remote_port") or server_spec.get("listen_port")
        target_port = server_spec.get("target_port") or public_port
        if public_port:
            if target_port:
                ports = [f"{public_port}={target_host}:{target_port}"]
            else:
                ports = [str(public_port)]
    else:
        processed_ports = []
        for p in ports:
            if not p:
                continue
            if isinstance(p, str):
                if '=' in p:
                    processed_ports.append(p)
                elif p.isdigit():
                    processed_ports.append(f"{p}={target_host}:{p}")
                else:
                    processed_ports.append(p)
            elif isinstance(p, int):
                processed_ports.append(f"{p}={target_host}:{p}")
            elif isinstance(p, dict):
                local = p.get("local") or p.get("listen_port") or p.get("public_port")
                tgt_host = p.get("target_host") or target_host
                tgt_port = p.get("target_port") or p.get("remote_port") or local
                if local:
                    processed_ports.append(f"{local}={tgt_host}:{tgt_port}")
            else:
                processed_ports.append(str(p))
        ports = processed_ports

    bind_ip = server_spec.get("bind_ip") or server_spec.get("listen_ip") or "0.0.0.0"
    server_spec["bind_addr"] = f"{bind_ip}:{control_port}"
    server_spec["control_port"] = control_port
    server_spec["transport"] = transport
    server_spec["type"] = transport
    server_spec["ports"] = ports
    server_spec["token"] = token

    transport_lower = transport.lower()
    host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
    if transport_lower in ("ws", "wsmux"):
        use_tls = bool(server_spec.get("tls_cert") or server_spec.get("server_options", {}).get("tls_cert"))
        proto = "wss://" if use_tls else "ws://"
        client_spec["remote_addr"] = f"{proto}{host_part}:{control_port}"
    else:
        client_spec["remote_addr"] = f"{host_part}:{control_port}"

    client_spec["control_port"] = control_port
    client_spec["transport"] = transport
    client_spec["type"] = transport
    client_spec["ports"] = ports
    client_spec["token"] = token

    return server_spec, client_spec


def build_chisel_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Chisel core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    ports = parse_ports_list(spec)
    if not ports:
        listen_port = server_spec.get("listen_port") or server_spec.get("remote_port")
        if listen_port and str(listen_port).isdigit():
            ports = [int(listen_port)]

    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    first_port = ports[0] if ports else 8080
    server_control_port = server_spec.get("control_port") or (int(first_port) + 10000 + (port_hash % 1000))
    server_spec["server_port"] = server_control_port
    server_spec["reverse_port"] = first_port

    auth = server_spec.get("auth")
    if not auth:
        from app.utils import generate_token
        auth = generate_token()
        server_spec["auth"] = auth
        if tunnel.spec is not None:
            tunnel.spec["auth"] = auth

    fingerprint = server_spec.get("fingerprint")
    if fingerprint:
        server_spec["fingerprint"] = fingerprint
        client_spec["fingerprint"] = fingerprint

    host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
    client_spec["server_url"] = f"http://{host_part}:{server_control_port}"
    client_spec["reverse_port"] = first_port
    client_spec["ports"] = ports
    client_spec["auth"] = auth

    return server_spec, client_spec


def build_frp_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for FRP core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    bind_port = server_spec.get("bind_port") or (7000 + (port_hash % 1000))

    token = server_spec.get("token")
    if not token:
        from app.utils import generate_token
        token = generate_token()
        server_spec["token"] = token
        if tunnel.spec is not None:
            tunnel.spec["token"] = token

    transport_type = (
        getattr(tunnel, "transport_type", None)
        or server_spec.get("transport_type")
        or server_spec.get("transport")
        or "tcp"
    )
    security_type = getattr(tunnel, "security_type", None) or server_spec.get("security_type") or "tls"
    custom_sni = (
        getattr(tunnel, "custom_sni", None)
        or getattr(tunnel, "stealth_domain", None)
        or server_spec.get("custom_sni")
        or server_spec.get("stealth_domain")
    )
    use_encryption = server_spec.get("use_encryption", True)
    use_compression = server_spec.get("use_compression", True)

    server_spec["bind_port"] = bind_port
    server_spec["token"] = token
    server_spec["transport_type"] = transport_type
    server_spec["security_type"] = security_type

    client_spec["server_addr"] = iran_node_ip
    client_spec["server_port"] = bind_port
    client_spec["token"] = token
    client_spec["transport_type"] = transport_type
    client_spec["security_type"] = security_type
    client_spec["custom_sni"] = custom_sni
    client_spec["use_encryption"] = use_encryption
    client_spec["use_compression"] = use_compression

    tunnel_type = tunnel.type.lower() if tunnel.type else "tcp"
    if tunnel_type not in ["tcp", "udp"]:
        tunnel_type = "tcp"
    client_spec["type"] = tunnel_type
    local_ip = server_spec.get("local_ip") or "127.0.0.1"
    client_spec["local_ip"] = local_ip

    ports = server_spec.get("ports", [])
    if not ports:
        local_port = server_spec.get("local_port")
        remote_port = server_spec.get("remote_port") or server_spec.get("listen_port")
        if remote_port and local_port:
            client_spec["ports"] = [{"local": int(local_port), "remote": int(remote_port)}]
        elif remote_port:
            client_spec["ports"] = [{"local": int(remote_port), "remote": int(remote_port)}]
        elif local_port:
            client_spec["ports"] = [{"local": int(local_port), "remote": int(local_port)}]
        else:
            client_spec["ports"] = [{"local": int(bind_port), "remote": int(bind_port)}]
    else:
        client_spec["ports"] = ports

    return server_spec, client_spec


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
        auth_token = spec.get("auth_token") or spec.get("token") or "gost-token"
    if ports is None:
        ports = spec.get("ports") or [8080]

    is_reverse = getattr(tunnel, "is_reverse", False) or False
    cdn_mode = getattr(tunnel, "cdn_mode", False) or False
    gaming_mode = getattr(tunnel, "gaming_mode", False) or False
    custom_host = getattr(tunnel, "custom_host", None)
    custom_sni = getattr(tunnel, "custom_sni", None)
    ws_path = getattr(tunnel, "ws_path", None)
    stealth_domain = getattr(tunnel, "stealth_domain", None)
    rate_limit_mbps = getattr(tunnel, "rate_limit_mbps", None)
    transport_type = getattr(tunnel, "transport_type", "tcp") or "tcp"
    security_type = getattr(tunnel, "security_type", "none") or "none"
    failover_ips = getattr(tunnel, "failover_ips", None)
    port_ranges = getattr(tunnel, "port_ranges", None)
    allowed_ips = getattr(tunnel, "allowed_ips", None)

    base_spec = {
        "control_port": control_port,
        "auth_token": auth_token,
        "type": getattr(tunnel, "type", "tcp") or "tcp",
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
        "utls_fingerprint": getattr(tunnel, "utls_fingerprint", None),
        "custom_headers": getattr(tunnel, "custom_headers", None),
        "obfuscation_type": getattr(tunnel, "obfuscation_type", None),
        "mux_type": getattr(tunnel, "mux_type", None),
        "relay_hops": getattr(tunnel, "relay_hops", None),
        "bypass_ips": getattr(tunnel, "bypass_ips", None),
        "dns_resolvers": getattr(tunnel, "dns_resolvers", None),
    }

    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        for k in ["utls_fingerprint", "utls_client", "mux_type", "handler_type", "user_agent", "multiplex"]:
            if k in tunnel.spec:
                base_spec[k] = tunnel.spec[k]

    if is_reverse:
        # Reverse Tunnel: Iran Node is GOST Server, Foreign Node is GOST Client
        server_spec = base_spec.copy()
        server_spec["mode"] = "server"

        client_spec = base_spec.copy()
        client_spec["mode"] = "client"
        client_spec["server_ip"] = iran_node_ip

        if allowed_ips:
            allowed_ips_server = allowed_ips.copy()
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

        if allowed_ips:
            allowed_ips_foreign = allowed_ips.copy()
            if iran_node_ip and iran_node_ip not in allowed_ips_foreign:
                allowed_ips_foreign.append(iran_node_ip)
            client_spec["allowed_ips"] = allowed_ips_foreign
        else:
            client_spec["allowed_ips"] = None
        server_spec["allowed_ips"] = None

    return server_spec, client_spec


def build_tunnel_node_specs(
    tunnel,
    iran_node_ip: str,
    foreign_node_ip: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Master entry point: generate (server_spec, client_spec) for any tunnel core"""
    core = (tunnel.core or "").lower()
    if core == "rathole":
        return build_rathole_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "backhaul":
        return build_backhaul_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "chisel":
        return build_chisel_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "frp":
        return build_frp_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    elif core == "gost":
        return build_gost_node_specs(tunnel, iran_node_ip, foreign_node_ip)
    else:
        raise ValueError(f"Unsupported tunnel core: {core}")
