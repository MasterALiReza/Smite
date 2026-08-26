"""Core adapters for different tunnel types"""
from typing import Protocol, Dict, Any, Optional, List
from pathlib import Path
import subprocess
import asyncio
import os
import psutil
import time
import logging
import signal
import shutil

logger = logging.getLogger(__name__)

async def free_port(port: Optional[Any]) -> None:
    """Safely and aggressively terminate any process holding the specified port."""
    if not port:
        return
    try:
        port_num = int(port)
    except (ValueError, TypeError):
        return
    if port_num <= 0:
        return
    
    current_pid = os.getpid()
    try:
        for p in psutil.process_iter(['pid', 'name']):
            if p.pid == current_pid:
                continue
            try:
                for conn in p.net_connections(kind='all'):
                    if conn.laddr and conn.laddr.port == port_num:
                        logger.warning(f"Terminating process {p.pid} ({p.name()}) holding port {port_num}")
                        p.kill()
                        try:
                            p.wait(timeout=1.0)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Error freeing port {port_num}: {e}")

    try:
        fuser_proc = await asyncio.create_subprocess_exec(
            "fuser", "-k", "-9", f"{port_num}/tcp",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await asyncio.wait_for(fuser_proc.wait(), timeout=1.0)
    except Exception:
        pass

    try:
        fuser_udp = await asyncio.create_subprocess_exec(
            "fuser", "-k", "-9", f"{port_num}/udp",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        await asyncio.wait_for(fuser_udp.wait(), timeout=1.0)
    except Exception:
        pass


async def safe_stop_subprocess(
    proc: Optional[asyncio.subprocess.Process] = None,
    patterns: Optional[List[str]] = None,
    timeout: float = 3.0
) -> None:
    """
    Safely and thoroughly stop a subprocess and any associated process group or orphan processes.
    Uses process group signaling, psutil process-table scanning, and fallback pattern killing.
    """
    if proc is not None and proc.returncode is None:
        if os.name == 'posix':
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as e:
                logger.debug(f"Error terminating pgid for pid {proc.pid}: {e}")
        
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        except Exception:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except (asyncio.TimeoutError, Exception):
            if os.name == 'posix':
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception:
                    pass
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    if patterns:
        current_pid = os.getpid()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            if p.pid == current_pid:
                continue
            try:
                cmdline_str = " ".join(p.info.get('cmdline') or [])
                for pat in patterns:
                    if pat and pat in cmdline_str:
                        logger.info(f"Terminating orphan process {p.pid} matching '{pat}'")
                        p.kill()
                        try:
                            p.wait(timeout=1.0)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                pass

        for pat in patterns:
            if not pat or not str(pat).strip():
                continue
            try:
                kill_proc = await asyncio.create_subprocess_exec(
                    "pkill", "-9", "-f", str(pat),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                await asyncio.wait_for(kill_proc.wait(), timeout=2.0)
            except Exception:
                pass

def parse_address_port(address_str: str):
    """Parse address:port string, returns (host, port, is_ipv6)"""
    import re
    import ipaddress
    
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


class CoreAdapter(Protocol):
    """Protocol for core adapters"""
    name: str
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Apply tunnel configuration"""
        ...
    
    async def remove(self, tunnel_id: str) -> None:
        """Remove tunnel"""
        ...
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get tunnel status"""
        ...


class RatholeAdapter:
    """Rathole reverse tunnel adapter"""
    name = "rathole"
    
    def __init__(self):
        self.config_dir = Path("/etc/smite-node/rathole")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.processes = {}
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Rathole tunnel - supports both server and client modes"""
        # Always remove any previous or orphan instance for this tunnel before applying
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)
        
        mode = spec.get('mode', 'client')
        
        transport = (spec.get('transport_type') or spec.get('transport') or 'tcp').lower()
        tunnel_type = (spec.get('tunnel_type') or spec.get('type') or 'tcp').lower()
        if transport in ['ws', 'websocket']:
            use_websocket = True
            use_noise = False
        elif transport == 'noise':
            use_websocket = False
            use_noise = True
        else:
            use_websocket = False
            use_noise = False

        websocket_tls = spec.get('websocket_tls', False) or spec.get('tls', False) or (transport == 'wss')
        
        if mode == 'server':
            bind_addr = spec.get('bind_addr', '0.0.0.0:23333')
            token = spec.get('token', '').strip()
            
            ports = spec.get('ports') or []
            if not ports:
                proxy_port = spec.get('proxy_port') or spec.get('remote_port') or spec.get('listen_port')
                if proxy_port:
                    ports = [int(proxy_port) if isinstance(proxy_port, (int, str)) and str(proxy_port).isdigit() else proxy_port]
            
            if not token:
                raise ValueError("Rathole server requires 'token' in spec")
            if not ports:
                raise ValueError("Rathole server requires 'ports' array or 'proxy_port'/'remote_port' in spec")
            
            bind_host, bind_port, is_ipv6 = parse_address_port(bind_addr)
            if not bind_port:
                bind_host = "0.0.0.0"
                bind_port = 23333
            
            config = f"""[server]
bind_addr = "{bind_host}:{bind_port}"
default_token = "{token}"
heartbeat_interval = 10
"""
            
            if use_noise:
                local_priv = spec.get('server_private_key') or spec.get('local_private_key', '')
                remote_pub = spec.get('client_public_key') or spec.get('remote_public_key', '')
                config += f"""
[server.transport]
type = "noise"

[server.transport.noise]
pattern = "Noise_KK_25519_ChaChaPoly_BLAKE2s"
local_private_key = "{local_priv}"
remote_public_key = "{remote_pub}"
"""
            elif use_websocket:
                config += f"""
[server.transport]
type = "websocket"

[server.transport.websocket]
"""
                if websocket_tls:
                    config += "tls = true\n"
            
            for i, port in enumerate(ports):
                port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                base_service_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id
                
                if tunnel_type == 'tcp+udp':
                    config += f"""
[server.services.{base_service_name}_tcp]
bind_addr = "0.0.0.0:{port_num}"
nodelay = true

[server.services.{base_service_name}_udp]
bind_addr = "0.0.0.0:{port_num}"
type = "udp"
nodelay = true
"""
                elif tunnel_type == 'udp':
                    config += f"""
[server.services.{base_service_name}]
bind_addr = "0.0.0.0:{port_num}"
type = "udp"
nodelay = true
"""
                else:
                    config += f"""
[server.services.{base_service_name}]
bind_addr = "0.0.0.0:{port_num}"
nodelay = true
"""
            
            config_path = self.config_dir / f"{tunnel_id}.toml"
            with open(config_path, "w") as f:
                f.write(config)
            
            # Free ports before launching rathole server
            await free_port(bind_port)
            for port in ports:
                try:
                    p_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                    await free_port(p_num)
                except Exception:
                    pass
            await asyncio.sleep(0.3)

            try:
                proc = await asyncio.create_subprocess_exec(*["/usr/local/bin/rathole", "-s", str(config_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            except FileNotFoundError:
                proc = await asyncio.create_subprocess_exec(*["rathole", "-s", str(config_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
        else:
            remote_addr = spec.get('remote_addr', '').strip()
            token = spec.get('token', '').strip()
            
            # Support multiple ports
            ports = spec.get('ports') or []
            if not ports:
                # Fallback to single port for backward compatibility
                local_addr = spec.get('local_addr', '127.0.0.1:8080')
                # Extract port from local_addr
                _, local_port, _ = parse_address_port(local_addr)
                if local_port:
                    ports = [local_port]
                else:
                    ports = [8080]
            
            if not remote_addr:
                raise ValueError("Rathole client requires 'remote_addr' (foreign server address) in spec")
            if not token:
                raise ValueError("Rathole client requires 'token' in spec")
            
            if remote_addr.startswith('ws://'):
                remote_addr = remote_addr[5:]
                use_websocket = True
            elif remote_addr.startswith('wss://'):
                remote_addr = remote_addr[6:]
                use_websocket = True
                websocket_tls = True
            
            config = f"""[client]
remote_addr = "{remote_addr}"
default_token = "{token}"
heartbeat_timeout = 25
retry_interval = 1
"""
            
            if use_noise:
                local_priv = spec.get('client_private_key') or spec.get('local_private_key', '')
                remote_pub = spec.get('server_public_key') or spec.get('remote_public_key', '')
                config += f"""
[client.transport]
type = "noise"

[client.transport.noise]
pattern = "Noise_KK_25519_ChaChaPoly_BLAKE2s"
local_private_key = "{local_priv}"
remote_public_key = "{remote_pub}"
"""
            elif use_websocket:
                config += f"""
[client.transport]
type = "websocket"

[client.transport.websocket]
"""
                if websocket_tls:
                    config += "tls = true\n"
            
            # Create multiple service sections for multiple ports
            for i, port in enumerate(ports):
                port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                base_service_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id
                local_addr = f"127.0.0.1:{port_num}"
                
                if tunnel_type == 'tcp+udp':
                    config += f"""
[client.services.{base_service_name}_tcp]
local_addr = "{local_addr}"
nodelay = true

[client.services.{base_service_name}_udp]
local_addr = "{local_addr}"
type = "udp"
nodelay = true
"""
                elif tunnel_type == 'udp':
                    config += f"""
[client.services.{base_service_name}]
local_addr = "{local_addr}"
type = "udp"
nodelay = true
"""
                else:
                    config += f"""
[client.services.{base_service_name}]
local_addr = "{local_addr}"
nodelay = true
"""
            
            config_path = self.config_dir / f"{tunnel_id}.toml"
            with open(config_path, "w") as f:
                f.write(config)
            
            try:
                proc = await asyncio.create_subprocess_exec(*["/usr/local/bin/rathole", "-c", str(config_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            except FileNotFoundError:
                proc = await asyncio.create_subprocess_exec(*["rathole", "-c", str(config_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
        
        self.processes[tunnel_id] = proc
        await asyncio.sleep(0.5)
        if proc.returncode is not None:
            stderr = (await proc.stderr.read()).decode() if proc.stderr else "Unknown error"
            raise RuntimeError(f"rathole failed to start: {stderr}")
    
    async def remove(self, tunnel_id: str):
        """Remove Rathole tunnel"""
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.pop(tunnel_id, None)
        await safe_stop_subprocess(proc, patterns=[f"rathole.*{tunnel_id}", f"-s.*{tunnel_id}", f"-c.*{tunnel_id}"])
            
        if config_path.exists():
            try:
                content = config_path.read_text(encoding="utf-8", errors="ignore")
                for line in content.splitlines():
                    if "bind_addr" in line and ":" in line:
                        p_str = line.split(":")[-1].replace('"', '').replace("'", "").strip()
                        if p_str.isdigit():
                            await free_port(int(p_str))
                config_path.unlink()
            except Exception:
                pass
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        config_path = self.config_dir / f"{tunnel_id}.toml"
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        return {
            "active": config_path.exists() and is_running,
            "type": "rathole",
            "config_exists": config_path.exists(),
            "process_running": is_running
        }


class BackhaulAdapter:
    """Backhaul reverse tunnel adapter"""
    name = "backhaul"

    CLIENT_OPTION_KEYS = [
        "connection_pool",
        "retry_interval",
        "nodelay",
        "keepalive_period",
        "log_level",
        "pprof",
        "mux_session",
        "mux_version",
        "mux_framesize",
        "mux_recievebuffer",
        "mux_streambuffer",
        "sniffer",
        "web_port",
        "sniffer_log",
        "dial_timeout",
        "aggressive_pool",
        "edge_ip",
        "skip_optz",
        "mss",
        "so_rcvbuf",
        "so_sndbuf",
        "accept_udp",
    ]

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        binary_path: Optional[Path] = None,
    ):
        resolved_config = config_dir or Path(
            os.environ.get("SMITE_BACKHAUL_CLIENT_DIR", "/etc/smite-node/backhaul")
        )
        self.config_dir = Path(resolved_config)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.processes: Dict[str, asyncio.subprocess.Process] = {}
        self.log_handles: Dict[str, Any] = {}
        default_binary = binary_path or Path(
            os.environ.get("BACKHAUL_CLIENT_BINARY", "/usr/local/bin/backhaul")
        )
        self.binary_candidates = [
            Path(default_binary),
            Path("backhaul"),
        ]

    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Backhaul tunnel - supports both server and client modes"""
        if tunnel_id in self.processes:
            logger.info(f"Backhaul tunnel {tunnel_id} already exists, removing it first")
            await self.remove(tunnel_id)
        
        mode = spec.get('mode', 'client')
        
        if mode == 'server':
            transport = (spec.get("transport") or spec.get("type") or "tcp").lower()
            if transport not in {"tcp", "udp", "ws", "wsmux", "tcpmux"}:
                raise ValueError(f"Unsupported Backhaul transport '{transport}'")
            
            server_options = dict(spec.get("server_options") or {})
            bind_addr = spec.get("bind_addr")
            if not bind_addr:
                control_port = spec.get("control_port") or spec.get("listen_port") or 3080
                bind_ip = spec.get("bind_ip", "0.0.0.0")
                bind_addr = f"{bind_ip}:{control_port}"
            
            ports = spec.get("ports")
            logger.info(f"Backhaul {mode} tunnel {tunnel_id}: received ports from spec: {ports} (type: {type(ports)})")
            
            if not ports or (isinstance(ports, list) and len(ports) == 0):
                listen_port = spec.get("public_port") or spec.get("listen_port")
                target_addr = spec.get("target_addr")
                if not target_addr:
                    target_host = spec.get("target_host", "127.0.0.1")
                    target_port = spec.get("target_port") or listen_port
                    if target_port:
                        target_addr = f"{target_host}:{target_port}"
                if listen_port and target_addr:
                    ports = [f"{listen_port}={target_addr}"]
                elif listen_port:
                    ports = [str(listen_port)]
                else:
                    ports = []
            
            if isinstance(ports, list):
                processed_ports = []
                for p in ports:
                    if not p:
                        continue
                    if isinstance(p, str):
                        processed_ports.append(p)
                    elif isinstance(p, (int, float)):
                        processed_ports.append(str(p))
                    elif isinstance(p, dict):
                        local = p.get("local") or p.get("listen_port") or p.get("public_port")
                        target_host = p.get("target_host") or spec.get("target_host", "127.0.0.1")
                        target_port = p.get("target_port") or p.get("remote_port") or local
                        if local:
                            processed_ports.append(f"{local}={target_host}:{target_port}")
                    else:
                        processed_ports.append(str(p))
                ports = processed_ports
            else:
                ports = [str(ports)] if ports else []
            
            logger.info(f"Backhaul {mode} tunnel {tunnel_id}: processed ports: {ports} (count: {len(ports)})")
            
            server_config: Dict[str, Any] = {
                "bind_addr": bind_addr,
                "transport": transport,
                "ports": ports,
            }
            
            token = spec.get("token") or server_options.get("token")
            if token:
                server_config["token"] = token
            
            SERVER_OPTION_KEYS = [
                "nodelay", "keepalive_period", "channel_size", "log_level",
                "heartbeat", "mux_con", "accept_udp", "skip_optz",
                "tls_cert", "tls_key", "sniffer", "web_port", "proxy_protocol"
            ]
            for key in SERVER_OPTION_KEYS:
                value = server_options.get(key) or spec.get(key)
                if value is not None and value != "":
                    server_config[key] = value
            
            server_config.setdefault("keepalive_period", 20)
            server_config.setdefault("heartbeat", 20)
            server_config.setdefault("nodelay", True)
            
            config_path = self.config_dir / f"{tunnel_id}.toml"
            config_path.write_text(self._render_toml({"server": server_config}), encoding="utf-8")
            
            # Free ports before starting Backhaul server
            try:
                _, b_port, _ = parse_address_port(bind_addr)
                if b_port:
                    await free_port(b_port)
            except Exception:
                pass
            for p in ports:
                try:
                    p_str = str(p).split('=')[0].strip()
                    await free_port(int(p_str))
                except Exception:
                    pass

            binary_path = self._resolve_binary_path()
            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul server for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml({"server": server_config}))
            log_fh.flush()
            
            try:
                proc = await asyncio.create_subprocess_exec(*[str(binary_path), "-c", str(config_path)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.config_dir),
                    start_new_session=True,
                )
            except Exception:
                log_fh.close()
                raise
        else:
            remote_addr = spec.get("remote_addr") or spec.get("control_addr") or spec.get("bind_addr")
            if not remote_addr:
                raise ValueError("Backhaul client requires 'remote_addr' in spec")

            if remote_addr.startswith('ws://'):
                remote_addr = remote_addr[5:]
            elif remote_addr.startswith('wss://'):
                remote_addr = remote_addr[6:]

            transport = (spec.get("transport") or spec.get("type") or "tcp").lower()
            if transport not in {"tcp", "udp", "ws", "wsmux", "tcpmux"}:
                raise ValueError(f"Unsupported Backhaul transport '{transport}'")
            client_options = dict(spec.get("client_options") or {})

            config_dict: Dict[str, Any] = {
                "remote_addr": remote_addr,
                "transport": transport,
            }

            token = spec.get("token") or client_options.get("token")
            if token:
                config_dict["token"] = token

            for key in self.CLIENT_OPTION_KEYS:
                value = client_options.get(key)
                if value is None or value == "":
                    value = spec.get(key)
                if value is None or value == "":
                    continue
                config_dict[key] = value

            if "connection_pool" not in config_dict:
                config_dict["connection_pool"] = 8
            if "retry_interval" not in config_dict:
                config_dict["retry_interval"] = 3
            if "dial_timeout" not in config_dict:
                config_dict["dial_timeout"] = 10
            if "keepalive_period" not in config_dict:
                config_dict["keepalive_period"] = 20
            if "heartbeat" not in config_dict:
                config_dict["heartbeat"] = 20
            if "aggressive_pool" not in config_dict:
                config_dict["aggressive_pool"] = True

            if spec.get("accept_udp") and transport in {"tcp", "tcpmux"}:
                config_dict["accept_udp"] = True

            config_path = self.config_dir / f"{tunnel_id}.toml"
            config_path.write_text(self._render_toml({"client": config_dict}), encoding="utf-8")

            binary_path = self._resolve_binary_path()

            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul client for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml({"client": config_dict}))
            log_fh.flush()

            try:
                proc = await asyncio.create_subprocess_exec(*[str(binary_path), "-c", str(config_path)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                log_fh.close()
                raise

        await asyncio.sleep(0.5)
        if proc.returncode is not None:
            error_output = ""
            try:
                error_output = log_path.read_text(encoding="utf-8")[-1000:]
            except Exception:
                pass
            log_fh.close()
            raise RuntimeError(f"backhaul failed to start: {error_output}")

        self.processes[tunnel_id] = proc
        self.log_handles[tunnel_id] = log_fh

    async def remove(self, tunnel_id: str):
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[f"backhaul.*{tunnel_id}"])

        if config_path.exists():
            try:
                config_path.unlink()
            except Exception:
                pass

    def status(self, tunnel_id: str) -> Dict[str, Any]:
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.get(tunnel_id)
        is_running = proc is not None and proc.returncode is None
        return {
            "active": config_path.exists() and is_running,
            "type": "backhaul",
            "config_exists": config_path.exists(),
            "process_running": is_running,
        }

    def _render_toml(self, data: Dict[str, Dict[str, Any]]) -> str:
        def format_value(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return str(value)
            if isinstance(value, list):
                if not value:
                    return "[]"
                rendered = ",\n  ".join(f"\"{str(item)}\"" for item in value)
                return "[\n  " + rendered + "\n]"
            value_str = str(value).replace("\\", "\\\\").replace('"', '\\"')
            return f"\"{value_str}\""

        lines: List[str] = []
        for section, values in data.items():
            lines.append(f"[{section}]")
            for key, val in values.items():
                if val is None:
                    continue
                lines.append(f"{key} = {format_value(val)}")
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _resolve_binary_path(self) -> Path:
        for candidate in self.binary_candidates:
            if candidate.exists():
                return candidate

        resolved = shutil.which("backhaul")
        if resolved:
            return Path(resolved)

        raise FileNotFoundError(
            "Backhaul binary not found. Expected at BACKHAUL_CLIENT_BINARY, '/usr/local/bin/backhaul', or in PATH."
        )


class ChiselAdapter:
    """Chisel reverse tunnel adapter"""
    name = "chisel"
    
    def __init__(self):
        self.config_dir = Path("/etc/smite-node/chisel")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.log_handles = {}
    
    def _resolve_binary_path(self) -> Path:
        """Resolve chisel binary path"""
        env_path = os.environ.get("CHISEL_BINARY")
        if env_path:
            resolved = Path(env_path)
            if resolved.exists() and resolved.is_file():
                return resolved
        
        common_paths = [
            Path("/usr/local/bin/chisel"),
            Path("/usr/bin/chisel"),
            Path("/opt/chisel/chisel"),
        ]
        
        for path in common_paths:
            if path.exists() and path.is_file():
                return path
        
        resolved = shutil.which("chisel")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "Chisel binary not found. Expected at CHISEL_BINARY, '/usr/local/bin/chisel', or in PATH."
        )
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Chisel tunnel - supports both server and client modes"""
        if tunnel_id in self.processes:
            logger.info(f"Chisel tunnel {tunnel_id} already exists, removing it first")
            await self.remove(tunnel_id)
        
        mode = spec.get('mode', 'client')
        
        if mode == 'server':
            server_port = spec.get('server_port') or spec.get('control_port') or spec.get('listen_port')
            if not server_port:
                raise ValueError("Chisel server requires 'server_port' or 'control_port' in spec")
            
            reverse_port = spec.get('reverse_port') or spec.get('remote_port') or spec.get('listen_port')
            if not reverse_port:
                raise ValueError("Chisel server requires 'reverse_port' or 'remote_port' in spec")
            
            host = "0.0.0.0"
            binary_path = self._resolve_binary_path()
            cmd = [
                str(binary_path),
                "server",
                "--host", host,
                "--port", str(server_port),
                "--reverse",
                "--keepalive", "25s"
            ]
            
            auth = spec.get('auth')
            if auth:
                cmd.extend(["--auth", auth])
            
            fingerprint = spec.get('fingerprint')
            if fingerprint:
                cmd.extend(["--fingerprint", fingerprint])
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel server for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"server_port={server_port}, reverse_port={reverse_port}\n")
                log_f.flush()
                proc = await asyncio.create_subprocess_exec(*cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.config_dir),
                    start_new_session=True
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("chisel binary not found. Please install chisel.")
        else:
            server_url = spec.get('server_url', '').strip()
            
            # Support multiple ports
            ports = spec.get('ports') or []
            if not ports:
                # Fallback to single port for backward compatibility
                reverse_port = spec.get('reverse_port') or spec.get('remote_port') or spec.get('listen_port') or spec.get('server_port')
                if reverse_port:
                    ports = [int(reverse_port) if isinstance(reverse_port, (int, str)) and str(reverse_port).isdigit() else reverse_port]
            
            if not server_url:
                raise ValueError("Chisel client requires 'server_url' (foreign server address) in spec")
            if not ports:
                raise ValueError("Chisel client requires 'ports' array or 'reverse_port'/'remote_port'/'listen_port' in spec")
            
            binary_path = self._resolve_binary_path()
            cmd = [
                str(binary_path),
                "client",
                "--keepalive", "25s",
                "--max-retry-count", "5",
                "--max-retry-interval", "30s"
            ]
            
            auth = spec.get('auth')
            if auth:
                cmd.extend(["--auth", auth])
            
            fingerprint = spec.get('fingerprint')
            if fingerprint:
                cmd.extend(["--fingerprint", fingerprint])
            
            cmd.append(server_url)
            
            # Add multiple reverse specs for multiple ports
            for port in ports:
                port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                local_addr = spec.get('local_addr')
                if not local_addr:
                    local_addr = f"127.0.0.1:{port_num}"
                
                host, local_port, is_ipv6 = parse_address_port(local_addr)
                if not local_port:
                    host = "127.0.0.1"
                    local_port = port_num
                
                if is_ipv6:
                    reverse_spec = f"R:{port_num}:[{host}]:{local_port}"
                else:
                    reverse_spec = f"R:{port_num}:{host}:{local_port}"
                cmd.append(reverse_spec)
            
            reverse_specs = [f"R:{port}:127.0.0.1:{port}" for port in ports]
            logger.info(f"Chisel tunnel {tunnel_id}: ports={ports}, server_url={server_url}")
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel client for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"server_url={server_url}, reverse_specs={', '.join(reverse_specs)}\n")
                log_f.flush()
                proc = await asyncio.create_subprocess_exec(*cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.config_dir),
                    start_new_session=True
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("chisel binary not found. Please install chisel.")
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        await asyncio.sleep(1.0)  # Give it more time to start
        if proc.returncode is not None:
            stderr = ""
            if log_file.exists():
                with open(log_file, 'r') as f:
                    stderr = f.read()
            if tunnel_id in self.log_handles:
                try:
                    self.log_handles[tunnel_id].close()
                except:
                    pass
                del self.log_handles[tunnel_id]
            raise RuntimeError(f"chisel failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
    
    async def remove(self, tunnel_id: str):
        """Remove Chisel tunnel"""
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[f"chisel.*{tunnel_id}"])
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        return {
            "active": is_running,
            "type": "chisel",
            "process_running": is_running
        }


class FrpAdapter:
    """FRP reverse tunnel adapter"""
    name = "frp"
    
    def __init__(self):
        self.config_dir = Path("/etc/smite-node/frp")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.log_handles = {}
    
    def _resolve_binary_path(self) -> Path:
        """Resolve frpc binary path"""
        env_path = os.environ.get("FRPC_BINARY")
        if env_path:
            resolved = Path(env_path)
            if resolved.exists() and resolved.is_file():
                return resolved
        
        common_paths = [
            Path("/usr/local/bin/frpc"),
            Path("/usr/bin/frpc"),
        ]
        
        for path in common_paths:
            if path.exists() and path.is_file():
                return path
        
        resolved = shutil.which("frpc")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "frpc binary not found. Expected at FRPC_BINARY, '/usr/local/bin/frpc', or in PATH."
        )
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply FRP tunnel - supports both server and client modes"""
        logger.info(f"FRP tunnel {tunnel_id} pre-cleaning any existing processes")
        await self.remove(tunnel_id)
        await asyncio.sleep(0.3)
        
        mode = spec.get('mode', 'client')
        
        if mode == 'server':
            bind_port = spec.get('bind_port', 7000)
            if isinstance(bind_port, str) and str(bind_port).isdigit():
                bind_port = int(bind_port)
            elif not isinstance(bind_port, int):
                bind_port = 7000
            await free_port(bind_port)
            token = spec.get('token')
            force_tls = bool(spec.get('force_tls')) or (spec.get('security_type') in ['tls', 'force_tls'])
            transport_proto = (spec.get('transport_type') or spec.get('transport') or spec.get('protocol') or 'tcp').lower()
            
            config_file = self.config_dir / f"frps_{tunnel_id}.yaml"
            config_content = f"""bindPort: {bind_port}
"""
            if transport_proto == 'kcp':
                config_content += f"kcpBindPort: {bind_port}\nquicBindPort: 0\n"
            elif transport_proto == 'quic':
                config_content += f"kcpBindPort: 0\nquicBindPort: {bind_port}\n"
            else:
                config_content += "kcpBindPort: 0\nquicBindPort: 0\n"

            config_content += f"""transport:
  maxPoolCount: 8
  heartbeatTimeout: 90
  tcpMux: true
  tcpMuxKeepaliveInterval: 25
  tls:
    force: {'true' if force_tls else 'false'}
"""
            if token:
                config_content += f"""auth:
  method: token
  token: "{token}"
"""
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            logger.info(f"FRP server tunnel {tunnel_id}: bind_port={bind_port}, token={'set' if token else 'none'}")
            
            env_path = os.environ.get("FRPS_BINARY")
            if env_path:
                binary_path = Path(env_path)
            else:
                common_paths = [
                    Path("/usr/local/bin/frps"),
                    Path("/usr/bin/frps"),
                ]
                binary_path = None
                for path in common_paths:
                    if path.exists() and path.is_file():
                        binary_path = path
                        break
                if not binary_path:
                    resolved = shutil.which("frps")
                    if resolved:
                        binary_path = Path(resolved)
                    else:
                        raise FileNotFoundError("frps binary not found. Expected at FRPS_BINARY, '/usr/local/bin/frps', or in PATH.")
            
            config_file_abs = config_file.resolve()
            cmd = [
                str(binary_path),
                "-c", str(config_file_abs)
            ]
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting FRP server for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"Config: bind_port={bind_port}, token={'set' if token else 'none'}\n")
                log_f.flush()
                proc = await asyncio.create_subprocess_exec(*cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.config_dir),
                    start_new_session=True
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("FRP server binary (frps) not found. Please install FRP.")
        else:
            logger.info(f"FRP tunnel {tunnel_id} received spec: {spec}")
            
            server_addr = spec.get('server_addr', '').strip()
            server_port = spec.get('server_port', 7000)
            token = spec.get('token')
            tunnel_type = spec.get('type', 'tcp').lower()
            local_ip = spec.get('local_ip', '127.0.0.1')
            
            # Transport protocol selection (tcp, kcp, quic, websocket, wss)
            transport_proto = (spec.get('transport_type') or spec.get('transport') or spec.get('protocol') or 'tcp').lower()
            if transport_proto in ['websocket', 'ws']:
                transport_proto = 'websocket'
            elif transport_proto == 'wss':
                transport_proto = 'wss'
            elif transport_proto == 'quic':
                transport_proto = 'quic'
            elif transport_proto == 'kcp':
                transport_proto = 'kcp'
            else:
                transport_proto = 'tcp'
            
            # Stealth TLS & SNI configuration
            security_type = spec.get('security_type', 'tls')
            tls_enable = spec.get('tls_enable', True) if security_type != 'none' else False
            if transport_proto in ['wss', 'quic']:
                tls_enable = True
            
            custom_sni = spec.get('custom_sni') or spec.get('stealth_domain') or spec.get('server_name') or 'speedtest.net'
            
            # Layer-2 proxy payload encryption & compression
            use_encryption = spec.get('use_encryption', True)
            use_compression = spec.get('use_compression', True)
            
            ports = spec.get('ports') or []
            if not ports:
                local_port = spec.get('local_port')
                remote_port = spec.get('remote_port') or spec.get('listen_port')
                if remote_port and local_port:
                    ports = [{'local': local_port, 'remote': remote_port}]
                elif remote_port:
                    ports = [{'local': remote_port, 'remote': remote_port}]
                elif local_port:
                    ports = [{'local': local_port, 'remote': local_port}]
            
            # Expand port ranges if provided
            if spec.get("port_ranges"):
                for port_range in spec.get("port_ranges"):
                    if isinstance(port_range, str) and '-' in port_range:
                        try:
                            start, end = port_range.split('-')
                            if int(end) - int(start) <= 200:
                                for p in range(int(start), int(end) + 1):
                                    ports.append({'local': p, 'remote': p})
                        except Exception:
                            pass
            
            logger.info(f"FRP tunnel {tunnel_id} parsed: server_addr='{server_addr}', server_port={server_port}, proto={transport_proto}, tls={tls_enable}, sni={custom_sni}, ports={len(ports)}")
            
            if not server_addr:
                raise ValueError("FRP client requires 'server_addr' (foreign server address) in spec")
            if not ports:
                raise ValueError("FRP client requires 'ports' array or 'remote_port'/'listen_port' in spec")
            if tunnel_type not in ['tcp', 'udp']:
                raise ValueError(f"FRP only supports 'tcp' and 'udp' types, got '{tunnel_type}'")
            
            if server_addr.startswith('[') and server_addr.endswith(']'):
                server_addr = server_addr[1:-1]
            
            if not server_addr or server_addr in ["0.0.0.0", "localhost", "127.0.0.1", "::1"]:
                raise ValueError(f"Invalid FRP server_addr: {server_addr}. Must be a valid foreign server IP address or hostname.")
            
            config_file = self.config_dir / f"frpc_{tunnel_id}.yaml"
            config_content = f"""serverAddr: "{server_addr}"
serverPort: {server_port}
loginFailExit: false
transport:
  protocol: "{transport_proto}"
  heartbeatInterval: 25
  heartbeatTimeout: 90
  tcpMux: true
  tcpMuxKeepaliveInterval: 25
  dialServerTimeout: 15
"""
            if tls_enable:
                config_content += f"""  tls:
    enable: true
    disableCustomTLSFirstByte: true
    serverName: "{custom_sni}"
"""

            if token:
                config_content += f"""auth:
  method: token
  token: "{token}"
"""
            
            config_content += "\nproxies:\n"
            for i, port_config in enumerate(ports):
                if isinstance(port_config, dict):
                    local_port = port_config.get('local')
                    remote_port = port_config.get('remote')
                else:
                    local_port = remote_port = port_config
                
                proxy_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id
                config_content += f"""  - name: {proxy_name}
    type: {tunnel_type}
    localIP: {local_ip}
    localPort: {local_port}
    remotePort: {remote_port}
    transport:
      useEncryption: {'true' if use_encryption else 'false'}
      useCompression: {'true' if use_compression else 'false'}
"""
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            
            logger.info(f"FRP tunnel {tunnel_id}: type={tunnel_type}, proto={transport_proto}, local={local_ip}, server={server_addr}:{server_port}")
            
            binary_path = self._resolve_binary_path()
            config_file_abs = config_file.resolve()
            
            cmd = [
                str(binary_path),
                "-c", str(config_file_abs)
            ]
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting FRP client for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"Config: type={tunnel_type}, local={local_ip}:{local_port}, remote={remote_port}, server={server_addr}:{server_port}\n")
                log_f.flush()
                proc = await asyncio.create_subprocess_exec(*cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    cwd=str(self.config_dir),
                    start_new_session=True,
                    env=os.environ.copy()
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("FRP binary (frpc) not found. Please install FRP.")
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        await asyncio.sleep(1.0)
        if proc.returncode is not None:
            stderr = ""
            if log_file.exists():
                with open(log_file, 'r') as f:
                    stderr = f.read()
            if tunnel_id in self.log_handles:
                try:
                    self.log_handles[tunnel_id].close()
                except:
                    pass
                del self.log_handles[tunnel_id]
            raise RuntimeError(f"FRP failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
    
    async def remove(self, tunnel_id: str):
        """Remove FRP tunnel (handles both server and client modes)"""
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(
            proc,
            patterns=[
                f"frps.*{tunnel_id}",
                f"frpc.*{tunnel_id}",
            ]
        )

        for cfg_name in [f"frps_{tunnel_id}.yaml", f"frpc_{tunnel_id}.yaml", f"frps_{tunnel_id}.toml", f"frpc_{tunnel_id}.toml"]:
            cfg_path = self.config_dir / cfg_name
            if cfg_path.exists():
                try:
                    cfg_path.unlink()
                except Exception:
                    pass
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        return {
            "active": is_running,
            "type": "frp",
            "process_running": is_running
        }


class GostAdapter:
    """GOST forwarding adapter - forwards from Iran node to Foreign server"""
    name = "gost"
    
    def __init__(self):
        self.config_dir = Path("/etc/smite-node/gost")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.processes = {}
        self.log_handles = {}
    
    def _resolve_binary_path(self) -> Path:
        """Resolve gost binary path"""
        env_path = os.environ.get("GOST_BINARY")
        if env_path:
            resolved = Path(env_path)
            if resolved.exists() and resolved.is_file():
                return resolved
        
        common_paths = [
            Path("/usr/local/bin/gost"),
            Path("/usr/bin/gost"),
        ]
        
        for path in common_paths:
            if path.exists() and path.is_file():
                return path
        
        resolved = shutil.which("gost")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "GOST binary not found. Expected at GOST_BINARY, '/usr/local/bin/gost', or in PATH."
        )
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply GOST forwarding using native v3 config (JSON)"""
        import json
        
        if tunnel_id in self.processes:
            logger.info(f"GOST tunnel {tunnel_id} already exists, removing it first")
            await self.remove(tunnel_id)
            
        is_reverse = spec.get('is_reverse', False)
        mode = spec.get('mode', 'client')
        control_port = spec.get('control_port') or spec.get('remote_port')
        if not control_port:
            raise ValueError("GOST requires 'control_port' or 'remote_port' in spec")
            
        auth_token = spec.get('auth_token', '')
        transport_type = spec.get('transport_type') or spec.get('transport') or 'tcp'
        security_type = spec.get('security_type', 'none')
        use_ipv6 = spec.get('use_ipv6', False)
        
        # CDN Mode Logic
        if spec.get("cdn_mode") and transport_type in ["tcp", "tcp+udp"]:
            transport_type = "ws"
        
        if transport_type == "websocket":
            transport_type = "ws"
        elif transport_type in ["multiplex ws", "multiplex_ws"]:
            transport_type = "mws"

        gost_type = transport_type
        if transport_type == "ws" and security_type in ["tls", "utls"]:
            gost_type = "wss"
        elif transport_type == "mws" and security_type in ["tls", "utls"]:
            gost_type = "mwss"
        elif transport_type == "tcp" and security_type in ["tls", "utls"]:
            gost_type = "tls"
        
        config = {
            "services": [],
            "chains": []
        }
        
        # Add Resolvers if specified
        if spec.get("dns_resolvers") and isinstance(spec.get("dns_resolvers"), list):
            resolver_nodes = []
            for i, res in enumerate(spec.get("dns_resolvers")):
                resolver_nodes.append({"name": f"dns-{tunnel_id}-{i}", "addr": res})
            config["resolvers"] = [{
                "name": f"resolver-{tunnel_id}",
                "nodes": resolver_nodes
            }]
            
        # Add Bypasses if specified
        if spec.get("bypass_ips") and isinstance(spec.get("bypass_ips"), list):
            config["bypasses"] = [{
                "name": f"bypass-{tunnel_id}",
                "matchers": spec.get("bypass_ips")
            }]
        
        if mode == 'server':
            # 1. Server Configuration (Foreign Node)
            bind_addr = f"[::]:{control_port}" if use_ipv6 else f"0.0.0.0:{control_port}"
            
            # Handler & Protocol Selection
            handler_type = spec.get("handler_type") or "relay"
            mux_type = spec.get("mux_type") or "yamux"
            
            listener_metadata = {
                "keepAlive": True,
                "keepAliveInterval": "25s",
                "keepAliveTimeout": "120s",
                "idleTimeout": "0s",
            }
            if spec.get("ws_path"):
                listener_metadata["path"] = spec.get("ws_path")
            if is_reverse:
                listener_metadata["bind"] = True
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                listener_metadata["mux.type"] = mux_type
                listener_metadata["nodelay"] = True
                
            listener = {"type": gost_type}
            if listener_metadata:
                listener["metadata"] = listener_metadata
            
            if security_type in ["tls", "utls"] and gost_type not in ["tcp", "udp", "rtcp", "rudp"]:
                cert_path = self.config_dir / "dummy_cert.pem"
                key_path = self.config_dir / "dummy_key.pem"
                if not cert_path.exists() or not key_path.exists():
                    try:
                        subprocess.run([
                            "openssl", "req", "-new", "-newkey", "rsa:2048", "-days", "3650",
                            "-nodes", "-x509", "-subj", "/O=Smite/CN=smite.node",
                            "-keyout", str(key_path), "-out", str(cert_path)
                        ], check=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    except Exception as e:
                        logger.error(f"Failed to generate self-signed cert: {e}")
                
                if cert_path.exists() and key_path.exists():
                    listener["tls"] = {
                        "certFile": str(cert_path),
                        "keyFile": str(key_path)
                    }
                
            # 1. Access Control (ACL)
            adm_name = None
            if spec.get("allowed_ips"):
                adm_name = f"adm-{tunnel_id}"
                config["admissions"] = [
                    {
                        "name": adm_name,
                        "matchers": spec.get("allowed_ips")
                    }
                ]
                
            handler_metadata = {
                "keepAlive": True,
            }
            if is_reverse:
                handler_metadata["bind"] = True
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                handler_metadata["mux.type"] = mux_type
                handler_metadata["nodelay"] = True
                
            handler = {
                "type": handler_type
            }
            if auth_token:
                handler["auth"] = {
                    "username": auth_token,
                    "password": ""
                }
            if handler_metadata:
                handler["metadata"] = handler_metadata
            if spec.get("bypass_ips"):
                handler["bypass"] = f"bypass-{tunnel_id}"
            if spec.get("dns_resolvers"):
                handler["resolver"] = f"resolver-{tunnel_id}"
                
            service = {
                "name": f"gost-server-{tunnel_id}",
                "addr": bind_addr,
                "handler": handler,
                "listener": listener
            }
            if adm_name:
                service["admission"] = adm_name

            config["services"].append(service)
            
        else:
            # 2. Client Configuration (Iran Node)
            ports = spec.get('ports') or []
            if not ports:
                listen_port = spec.get('listen_port')
                if listen_port:
                    ports = [int(listen_port) if isinstance(listen_port, (int, str)) and str(listen_port).isdigit() else listen_port]
            
            # 4. Port Ranges
            if spec.get("port_ranges"):
                for port_range in spec.get("port_ranges"):
                    if isinstance(port_range, str) and '-' in port_range:
                        try:
                            start, end = port_range.split('-')
                            # expand carefully to avoid thousands of ports
                            if int(end) - int(start) <= 500:
                                ports.extend(range(int(start), int(end) + 1))
                            else:
                                ports.append(port_range) # Fallback to string if too large, though unsupported by pure GOST listeners
                        except Exception:
                            ports.append(port_range)
                    else:
                        ports.append(port_range)
            
            # Deduplicate ports while preserving order
            seen = set()
            unique_ports = []
            for p in ports:
                if p not in seen:
                    seen.add(p)
                    unique_ports.append(p)
            ports = unique_ports
            
            if not ports:
                raise ValueError("GOST client requires 'ports' array or 'listen_port' or 'port_ranges' in spec")
                
            server_ip = spec.get('server_ip') or spec.get('remote_ip')
            if not server_ip:
                raise ValueError("GOST client requires 'server_ip' or 'remote_ip' in spec")
                
            target_addr = f"[{server_ip}]:{control_port}" if ":" in server_ip and not server_ip.startswith("[") else f"{server_ip}:{control_port}"

            # Advanced Configuration
            dialer_metadata = {}
            if spec.get("ws_path"):
                dialer_metadata["path"] = spec.get("ws_path")
            if spec.get("custom_host"):
                dialer_metadata["host"] = spec.get("custom_host")
            elif spec.get("stealth_domain"):
                dialer_metadata["host"] = spec.get("stealth_domain")
                
            dialer_tls = {}
            if spec.get("custom_sni"):
                dialer_tls["serverName"] = spec.get("custom_sni")
            
            if spec.get("stealth_domain"):
                dialer_tls["serverName"] = spec.get("stealth_domain")
                
            if security_type == "utls":
                # Dynamic uTLS fingerprint support
                utls_client = spec.get("utls_client") or spec.get("utls_fingerprint") or "chrome"
                if utls_client in ["random", "randomized"]:
                    import random
                    utls_client = random.choice(["chrome", "firefox", "ios", "android", "edge", "safari"])
                dialer_tls["utls"] = {"client": utls_client}
                if not dialer_tls.get("serverName"):
                    dialer_tls["serverName"] = "www.google.com"  # fallback spoofed SNI for uTLS
                # When using uTLS/TLS often we don't have valid cert for our own server IP
                dialer_tls["secure"] = False
            elif security_type == "tls":
                if not dialer_tls.get("serverName"):
                    dialer_tls["serverName"] = "www.google.com"
                dialer_tls["secure"] = False
                
            # Anti-DPI custom headers for WebSocket/HTTP
            if gost_type in ["ws", "wss", "mws", "mwss", "http", "https"]:
                headers_dict = {}
                if spec.get("custom_headers"):
                    ch = spec.get("custom_headers")
                    if isinstance(ch, dict):
                        headers_dict.update(ch)
                    elif isinstance(ch, str):
                        for h in ch.split(','):
                            if ':' in h:
                                k, v = h.split(':', 1)
                                headers_dict[k.strip()] = v.strip()
                
                # Default User-Agent if not specified
                user_agent = spec.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                has_ua = any(k.lower() == "user-agent" for k in headers_dict.keys())
                if not has_ua:
                    headers_dict["User-Agent"] = user_agent
                
                dialer_metadata["header"] = headers_dict

            # 2. Rate Limit (Limiter)
            if spec.get("rate_limit_mbps"):
                rate_bytes = int(spec.get("rate_limit_mbps") * 125000) # Mbps to Bytes/sec
                config["limiters"] = [
                    {
                        "name": f"limiter-{tunnel_id}",
                        "limits": [
                            f"{rate_bytes}B"
                        ]
                    }
                ]
                
            dialer = {"type": gost_type}
            if spec.get("bypass_ips"):
                dialer["bypass"] = f"bypass-{tunnel_id}"
            if spec.get("dns_resolvers"):
                dialer["resolver"] = f"resolver-{tunnel_id}"
            
            # keepalive metadata for stability
            dialer_metadata["keepAlive"] = True
            dialer_metadata["keepAliveInterval"] = "25s"
            dialer_metadata["keepAliveTimeout"] = "120s"
            dialer_metadata["timeout"] = "30s"
            dialer_metadata["idleTimeout"] = "0s"
            
            mux_type = spec.get("mux_type") or "yamux"
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                dialer_metadata["mux.type"] = mux_type
                dialer_metadata["nodelay"] = True
            
            if dialer_metadata:
                dialer["metadata"] = dialer_metadata
            if security_type in ["tls", "utls"] and gost_type not in ["udp"]:
                if dialer_tls:
                    dialer["tls"] = dialer_tls
                else:
                    dialer["tls"] = {"secure": False}
                
            # Generate node objects for primary and failover IPs
            hop_nodes = []
            
            connector_metadata = {}
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                connector_metadata["mux.type"] = mux_type
                connector_metadata["nodelay"] = True
                connector_metadata["keepAlive"] = True
            
            connector_type = spec.get("connector_type") or spec.get("handler_type") or "relay"
            connector_primary = {"type": connector_type}
            if auth_token:
                connector_primary["auth"] = {
                    "username": auth_token,
                    "password": ""
                }
            if connector_metadata:
                connector_primary["metadata"] = connector_metadata
            
            # Primary IP node
            hop_nodes.append({
                "name": f"node-{tunnel_id}-primary",
                "addr": target_addr,
                "connector": connector_primary,
                "dialer": dialer
            })
            
            # Failover IPs
            failover_ips = spec.get("failover_ips") or []
            if failover_ips:
                for i, f_ip in enumerate(failover_ips):
                    if not f_ip or not f_ip.strip(): continue
                    f_addr = f"[{f_ip.strip()}]:{control_port}" if ":" in f_ip and not f_ip.startswith("[") else f"{f_ip.strip()}:{control_port}"
                    connector_failover = {"type": connector_type}
                    if auth_token:
                        connector_failover["auth"] = {
                            "username": auth_token,
                            "password": ""
                        }
                    if connector_metadata:
                        connector_failover["metadata"] = connector_metadata

                    hop_nodes.append({
                        "name": f"node-{tunnel_id}-failover-{i+1}",
                        "addr": f_addr,
                        "connector": connector_failover,
                        "dialer": dialer
                    })

            # Create Relay Chain with HA failover selector if failover_ips provided
            hop_obj = {
                "name": f"hop-{tunnel_id}",
                "nodes": hop_nodes
            }
            if failover_ips:
                hop_obj["selector"] = {
                    "strategy": "fifo",
                    "maxFails": 1,
                    "failTimeout": "10s"
                }

            chain_hops = []
            
            # Prepend multi-hop relays before the final node
            relay_hops = spec.get("relay_hops") or []
            for idx, hop_cfg in enumerate(relay_hops):
                if isinstance(hop_cfg, str):
                    h_addr = hop_cfg
                    h_dialer = {"type": "tcp"}
                    h_connector = {"type": "relay"}
                elif isinstance(hop_cfg, dict):
                    h_addr = hop_cfg.get("addr", "")
                    h_dialer = {"type": hop_cfg.get("transport_type", "tcp")}
                    h_connector = {"type": "relay"}
                else:
                    continue
                    
                chain_hops.append({
                    "name": f"hop-{tunnel_id}-relay-{idx}",
                    "nodes": [
                        {
                            "name": f"node-{tunnel_id}-relay-{idx}",
                            "addr": h_addr,
                            "connector": h_connector,
                            "dialer": h_dialer
                        }
                    ]
                })

            chain_hops.append(hop_obj)

            config["chains"].append({
                "name": f"chain-{tunnel_id}",
                "hops": chain_hops
            })
            
            tunnel_proto = spec.get("type", "tcp").lower()
            # If the type is legacy or invalid, default to tcp forwarding
            if tunnel_proto not in ["tcp", "udp", "tcp+udp"]:
                tunnel_proto = "tcp"

            # Create Local Listeners
            default_target_address = '127.0.0.1'
            for port in ports:
                if isinstance(port, dict):
                    local_port = port.get('local_port') or port.get('local')
                    target_address = port.get('target_address', default_target_address)
                    target_port = port.get('target_port') or port.get('remote') or local_port
                    port_num = int(local_port) if isinstance(local_port, (int, str)) and str(local_port).isdigit() else local_port
                    target_port_num = int(target_port) if isinstance(target_port, (int, str)) and str(target_port).isdigit() else target_port
                else:
                    port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                    target_address = default_target_address
                    target_port_num = port_num
                    
                listen_addr = f":{port_num}" if is_reverse else (f"[::]:{port_num}" if use_ipv6 else f"0.0.0.0:{port_num}")
                
                if tunnel_proto in ["tcp", "tcp+udp"]:
                    listener_type = "rtcp" if is_reverse else "tcp"
                    listener_tcp = {"type": listener_type}
                    
                    if is_reverse:
                        listener_tcp["chain"] = f"chain-{tunnel_id}"
                        handler_tcp = {
                            "type": "rtcp"
                        }
                    else:
                        handler_tcp = {
                            "type": "tcp",
                            "chain": f"chain-{tunnel_id}"
                        }
                    
                    service_tcp = {
                        "name": f"tcp-in-{port_num}",
                        "addr": f":{port_num}" if is_reverse else listen_addr,
                        "handler": handler_tcp,
                        "listener": listener_tcp,
                        "forwarder": {
                            "nodes": [
                                {"name": f"target-tcp-{port_num}", "addr": f"{target_address}:{target_port_num}"}
                            ]
                        }
                    }
                    if spec.get("rate_limit_mbps"):
                        service_tcp["limiter"] = f"limiter-{tunnel_id}"
                    
                    config["services"].append(service_tcp)
                
                if tunnel_proto in ["udp", "tcp+udp"]:
                    listener_type = "rudp" if is_reverse else "udp"
                    listener_udp = {"type": listener_type}
                    
                    if is_reverse:
                        listener_udp["chain"] = f"chain-{tunnel_id}"
                        handler_udp = {
                            "type": "rudp"
                        }
                    else:
                        handler_udp = {
                            "type": "udp",
                            "chain": f"chain-{tunnel_id}"
                        }
                    
                    service_udp = {
                        "name": f"udp-in-{port_num}",
                        "addr": f":{port_num}" if is_reverse else listen_addr,
                        "handler": handler_udp,
                        "listener": listener_udp,
                        "forwarder": {
                            "nodes": [
                                {"name": f"target-udp-{port_num}", "addr": f"{target_address}:{target_port_num}"}
                            ]
                        }
                    }
                    if spec.get("rate_limit_mbps"):
                        service_udp["limiter"] = f"limiter-{tunnel_id}"
                    
                    config["services"].append(service_udp)

        # Remove empty blocks
        if not config["chains"]:
            del config["chains"]

        config_file = self.config_dir / f"{tunnel_id}.json"
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        try:
            os.chmod(config_file, 0o600)
        except Exception:
            pass

        # Free all listening ports before starting GOST
        if mode == 'server':
            await free_port(control_port)
        else:
            for p in ports:
                try:
                    await free_port(int(p))
                except Exception:
                    pass
            if is_reverse:
                await free_port(control_port)

        binary_path = self._resolve_binary_path()
        cmd = [str(binary_path), "-C", str(config_file)]
        
        log_file = self.config_dir / f"{tunnel_id}.log"
        log_f = open(log_file, 'w', buffering=1)
        try:
            log_f.write(f"Starting GOST v3 forwarding for tunnel {tunnel_id} (Mode: {mode})\n")
            log_f.write(f"Command: {' '.join(cmd)}\n")
            log_f.flush()
            
            proc = await asyncio.create_subprocess_exec(*cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(self.config_dir),
                start_new_session=True,
                close_fds=False
            )
        except Exception as e:
            log_f.close()
            raise RuntimeError(f"Failed to start GOST: {e}")
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        
        await asyncio.sleep(1.5)
        if proc.returncode is not None:
            stderr = ""
            if log_file.exists():
                with open(log_file, 'r') as f:
                    stderr = f.read()
            if tunnel_id in self.log_handles:
                try:
                    self.log_handles[tunnel_id].close()
                except:
                    pass
                del self.log_handles[tunnel_id]
            raise RuntimeError(f"GOST failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
        
        logger.info(f"GOST v3 forwarding started for tunnel {tunnel_id} (Mode: {mode})")
    
    async def remove(self, tunnel_id: str):
        """Remove GOST tunnel"""
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[f"gost.*{tunnel_id}"])

        config_file = self.config_dir / f"{tunnel_id}.json"
        if config_file.exists():
            try:
                config_file.unlink()
            except Exception:
                pass

        log_file = self.config_dir / f"{tunnel_id}.log"
        if log_file.exists():
            try:
                log_file.unlink()
            except Exception:
                pass
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        return {
            "active": is_running,
            "type": "gost",
            "process_running": is_running
        }


class AdapterManager:
    """Manager for core adapters"""
    
    def __init__(self):
        self.adapters: Dict[str, CoreAdapter] = {
            "rathole": RatholeAdapter(),
            "backhaul": BackhaulAdapter(),
            "chisel": ChiselAdapter(),
            "frp": FrpAdapter(),
            "gost": GostAdapter(),
        }
        self.active_tunnels: Dict[str, CoreAdapter] = {}
        self.config_dir = Path("/var/lib/smite-node")
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Tunnel persistence directory: {self.config_dir} (exists: {self.config_dir.exists()}, writable: {self.config_dir.is_dir()})")
        except Exception as e:
            logger.error(f"Failed to create tunnel persistence directory {self.config_dir}: {e}")
            raise
        self.tunnels_file = self.config_dir / "tunnels.json"
        self.tunnel_configs: Dict[str, Dict[str, Any]] = {}
        logger.info(f"Tunnel persistence file: {self.tunnels_file}")
    
    def get_adapter(self, tunnel_core: str) -> Optional[CoreAdapter]:
        """Get adapter for tunnel core"""
        return self.adapters.get(tunnel_core)
    
    def _load_tunnels(self):
        """Load persisted tunnel configurations"""
        import json
        if self.tunnels_file.exists():
            try:
                file_size = self.tunnels_file.stat().st_size
                logger.info(f"Found tunnel config file at {self.tunnels_file} (size: {file_size} bytes)")
                
                if file_size == 0:
                    logger.warning(f"Tunnel config file {self.tunnels_file} is empty")
                    self.tunnel_configs = {}
                    return
                
                with open(self.tunnels_file, 'r') as f:
                    content = f.read()
                    if not content.strip():
                        logger.warning(f"Tunnel config file {self.tunnels_file} contains only whitespace")
                        self.tunnel_configs = {}
                        return
                    
                    self.tunnel_configs = json.loads(content)
                
                logger.info(f"Loaded {len(self.tunnel_configs)} persisted tunnel configurations from {self.tunnels_file}")
                for tunnel_id, config in self.tunnel_configs.items():
                    core = config.get("core", "unknown")
                    mode = config.get("spec", {}).get("mode", "N/A")
                    logger.info(f"  - Tunnel {tunnel_id}: core={core}, mode={mode}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse tunnel configurations JSON from {self.tunnels_file}: {e}", exc_info=True)
                self.tunnel_configs = {}
            except Exception as e:
                logger.error(f"Failed to load tunnel configurations from {self.tunnels_file}: {e}", exc_info=True)
                self.tunnel_configs = {}
        else:
            logger.info(f"No tunnel configurations file found at {self.tunnels_file} (this is normal for new nodes)")
            self.tunnel_configs = {}
    
    def _save_tunnels(self):
        """Save tunnel configurations to disk"""
        import json
        import os
        try:
            logger.info(f"Saving {len(self.tunnel_configs)} tunnel configurations to {self.tunnels_file}")
            
            temp_file = self.tunnels_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(self.tunnel_configs, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            
            temp_file.replace(self.tunnels_file)
            
            if self.tunnels_file.exists():
                file_size = self.tunnels_file.stat().st_size
                logger.info(f"Successfully saved tunnel configurations to {self.tunnels_file} (size: {file_size} bytes, tunnels: {list(self.tunnel_configs.keys())})")
            else:
                logger.error(f"File {self.tunnels_file} was not created after write operation")
        except Exception as e:
            logger.error(f"Failed to save tunnel configurations to {self.tunnels_file}: {e}", exc_info=True)
    
    async def restore_tunnels(self):
        """Restore all persisted tunnels on startup"""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"Starting tunnel restoration from {self.tunnels_file}")
        logger.info(f"Config directory exists: {self.config_dir.exists()}, writable: {os.access(self.config_dir, os.W_OK) if self.config_dir.exists() else False}")
        logger.info(f"Tunnels file exists: {self.tunnels_file.exists()}")
        
        self._load_tunnels()
        
        if not self.tunnel_configs:
            logger.info("No persisted tunnels to restore")
            return
        
        logger.info(f"Restoring {len(self.tunnel_configs)} persisted tunnels...")
        restored = 0
        failed = 0
        
        for tunnel_id, config in self.tunnel_configs.items():
            try:
                tunnel_core = config.get("core")
                spec = config.get("spec", {})
                
                if not tunnel_core:
                    logger.warning(f"Tunnel {tunnel_id}: Missing core, skipping")
                    failed += 1
                    continue
                
                if not spec:
                    logger.warning(f"Tunnel {tunnel_id}: Empty spec, skipping")
                    failed += 1
                    continue
                
                adapter = self.get_adapter(tunnel_core)
                if not adapter:
                    logger.warning(f"Tunnel {tunnel_id}: Unknown core {tunnel_core}, skipping")
                    failed += 1
                    continue
                
                mode = spec.get('mode', 'N/A')
                logger.info(f"Restoring tunnel {tunnel_id}: core={tunnel_core}, mode={mode}, spec_keys={list(spec.keys())}")
                
                if tunnel_core in ["rathole", "backhaul", "chisel", "frp"] and mode == 'N/A':
                    logger.warning(f"Tunnel {tunnel_id}: Reverse tunnel missing mode field, defaulting to client")
                    spec['mode'] = 'client'
                
                try:
                    await adapter.apply(tunnel_id, spec)
                    self.active_tunnels[tunnel_id] = adapter
                    restored += 1
                    logger.info(f"Successfully restored tunnel {tunnel_id} (core={tunnel_core}, mode={spec.get('mode', 'N/A')})")
                except Exception as apply_error:
                    logger.error(f"Failed to apply tunnel {tunnel_id} during restoration: {apply_error}", exc_info=True)
                    failed += 1
            except Exception as e:
                logger.error(f"Failed to restore tunnel {tunnel_id}: {e}", exc_info=True)
                failed += 1
        
        logger.info(f"Tunnel restoration completed: {restored} restored, {failed} failed")
        self.start_watchdog()

    async def _watchdog_loop(self):
        """Continuous Self-Healing Watchdog: inspects tunnel processes every 15s and auto-recovers dead tunnels"""
        logger.info("AdapterManager self-healing watchdog loop started (interval: 15s)")
        backoff: Dict[str, int] = {}
        while True:
            try:
                await asyncio.sleep(15)
                for tunnel_id in list(self.tunnel_configs.keys()):
                    config = self.tunnel_configs.get(tunnel_id, {})
                    tunnel_core = config.get("core")
                    spec = config.get("spec", {})
                    if not tunnel_core or not spec:
                        continue
                    
                    adapter = self.get_adapter(tunnel_core)
                    if not adapter:
                        continue
                    
                    status = adapter.status(tunnel_id)
                    is_running = status.get("process_running", False) or status.get("active", False)
                    
                    if not is_running:
                        current_delay = backoff.get(tunnel_id, 3)
                        logger.warning(f"Watchdog: tunnel {tunnel_id} ({tunnel_core}) is inactive/dead! Auto-recovering in {current_delay}s...")
                        await asyncio.sleep(current_delay)
                        try:
                            # Cleanly remove old process/sockets and reapply
                            await adapter.remove(tunnel_id)
                            await asyncio.sleep(0.3)
                            await adapter.apply(tunnel_id, spec)
                            self.active_tunnels[tunnel_id] = adapter
                            backoff.pop(tunnel_id, None)
                            logger.info(f"Watchdog: successfully revived and restored tunnel {tunnel_id} ({tunnel_core})")
                        except Exception as e:
                            logger.error(f"Watchdog: failed to auto-recover tunnel {tunnel_id}: {e}")
                            backoff[tunnel_id] = min(current_delay * 2, 120)
                    else:
                        backoff.pop(tunnel_id, None)
            except asyncio.CancelledError:
                logger.info("AdapterManager watchdog loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in AdapterManager watchdog loop: {e}", exc_info=True)

    def start_watchdog(self):
        """Start watchdog background task"""
        if not hasattr(self, '_watchdog_task') or self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logger.info("AdapterManager self-healing watchdog task started")

    def stop_watchdog(self):
        """Stop watchdog background task"""
        if hasattr(self, '_watchdog_task') and self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
    
    async def apply_tunnel(self, tunnel_id: str, tunnel_core: str, spec: Dict[str, Any]):
        """Apply tunnel using appropriate adapter"""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Applying tunnel {tunnel_id}: core={tunnel_core}")
        
        if tunnel_id in self.active_tunnels:
            logger.info(f"Tunnel {tunnel_id} already exists, removing it first")
            await self.remove_tunnel(tunnel_id)
        
        adapter = self.get_adapter(tunnel_core)
        if not adapter:
            error_msg = f"Unknown tunnel core: {tunnel_core}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        logger.info(f"Using adapter: {adapter.name}, mode={spec.get('mode', 'N/A')}")
        await adapter.apply(tunnel_id, spec)
        self.active_tunnels[tunnel_id] = adapter
        
        self.tunnel_configs[tunnel_id] = {
            "core": tunnel_core,
            "spec": spec.copy()
        }
        logger.info(f"Saving tunnel {tunnel_id} to persistent storage (core={tunnel_core}, mode={spec.get('mode', 'N/A')})")
        self._save_tunnels()
        self.start_watchdog()
        logger.info(f"Tunnel {tunnel_id} applied and saved successfully (core={tunnel_core}, mode={spec.get('mode', 'N/A')}, total_saved={len(self.tunnel_configs)})")
    
    async def remove_tunnel(self, tunnel_id: str):
        """Remove tunnel"""
        if tunnel_id in self.active_tunnels:
            adapter = self.active_tunnels[tunnel_id]
            await adapter.remove(tunnel_id)
            del self.active_tunnels[tunnel_id]
        
        if tunnel_id in self.tunnel_configs:
            del self.tunnel_configs[tunnel_id]
            self._save_tunnels()
    
    async def get_tunnel_status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get tunnel status"""
        if tunnel_id in self.active_tunnels:
            adapter = self.active_tunnels[tunnel_id]
            return adapter.status(tunnel_id)
        return {"active": False}
    
    async def cleanup(self):
        """Cleanup all tunnels"""
        self.stop_watchdog()
        for tunnel_id in list(self.active_tunnels.keys()):
            await self.remove_tunnel(tunnel_id)

