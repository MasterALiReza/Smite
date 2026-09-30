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

try:
    from app.utils import generate_token, generate_noise_keypair, generate_rathole_tls_bundle
except ImportError:
    try:
        from panel.app.utils import generate_token, generate_noise_keypair, generate_rathole_tls_bundle
    except ImportError:
        generate_token = None
        generate_noise_keypair = None
        generate_rathole_tls_bundle = None



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
                port_num = int(val)
                if 1 <= port_num <= 65535:
                    clean_ports.append(port_num)
        elif isinstance(p, (int, str)) and str(p).isdigit():
            port_num = int(p)
            if 1 <= port_num <= 65535:
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

        if not (server_priv and server_pub and client_priv and client_pub):
            if generate_noise_keypair:
                s_priv, s_pub = generate_noise_keypair()
                c_priv, c_pub = generate_noise_keypair()
            else:
                s_priv, s_pub = "mock-server-priv", "mock-server-pub"
                c_priv, c_pub = "mock-client-priv", "mock-client-pub"
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

    transport_lower = transport.lower()
    if transport_lower in ("websocket", "ws", "wss"):
        use_tls = (transport_lower == "wss") or bool(server_spec.get("websocket_tls") or server_spec.get("tls"))
        server_spec["websocket_tls"] = use_tls
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

    return server_spec, client_spec


def build_backhaul_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Backhaul core"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    tunnel_type = (getattr(tunnel, "type", "tcp") or "tcp").lower()
    raw_transport = (
        server_spec.get("transport")
        or server_spec.get("transport_type")
        or server_spec.get("type")
        or "tcpmux"
    )
    raw_transport_str = str(raw_transport).lower()

    # Differentiate between:
    # 1. Pure UDP transport (raw_transport == "udp" -> transport = "udp", lowest jitter / no HOL blocking)
    # 2. UDP-over-TCP encapsulation (accept_udp = True with transport = "tcp")
    is_pure_udp = raw_transport_str == "udp"
    is_udp_over_tcp = (
        server_spec.get("accept_udp") is True
        or (tunnel_type in ("udp", "tcp+udp") and not is_pure_udp)
        or (server_spec.get("type") in ("udp", "tcp+udp") and not is_pure_udp)
    )

    if is_pure_udp:
        transport = "udp"
        server_spec["transport"] = "udp"
        client_spec["transport"] = "udp"
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["transport"] = "udp"
    elif is_udp_over_tcp:
        # Musixal/Backhaul requires transport = "tcp" for accept_udp = true encapsulation
        transport = "tcp"
        server_spec["accept_udp"] = True
        client_spec["accept_udp"] = True
        server_spec["transport"] = "tcp"
        client_spec["transport"] = "tcp"
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["accept_udp"] = True
            tunnel.spec["transport"] = transport
    elif raw_transport_str in {"tcp", "tcpmux", "ws", "wss", "wsmux", "wssmux"}:
        transport = raw_transport_str
    else:
        transport = "tcpmux"

    token = server_spec.get("token")
    if not token:
        token = generate_token() if generate_token else "default-token"
        server_spec["token"] = token
        if getattr(tunnel, "spec", None) is not None:
            tunnel.spec["token"] = token

    target_host = server_spec.get("target_host", "127.0.0.1")

    ports = server_spec.get("ports", [])
    if not ports or len(ports) == 0:
        public_port = server_spec.get("public_port") or server_spec.get("remote_port") or server_spec.get("listen_port")
        target_port = server_spec.get("target_port") or public_port
        if public_port:
            p_str = str(public_port).strip()
            if target_port and p_str != str(target_port).strip():
                ports = [f"{p_str}={target_host}:{target_port}"]
            elif '-' in p_str:
                # Port range like 27000-27050
                ports = [p_str] if target_host in ("127.0.0.1", "localhost") else [f"{p_str}={target_host}:{p_str}"]
            else:
                ports = [f"{p_str}={target_host}:{p_str}"] if p_str.isdigit() else [p_str]
    else:
        processed_ports = []
        for p in ports:
            if not p:
                continue
            if isinstance(p, str):
                p_clean = p.strip()
                if '=' in p_clean:
                    processed_ports.append(p_clean)
                elif p_clean.isdigit():
                    processed_ports.append(f"{p_clean}={target_host}:{p_clean}")
                elif '-' in p_clean:
                    # Native Backhaul port range (e.g. 27000-27050)
                    if target_host in ("127.0.0.1", "localhost"):
                        processed_ports.append(p_clean)
                    else:
                        processed_ports.append(f"{p_clean}={target_host}:{p_clean}")
                else:
                    processed_ports.append(p_clean)
            elif isinstance(p, (int, float)):
                p_int = int(p)
                processed_ports.append(f"{p_int}={target_host}:{p_int}")
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
    server_spec["bind_addr"] = f"{bind_ip}:{control_port}"
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
        server_options.setdefault("mux_streambuffer", 131072)
        client_options.setdefault("mux_streambuffer", 131072)
        server_options.setdefault("mss", 1380)
        client_options.setdefault("mss", 1380)
        client_options.setdefault("aggressive_pool", True)

    for opts in (server_options, client_options):
        kp = opts.get("keepalive_period")
        if kp is None or not isinstance(kp, (int, float)) or kp > 25:
            opts["keepalive_period"] = 12 if is_gaming_mode else 20
        hb = opts.get("heartbeat")
        if hb is None or not isinstance(hb, (int, float)) or hb > 25:
            opts["heartbeat"] = 12 if is_gaming_mode else 20
        if is_udp_over_tcp:
            opts["accept_udp"] = True

    # Normalize numeric and boolean keys to exact types
    backhaul_numeric_keys = {
        "keepalive_period", "heartbeat", "channel_size", "mux_con", "web_port",
        "mss", "so_rcvbuf", "so_sndbuf", "mux_version", "mux_framesize",
        "mux_recievebuffer", "mux_streambuffer", "connection_pool", "retry_interval",
        "dial_timeout"
    }
    backhaul_boolean_keys = {
        "nodelay", "skip_optz", "sniffer", "proxy_protocol", "aggressive_pool", "accept_udp"
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
        "mux_streambuffer", "mux_con", "sniffer", "web_port"
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
    if transport_lower in ("ws", "wsmux"):
        use_tls = bool(server_spec.get("tls_cert") or server_options.get("tls_cert"))
        proto = "wss://" if use_tls else "ws://"
        client_spec["remote_addr"] = f"{proto}{host_part}:{control_port}"
    else:
        client_spec["remote_addr"] = f"{host_part}:{control_port}"

    client_spec["control_port"] = control_port
    client_spec["transport"] = transport
    client_spec["type"] = transport
    client_spec["tunnel_type"] = tunnel_type
    client_spec["ports"] = ports
    client_spec["token"] = token

    return server_spec, client_spec


def build_chisel_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for Chisel core with WSS, UDP, and Anti-DPI Camouflage"""
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

    # 1. Transport & TLS Resolution
    transport = (
        getattr(tunnel, "transport_type", None)
        or server_spec.get("transport")
        or server_spec.get("transport_type")
        or "ws"
    ).lower()
    use_tls = (transport in ("wss", "https", "tls")) or bool(
        server_spec.get("websocket_tls") or server_spec.get("tls")
    )
    
    server_spec["transport"] = "wss" if use_tls else "ws"
    server_spec["transport_type"] = "wss" if use_tls else "ws"
    client_spec["transport"] = "wss" if use_tls else "ws"
    client_spec["transport_type"] = "wss" if use_tls else "ws"
    server_spec["websocket_tls"] = use_tls
    client_spec["websocket_tls"] = use_tls

    # 2. Tunnel Type (tcp, udp, tcp+udp, socks5)
    tunnel_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or server_spec.get("type")
        or server_spec.get("tunnel_type")
        or "tcp"
    ).lower()
    if tunnel_type not in ["tcp", "udp", "tcp+udp", "socks5"]:
        tunnel_type = "tcp"
    server_spec["type"] = tunnel_type
    server_spec["tunnel_type"] = tunnel_type
    client_spec["type"] = tunnel_type
    client_spec["tunnel_type"] = tunnel_type

    # 3. Port parsing and Control Port allocation
    ports = parse_ports_list(spec)
    if not ports:
        listen_port = server_spec.get("listen_port") or server_spec.get("remote_port")
        if listen_port and str(listen_port).isdigit():
            ports = [int(listen_port)]
        else:
            ports = [8080]

    port_hash = int(hashlib.sha256(tunnel.id.encode()).hexdigest()[:8], 16)
    first_port = ports[0] if ports else 8080
    raw_control_port = server_spec.get("control_port")
    try:
        ctrl_p = int(raw_control_port) if raw_control_port else 0
    except (ValueError, TypeError):
        ctrl_p = 0
    if ctrl_p < 1024 or ctrl_p > 65535 or ctrl_p in ports:
        server_control_port = int(first_port) + 10000 + (port_hash % 1000)
        while server_control_port in ports:
            server_control_port += 1
    else:
        server_control_port = ctrl_p
    server_spec["server_port"] = server_control_port
    server_spec["control_port"] = server_control_port
    server_spec["reverse_port"] = first_port
    server_spec["ports"] = ports
    client_spec["server_port"] = server_control_port
    client_spec["control_port"] = server_control_port
    client_spec["reverse_port"] = first_port
    client_spec["ports"] = ports

    # 4. Authentication (Token)
    auth = server_spec.get("auth") or server_spec.get("token") or server_spec.get("auth_token")
    if not auth:
        auth = generate_token() if generate_token else "default-auth"
    server_spec["auth"] = auth
    server_spec["auth_token"] = auth
    server_spec["token"] = auth
    client_spec["auth"] = auth
    client_spec["auth_token"] = auth
    client_spec["token"] = auth

    # 5. Anti-DPI & Stealth (Custom SNI, Host Header, Decoy Backend)
    custom_sni = (
        getattr(tunnel, "custom_sni", None)
        or getattr(tunnel, "stealth_domain", None)
        or server_spec.get("custom_sni")
        or server_spec.get("stealth_domain")
    )
    custom_host = (
        getattr(tunnel, "custom_host", None)
        or server_spec.get("custom_host")
        or server_spec.get("hostname")
    )
    backend_url = (
        getattr(tunnel, "backend_url", None)
        or server_spec.get("backend_url")
        or server_spec.get("backend")
        or server_spec.get("decoy_url")
    )
    user_agent = getattr(tunnel, "user_agent", None) or server_spec.get("user_agent")

    if custom_sni:
        server_spec["custom_sni"] = custom_sni
        client_spec["custom_sni"] = custom_sni
    if custom_host:
        server_spec["custom_host"] = custom_host
        client_spec["custom_host"] = custom_host
    if backend_url:
        server_spec["backend_url"] = backend_url
    if user_agent:
        client_spec["user_agent"] = user_agent

    # 6. Stability Tuning (Keepalive, Max Retry Interval, Proxy)
    keepalive = getattr(tunnel, "keepalive", None) or server_spec.get("keepalive") or "10s"
    max_retry_interval = server_spec.get("max_retry_interval") or "10s"
    server_spec["keepalive"] = keepalive
    client_spec["keepalive"] = keepalive
    client_spec["max_retry_interval"] = max_retry_interval
    if "proxy" in server_spec:
        client_spec["proxy"] = server_spec["proxy"]

    # 7. TLS Certificate generation and propagation
    if use_tls:
        tls_cert_pem = server_spec.get("tls_cert_pem")
        tls_key_pem = server_spec.get("tls_key_pem")
        if not (tls_cert_pem and tls_key_pem):
            try:
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                import datetime

                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, custom_sni or iran_node_ip or "chisel-tunnel"),
                ])
                cert = x509.CertificateBuilder().subject_name(
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
                ).sign(key, hashes.SHA256())

                tls_key_pem = key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode('utf-8')
                tls_cert_pem = cert.public_bytes(
                    encoding=serialization.Encoding.PEM
                ).decode('utf-8')
            except Exception as e:
                logger.warning(f"Could not generate in-memory cert for chisel: {e}")

        if tls_cert_pem and tls_key_pem:
            server_spec["tls_cert_pem"] = tls_cert_pem
            server_spec["tls_key_pem"] = tls_key_pem
            if tunnel.spec is not None:
                tunnel.spec["tls_cert_pem"] = tls_cert_pem
                tunnel.spec["tls_key_pem"] = tls_key_pem

        client_spec["tls_skip_verify"] = server_spec.get("tls_skip_verify", True)

    fingerprint = server_spec.get("fingerprint")
    if fingerprint:
        server_spec["fingerprint"] = fingerprint
        client_spec["fingerprint"] = fingerprint

    host_part = f"[{iran_node_ip}]" if is_valid_ipv6(iran_node_ip) else iran_node_ip
    proto = "https://" if use_tls else "http://"
    client_spec["server_url"] = f"{proto}{host_part}:{server_control_port}"

    # 8. Sync state back to tunnel.spec for persistent DB storage
    if tunnel.spec is not None:
        tunnel.spec["auth"] = auth
        tunnel.spec["auth_token"] = auth
        tunnel.spec["token"] = auth
        tunnel.spec["control_port"] = server_control_port
        tunnel.spec["ports"] = ports
        tunnel.spec["transport"] = "wss" if use_tls else "ws"
        tunnel.spec["transport_type"] = "wss" if use_tls else "ws"
        tunnel.spec["type"] = tunnel_type
        tunnel.spec["tunnel_type"] = tunnel_type
        if custom_sni:
            tunnel.spec["custom_sni"] = custom_sni
        if custom_host:
            tunnel.spec["custom_host"] = custom_host
        if backend_url:
            tunnel.spec["backend_url"] = backend_url
        if keepalive:
            tunnel.spec["keepalive"] = keepalive

    return server_spec, client_spec


def build_frp_node_specs(tunnel, iran_node_ip: str, foreign_node_ip: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Generate server (Iran) and client (Foreign) specs for FRP core with WSS, QUIC, Strict Auth, Health Checks, Bandwidth Limits, and VHost routing"""
    import re
    spec = tunnel.spec.copy() if tunnel.spec else {}
    server_spec = spec.copy()
    server_spec["mode"] = "server"
    client_spec = spec.copy()
    client_spec["mode"] = "client"

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
    raw_ports = server_spec.get("ports")
    if raw_ports and isinstance(raw_ports, list) and len(raw_ports) > 0:
        ports = raw_ports
    else:
        ports = parse_ports_list(server_spec)
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

    if raw_transport in ["websocket", "ws", "wss", "https"]:
        transport_proto = "websocket"
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
    else: # tcp, kcp
        if explicit_security == "none":
            use_tls = False
            security_type = "none"
        else:
            use_tls = (explicit_security in ["tls", "force_tls"]) or bool(server_spec.get("tls_enable", True))
            security_type = explicit_security or ("tls" if use_tls else "none")

    custom_sni = (
        getattr(tunnel, "custom_sni", None)
        or getattr(tunnel, "stealth_domain", None)
        or server_spec.get("custom_sni")
        or server_spec.get("stealth_domain")
    )

    # In-memory self-signed TLS certificates for WSS / TLS server
    if use_tls:
        tls_cert_pem = server_spec.get("tls_cert_pem")
        tls_key_pem = server_spec.get("tls_key_pem")
        if not (tls_cert_pem and tls_key_pem):
            try:
                from cryptography import x509
                from cryptography.x509.oid import NameOID
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import rsa
                import datetime

                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                subject = issuer = x509.Name([
                    x509.NameAttribute(NameOID.COMMON_NAME, custom_sni or iran_node_ip or "frp-tunnel"),
                ])
                cert = x509.CertificateBuilder().subject_name(
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
                ).sign(key, hashes.SHA256())

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
    # UDP datagrams have high entropy/pre-encryption (e.g. WireGuard/gaming),
    # so compression wastes CPU and increases packet jitter.
    default_compression = False if (tunnel_type in ["udp", "tcp+udp"] or getattr(tunnel, "gaming_mode", False)) else True
    use_encryption = bool(server_spec.get("use_encryption", True))
    use_compression = bool(server_spec.get("use_compression", default_compression))
    
    # Internal health check configuration
    # Note: FRP ONLY supports 'tcp' and 'http' health checks. It has NO UDP health check.
    # Attaching a TCP health check to a pure UDP service causes continuous health check failure
    # (TCP connection refused) which immediately kills/unregisters the UDP proxy!
    if tunnel_type == "udp":
        enable_health_check = False
        health_check_type = None
    else:
        enable_health_check = bool(
            server_spec.get("enable_health_check", True)
            or server_spec.get("health_check_type")
            or getattr(tunnel, "gaming_mode", False)
        )
        health_check_type = server_spec.get("health_check_type") or ("tcp" if enable_health_check else None)
    health_check_interval = int(server_spec.get("health_check_interval_s") or 10)
    health_check_timeout = int(server_spec.get("health_check_timeout_s") or 3)
    health_check_max_failed = int(server_spec.get("health_check_max_failed") or 3)
    health_check_path = str(server_spec.get("health_check_path") or "/").strip()
    if not health_check_path.startswith("/"):
        health_check_path = f"/{health_check_path}"
    health_check_path = health_check_path.replace('"', '').replace('\n', '').replace('\r', '')

    # Bandwidth limit per proxy (normalized and validated format: e.g. 10MB, 500KB)
    rate_limit_mbps = getattr(tunnel, "rate_limit_mbps", None) or server_spec.get("rate_limit_mbps")
    raw_bw = server_spec.get("bandwidth_limit")
    if not raw_bw and rate_limit_mbps and float(rate_limit_mbps) > 0:
        raw_bw = f"{int(float(rate_limit_mbps))}MB"
    
    bandwidth_limit = None
    if raw_bw:
        clean_bw = str(raw_bw).strip().upper().replace(" ", "")
        if clean_bw.isdigit():
            clean_bw = f"{clean_bw}MB"
        if re.match(r'^\d+(KB|MB|GB|B)$', clean_bw):
            bandwidth_limit = clean_bw
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

    # 7. Assembling Server & Client Specs
    server_spec["bind_port"] = bind_port
    server_spec["control_port"] = bind_port
    server_spec["vhost_http_port"] = vhost_http_port
    server_spec["vhost_https_port"] = vhost_https_port
    server_spec["token"] = token
    server_spec["transport_type"] = transport_proto
    server_spec["transport"] = transport_proto
    server_spec["security_type"] = security_type
    server_spec["tls_enable"] = use_tls
    server_spec["tunnel_type"] = tunnel_type
    server_spec["type"] = tunnel_type
    server_spec["ports"] = ports
    server_spec["use_encryption"] = use_encryption
    server_spec["use_compression"] = use_compression

    client_spec["server_addr"] = iran_node_ip
    client_spec["server_port"] = bind_port
    client_spec["control_port"] = bind_port
    client_spec["token"] = token
    client_spec["transport_type"] = transport_proto
    client_spec["transport"] = transport_proto
    client_spec["security_type"] = security_type
    client_spec["tls_enable"] = use_tls
    if custom_sni:
        client_spec["custom_sni"] = custom_sni
        server_spec["custom_sni"] = custom_sni
    client_spec["use_encryption"] = use_encryption
    client_spec["use_compression"] = use_compression
    client_spec["tunnel_type"] = tunnel_type
    client_spec["type"] = tunnel_type
    client_spec["local_ip"] = server_spec.get("local_ip", "127.0.0.1")
    client_spec["ports"] = ports

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

    if proxy_protocol_version and tunnel_type in ["tcp", "http", "https"]:
        client_spec["proxy_protocol_version"] = proxy_protocol_version
    else:
        client_spec.pop("proxy_protocol_version", None)

    if custom_domains:
        client_spec["custom_domains"] = custom_domains
        server_spec["custom_domains"] = custom_domains

    # 8. Sync state back into tunnel.spec for persistent DB storage
    if tunnel.spec is not None:
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
        if proxy_protocol_version and tunnel_type in ["tcp", "http", "https"]:
            tunnel.spec["proxy_protocol_version"] = proxy_protocol_version
        else:
            tunnel.spec.pop("proxy_protocol_version", None)
        if custom_domains:
            tunnel.spec["custom_domains"] = custom_domains

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
        parsed_ports = parse_ports_list(spec)
        ports = parsed_ports if parsed_ports else [8080]

    spec["ports"] = ports
    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        tunnel.spec["ports"] = ports

    force_direct = bool(spec.get("force_direct") or (getattr(tunnel, "spec", {}) or {}).get("force_direct"))
    if force_direct:
        is_reverse = False
    else:
        is_reverse = getattr(tunnel, "is_reverse", None)
        if is_reverse is None:
            is_reverse = spec.get("is_reverse")
        # For multi-node setup (iran + foreign), default to reverse unless force_direct is explicitly set
        if is_reverse is None or not is_reverse:
            if foreign_node_ip or getattr(tunnel, "foreign_node_id", None) or getattr(tunnel, "iran_node_id", None):
                is_reverse = True
            else:
                is_reverse = bool(is_reverse)

    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        tunnel.spec["is_reverse"] = is_reverse
    if hasattr(tunnel, "is_reverse"):
        try:
            tunnel.is_reverse = is_reverse
        except Exception:
            pass

    cdn_mode = getattr(tunnel, "cdn_mode", False) or spec.get("cdn_mode", False) or False
    gaming_mode = getattr(tunnel, "gaming_mode", False) or spec.get("gaming_mode", False) or False
    custom_host = getattr(tunnel, "custom_host", None) or spec.get("custom_host")
    custom_sni = getattr(tunnel, "custom_sni", None) or spec.get("custom_sni")
    ws_path = getattr(tunnel, "ws_path", None) or spec.get("ws_path")
    stealth_domain = getattr(tunnel, "stealth_domain", None) or spec.get("stealth_domain")
    rate_limit_mbps = getattr(tunnel, "rate_limit_mbps", None) or spec.get("rate_limit_mbps")
    transport_type = getattr(tunnel, "transport_type", None) or spec.get("transport_type") or spec.get("transport") or "tcp"
    security_type = getattr(tunnel, "security_type", None) or spec.get("security_type") or "none"
    failover_ips = getattr(tunnel, "failover_ips", None) or spec.get("failover_ips")
    port_ranges = getattr(tunnel, "port_ranges", None) or spec.get("port_ranges")
    allowed_ips = getattr(tunnel, "allowed_ips", None) or spec.get("allowed_ips")

    tunnel_type = (
        getattr(tunnel, "type", None)
        or getattr(tunnel, "tunnel_type", None)
        or spec.get("type")
        or spec.get("tunnel_type")
        or "tcp"
    ).lower()
    if tunnel_type not in ["tcp", "udp", "tcp+udp"]:
        tunnel_type = "tcp"

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

    target_host = spec.get("target_host") or getattr(tunnel, "target_host", None)
    if target_host:
        base_spec["target_host"] = target_host

    # For UDP datagram tunneling over streaming transports, ensure multiplexing is default
    if tunnel_type in ["udp", "tcp+udp"] and transport_type.lower() not in ["mws", "mwss", "udp", "rudp", "kcp", "quic"]:
        base_spec["multiplex"] = True
        if not base_spec.get("mux_type"):
            base_spec["mux_type"] = "yamux"

    if hasattr(tunnel, "spec") and isinstance(tunnel.spec, dict):
        for k in ["utls_fingerprint", "utls_client", "mux_type", "handler_type", "user_agent", "multiplex", "selector_strategy", "strategy", "keepalive_interval", "max_fails", "fail_timeout"]:
            if k in tunnel.spec:
                base_spec[k] = tunnel.spec[k]

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
