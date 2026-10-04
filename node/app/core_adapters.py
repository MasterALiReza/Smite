"""Core adapters for different tunnel types"""
from typing import Protocol, Dict, Any, Optional, List, Set, Iterable, Sequence
from pathlib import Path
import subprocess
import asyncio
import os
import socket
import errno
import psutil
import time
import logging
import signal
import shutil
import threading
import uuid
import re

logger = logging.getLogger(__name__)

def sanitize_config_str(val: Any) -> str:
    """Sanitizes configuration strings by removing newlines and quotes to prevent config injection."""
    if val is None:
        return ""
    return str(val).replace("\r", "").replace("\n", "").replace('"', "").replace("'", "").strip()

def sanitize_spec_for_log(spec: Any) -> Any:
    """Sanitize sensitive fields (tokens, keys, passwords) before logging."""
    if not isinstance(spec, dict):
        return spec
    sensitive_keys = {
        "token", "auth_token", "password", "key", "auth",
        "server_private_key", "client_private_key", "noise_key",
        "server_key", "client_key", "tls_key", "tls_key_pem",
        "tls_pkcs12_password"
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

def sanitize_cmd_for_log(cmd: List[str]) -> str:
    """Mask credential flags in command argument lists before logging."""
    sanitized = []
    skip_next = False
    for i, arg in enumerate(cmd):
        if skip_next:
            sanitized.append("***REDACTED***")
            skip_next = False
            continue
        if arg in ("--auth", "--key", "-token", "--token", "-k", "-secret"):
            sanitized.append(arg)
            skip_next = True
        elif ":" in arg and (i > 0 and cmd[i - 1] in ("--auth", "-u")):
            sanitized.append("***REDACTED***")
        else:
            sanitized.append(arg)
    return " ".join(sanitized)

def _find_pids_by_port_procfs(port: int) -> Set[int]:
    """Find process IDs holding a port by scanning Linux /proc net entries and fds directly."""
    hex_p = f"{port:04X}"
    inodes: Set[str] = set()
    for proto in ["tcp", "tcp6", "udp", "udp6"]:
        path = f"/proc/net/{proto}"
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 10:
                        local_addr = parts[1]
                        if ":" in local_addr and local_addr.split(":")[1].upper() == hex_p:
                            inode = parts[9]
                            if inode != "0":
                                inodes.add(inode)
        except Exception:
            pass
    
    if not inodes:
        return set()
    
    pids: Set[int] = set()
    if os.path.exists("/proc"):
        try:
            for proc_entry in os.scandir("/proc"):
                if proc_entry.is_dir() and proc_entry.name.isdigit():
                    pid = int(proc_entry.name)
                    fd_dir = f"/proc/{pid}/fd"
                    try:
                        for fd_entry in os.scandir(fd_dir):
                            try:
                                target = os.readlink(fd_entry.path)
                                for inode in inodes:
                                    if f"[{inode}]" in target:
                                        pids.add(pid)
                                        break
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass
    return pids


ALLOWED_CORE_BINARIES = {"rathole", "backhaul", "frps", "frpc", "gost", "chisel"}


def _is_safe_core_process(pid: int) -> bool:
    """Check if a process belongs to a known proxy core binary before terminating."""
    try:
        p = psutil.Process(pid)
        name = p.name().lower()
        if any(c in name for c in ALLOWED_CORE_BINARIES):
            return True
        cmdline = " ".join(p.cmdline()).lower()
        if any(c in cmdline for c in ALLOWED_CORE_BINARIES):
            return True
    except Exception:
        pass
    return False


def _get_pid_dir() -> Path:
    base = Path("/var/lib/smite-node/pids")
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        fallback = Path("./data/pids")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _save_tunnel_pid(tunnel_id: str, pid: int) -> None:
    """Save running tunnel process PID to disk for zero-downtime adoption across restarts."""
    try:
        pdir = _get_pid_dir()
        (pdir / f"{tunnel_id}.pid").write_text(str(pid))
    except Exception as e:
        logger.debug(f"Failed to save PID for tunnel {tunnel_id}: {e}")


def _remove_tunnel_pid(tunnel_id: str) -> None:
    """Remove tunnel PID record from disk."""
    try:
        pdir = _get_pid_dir()
        pid_file = pdir / f"{tunnel_id}.pid"
        if pid_file.exists():
            pid_file.unlink()
    except Exception:
        pass


def _get_tunnel_pid(tunnel_id: str) -> Optional[int]:
    """Retrieve recorded PID for a tunnel."""
    try:
        pdir = _get_pid_dir()
        pid_file = pdir / f"{tunnel_id}.pid"
        if pid_file.exists():
            content = pid_file.read_text().strip()
            if content.isdigit():
                return int(content)
    except Exception:
        pass
    return None


def _is_tunnel_pid_alive(tunnel_id: str, core_name: Optional[str] = None) -> bool:
    """Check if the detached tunnel process is still active and healthy on the host."""
    pid = _get_tunnel_pid(tunnel_id)
    if not pid:
        return False
    try:
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        if not _is_safe_core_process(pid):
            return False
        cmdline = " ".join(p.cmdline()).lower()
        if tunnel_id.lower() in cmdline or (core_name and core_name.lower() in p.name().lower()):
            return True
        return False
    except Exception:
        return False



def _extract_ports_from_spec_dict(spec: Dict[str, Any]) -> Set[int]:
    """Helper to extract all listening and service ports from a tunnel spec dictionary."""
    extracted: Set[int] = set()
    if not isinstance(spec, dict):
        return extracted

    # 1. ports array or comma-separated string
    raw_ports = spec.get("ports") or []
    if isinstance(raw_ports, str):
        raw_ports = [p.strip() for p in raw_ports.split(",") if p.strip()]
    elif not isinstance(raw_ports, list):
        raw_ports = [raw_ports]

    for p in raw_ports:
        if isinstance(p, int) and 0 < p <= 65535:
            extracted.add(p)
        elif isinstance(p, str):
            p_clean = p.strip()
            if "=" in p_clean:
                p_clean = p_clean.split("=", 1)[0].strip()
            if ":" in p_clean:
                p_clean = p_clean.rsplit(":", 1)[-1].strip()
            if "/" in p_clean:
                p_clean = p_clean.split("/", 1)[0].strip()
            if p_clean.isdigit():
                val = int(p_clean)
                if 0 < val <= 65535:
                    extracted.add(val)
        elif isinstance(p, dict):
            for k in ("port", "listen_port", "remote_port", "local_port", "remote", "local"):
                v = p.get(k)
                if v and str(v).isdigit() and 0 < int(v) <= 65535:
                    extracted.add(int(v))

    # 2. explicit port keys
    for k in ("port", "listen_port", "remote_port", "proxy_port", "local_port", "bind_port", "control_port", "server_port"):
        val = spec.get(k)
        if val and str(val).isdigit() and 0 < int(val) <= 65535:
            extracted.add(int(val))

    # 3. bind_addr / remote_addr / server_addr
    for k in ("bind_addr", "remote_addr", "server_addr", "target_addr"):
        val = str(spec.get(k, ""))
        if ":" in val:
            port_part = val.rsplit(":", 1)[-1].strip().strip("]")
            if port_part.isdigit() and 0 < int(port_part) <= 65535:
                extracted.add(int(port_part))

    # 4. port ranges
    pranges = spec.get("port_ranges") or []
    if isinstance(pranges, str):
        pranges = [x.strip() for x in pranges.split(",") if x.strip()]
    if isinstance(pranges, list):
        for pr in pranges:
            if isinstance(pr, str) and "-" in pr:
                parts = pr.split("-")
                if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                    s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                    if 0 < s_p <= e_p <= 65535 and (e_p - s_p) <= 500:
                        extracted.update(range(s_p, e_p + 1))

    return extracted


async def free_ports(ports: Iterable[Any]) -> None:
    """Safely terminate any core proxy process holding any of the specified ports in a single batch pass."""
    if not ports:
        return
    target_ports: Set[int] = set()
    for p in ports:
        if not p:
            continue
        try:
            p_int = int(p)
            if 0 < p_int <= 65535:
                target_ports.add(p_int)
        except (ValueError, TypeError):
            continue
    if not target_ports:
        return

    current_pid = os.getpid()
    ignored_pids = {current_pid, os.getppid(), 1}

    # Method 1: Linux /proc/net + /proc/{pid}/fd scan
    for port_num in target_ports:
        try:
            pids = _find_pids_by_port_procfs(port_num)
            for pid in pids:
                if pid not in ignored_pids and _is_safe_core_process(pid):
                    logger.warning(f"Kernel socket scan: terminating core process {pid} holding port {port_num}")
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Error in procfs port scan for port {port_num}: {e}")

    # Method 2: Single-pass psutil process and connection scanning
    try:
        for p in psutil.process_iter(['pid', 'name']):
            if p.pid in ignored_pids:
                continue
            try:
                if not _is_safe_core_process(p.pid):
                    continue
                for conn in p.net_connections(kind='all'):
                    if conn.laddr and conn.laddr.port in target_ports:
                        logger.warning(f"psutil: Terminating core process {p.pid} ({p.name()}) holding port {conn.laddr.port}")
                        p.kill()
                        try:
                            p.wait(timeout=0.5)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Error freeing ports {target_ports} via psutil: {e}")

    # Method 3: Tear down lingering half-open kernel TCP sockets on Linux via ss -K (SOCK_DESTROY)
    if os.name == 'posix':
        for port_num in target_ports:
            try:
                proc_kill = await asyncio.create_subprocess_exec(
                    "ss", "-K", f"( sport = :{port_num} or dport = :{port_num} )",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await asyncio.wait_for(proc_kill.wait(), timeout=0.8)
            except Exception:
                pass


async def free_port(port: Optional[Any]) -> None:
    """Safely terminate any core proxy process holding the specified port without affecting system or node services."""
    if port:
        await free_ports([port])


async def safe_stop_subprocess(
    proc: Optional[asyncio.subprocess.Process] = None,
    patterns: Optional[List[str]] = None,
    timeout: float = 3.0,
    pid: Optional[int] = None
) -> None:
    """
    Safely and thoroughly stop a subprocess and any associated process group or orphan processes.
    Uses process group signaling, direct PID termination, psutil process-table scanning, and fallback pattern killing.
    """
    target_pid = proc.pid if proc is not None else pid
    current_pid = os.getpid()
    ignored_pids = {current_pid, os.getppid(), 1}

    if target_pid and target_pid not in ignored_pids and psutil.pid_exists(target_pid):
        if _is_safe_core_process(target_pid):
            if os.name == 'posix':
                try:
                    pgid = os.getpgid(target_pid)
                    my_pgid = os.getpgid(current_pid)
                    if pgid != my_pgid and pgid > 1:
                        os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception as e:
                    logger.debug(f"Error terminating pgid for pid {target_pid}: {e}")

            try:
                if proc is not None and proc.returncode is None:
                    proc.terminate()
                else:
                    os.kill(target_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception:
                pass

            wait_success = False
            if proc is not None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=timeout)
                    wait_success = True
                except (asyncio.TimeoutError, Exception):
                    wait_success = False
            else:
                for _ in range(int(timeout * 10)):
                    await asyncio.sleep(0.1)
                    if not psutil.pid_exists(target_pid):
                        wait_success = True
                        break

            if not wait_success and psutil.pid_exists(target_pid):
                if os.name == 'posix':
                    try:
                        pgid = os.getpgid(target_pid)
                        my_pgid = os.getpgid(current_pid)
                        if pgid != my_pgid and pgid > 1:
                            os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    except Exception:
                        pass
                try:
                    if proc is not None:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=1.5)
                    else:
                        os.kill(target_pid, signal.SIGKILL)
                except Exception:
                    pass

    if patterns:
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            if p.pid in ignored_pids:
                continue
            try:
                if not _is_safe_core_process(p.pid):
                    continue
                cmdline_str = " ".join(p.info.get('cmdline') or [])
                for pat in patterns:
                    if pat and str(pat).strip() in cmdline_str:
                        logger.info(f"Terminating orphan core process {p.pid} matching '{pat}'")
                        p.kill()
                        try:
                            p.wait(timeout=0.5)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                pass

async def _spawn_core_subprocess(
    cmd: List[str],
    stdout: Any = None,
    stderr: Any = None,
    env: Optional[Dict[str, str]] = None,
) -> asyncio.subprocess.Process:
    """Spawns a core process with a dedicated process group so signals never hit uvicorn"""
    if stdout is None:
        stdout = subprocess.DEVNULL
    if stderr is None:
        stderr = subprocess.DEVNULL
    kwargs = {"stdout": stdout, "stderr": stderr}
    if env is not None:
        kwargs["env"] = env
    if os.name == 'posix':
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True
    return await asyncio.create_subprocess_exec(*cmd, **kwargs)


def is_port_listening_locally(port: int, proto: str = "any") -> bool:
    """Check if port is actively listening in Linux kernel /proc/net, psutil, or via socket probe"""
    if not port or int(port) <= 0:
        return False
    
    port_int = int(port)
    port_hex = f"{port_int:04X}"
    
    # 1. Check POSIX /proc/net tables (Ultra-fast, container-friendly)
    files_to_check = []
    if proto in ["tcp", "any"]:
        files_to_check.extend(["/proc/net/tcp", "/proc/net/tcp6"])
    if proto in ["udp", "any"]:
        files_to_check.extend(["/proc/net/udp", "/proc/net/udp6"])
        
    for pfile in files_to_check:
        try:
            if os.path.exists(pfile):
                with open(pfile, "r") as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            local_addr = parts[1]
                            state = parts[3]
                            if local_addr.endswith(f":{port_hex}"):
                                if "tcp" in pfile:
                                    if state == "0A":  # TCP_LISTEN
                                        return True
                                else:
                                    return True
        except Exception:
            pass
            
    # 2. psutil check (cross-platform)
    try:
        import psutil
        for c in psutil.net_connections(kind='inet'):
            if c.laddr and c.laddr.port == port_int:
                if proto == "tcp" and c.type == socket.SOCK_STREAM and c.status == psutil.CONN_LISTEN:
                    return True
                elif proto == "udp" and c.type == socket.SOCK_DGRAM:
                    return True
                elif proto == "any":
                    return True
    except Exception:
        pass
        
    # 3. Direct socket probe fallback (cross-platform Windows & Linux)
    if proto in ["tcp", "any"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.1)
            res = s.connect_ex(('127.0.0.1', port_int))
            s.close()
            if res == 0:
                return True
        except Exception:
            pass
            
    if proto in ["udp", "any"]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind(('127.0.0.1', port_int))
                s.close()
            except OSError as err:
                s.close()
                import errno
                if err.errno in (getattr(errno, 'EADDRINUSE', 98), 10048, getattr(errno, 'EACCES', 13)):
                    return True
        except Exception:
            pass

    return False


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


def format_address_port(host: str, port: Optional[int] = None) -> str:
    """Format host and port into address:port string, handling IPv6 addresses."""
    if not host:
        return ""
    import ipaddress
    clean_host = host.strip("[]")
    try:
        ipaddress.IPv6Address(clean_host)
        if port is not None:
            return f"[{clean_host}]:{port}"
        return f"[{clean_host}]"
    except (ValueError, ipaddress.AddressValueError):
        if port is not None:
            return f"{host}:{port}"
        return host


class CoreAdapter(Protocol):
    """Protocol for core adapters"""
    name: str
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Apply tunnel configuration"""
        ...
    
    async def remove(self, tunnel_id: str, purge: bool = False) -> None:
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
        self.log_handles = {}
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Rathole tunnel - supports both server and client modes"""
        # Always remove any previous or orphan instance for this tunnel before applying
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)
        
        mode = spec.get('mode', 'client')
        
        transport = (spec.get('transport_type') or spec.get('transport') or 'tcp').lower()
        tunnel_type = (spec.get('tunnel_type') or spec.get('type') or 'tcp').lower()
        if transport in ['ws', 'websocket', 'wss']:
            use_websocket = True
            use_noise = False
        elif transport == 'noise':
            use_websocket = False
            use_noise = True
        else:
            use_websocket = False
            use_noise = False

        websocket_tls = (transport == 'wss') or bool(spec.get('websocket_tls', False) or spec.get('tls', False))
        
        if mode == 'server':
            bind_addr = spec.get('bind_addr', '0.0.0.0:23333')
            token = spec.get('token', '').strip()
            
            ports = spec.get('ports') or []
            if not ports:
                proxy_port = spec.get('proxy_port') or spec.get('remote_port') or spec.get('listen_port')
                if proxy_port:
                    ports = [int(proxy_port) if isinstance(proxy_port, (int, str)) and str(proxy_port).isdigit() else proxy_port]
            
            token = sanitize_config_str(token)
            if not token:
                raise ValueError("Rathole server requires 'token' in spec")
            if not ports:
                raise ValueError("Rathole server requires 'ports' array or 'proxy_port'/'remote_port' in spec")
            
            bind_host, bind_port, is_ipv6 = parse_address_port(bind_addr)
            if not bind_port:
                bind_host = "0.0.0.0"
                bind_port = 23333
            
            # Forcefully free control port and all service ports before spawning
            await free_port(bind_port)
            for p in ports:
                await free_port(p)
            
            bind_addr_str = f"[{bind_host}]:{bind_port}" if (is_ipv6 and not bind_host.startswith("[")) else f"{bind_host}:{bind_port}"
            config = f"""[server]
bind_addr = "{bind_addr_str}"
default_token = "{token}"
heartbeat_interval = 10
"""
            
            if use_noise:
                local_priv = sanitize_config_str(spec.get('server_private_key') or spec.get('local_private_key', ''))
                remote_pub = sanitize_config_str(spec.get('client_public_key') or spec.get('remote_public_key', ''))
                config += f"""
[server.transport]
type = "noise"

[server.transport.noise]
pattern = "Noise_KK_25519_ChaChaPoly_BLAKE2s"
local_private_key = "{local_priv}"
remote_public_key = "{remote_pub}"
"""
            elif use_websocket:
                tls_val = "true" if websocket_tls else "false"
                config += f"""
[server.transport]
type = "websocket"

[server.transport.websocket]
tls = {tls_val}
"""
                if websocket_tls:
                    pfx_path = self.config_dir / f"{tunnel_id}.pfx"
                    pfx_b64 = spec.get('tls_pkcs12_b64')
                    pfx_pwd = spec.get('tls_pkcs12_password', '')
                    if pfx_b64:
                        import base64
                        pfx_bytes = base64.b64decode(pfx_b64)
                        with open(pfx_path, "wb") as pf:
                            pf.write(pfx_bytes)
                        try:
                            os.chmod(pfx_path, 0o600)
                        except Exception:
                            pass
                    elif not pfx_path.exists():
                        raise ValueError(f"Rathole server in WSS mode requires 'tls_pkcs12_b64' or {pfx_path}")

                    config += f"""
[server.transport.tls]
pkcs12 = "{pfx_path}"
pkcs12_password = "{sanitize_config_str(pfx_pwd)}"
"""
            
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
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config)
            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass
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
            elif transport in ['ws', 'websocket', 'wss']:
                use_websocket = True
                if transport == 'wss':
                    websocket_tls = True
            
            token = sanitize_config_str(token)
            config = f"""[client]
remote_addr = "{remote_addr}"
default_token = "{token}"
heartbeat_timeout = 40
retry_interval = 1
"""
            
            if use_noise:
                local_priv = sanitize_config_str(spec.get('client_private_key') or spec.get('local_private_key', ''))
                remote_pub = sanitize_config_str(spec.get('server_public_key') or spec.get('remote_public_key', ''))
                config += f"""
[client.transport]
type = "noise"

[client.transport.noise]
pattern = "Noise_KK_25519_ChaChaPoly_BLAKE2s"
local_private_key = "{local_priv}"
remote_public_key = "{remote_pub}"
"""
            elif use_websocket:
                tls_val = "true" if websocket_tls else "false"
                config += f"""
[client.transport]
type = "websocket"

[client.transport.websocket]
tls = {tls_val}
"""
                if websocket_tls:
                    ca_pem = spec.get('tls_ca_cert_pem')
                    ca_path = self.config_dir / f"{tunnel_id}_ca.crt"
                    if ca_pem:
                        with open(ca_path, "w", encoding="utf-8") as cf:
                            cf.write(ca_pem.strip() + "\n")
                        try:
                            os.chmod(ca_path, 0o600)
                        except Exception:
                            pass

                    sni = sanitize_config_str(
                        spec.get('custom_sni')
                        or spec.get('stealth_domain')
                        or spec.get('hostname')
                        or (remote_addr.split(':')[0].strip('[]') if ':' in remote_addr else remote_addr)
                    )

                    config += f"""
[client.transport.tls]
"""
                    if ca_path.exists():
                        config += f'trusted_root = "{ca_path}"\n'
                    if sni:
                        config += f'hostname = "{sni}"\n'
            
            target_host = spec.get("target_host") or spec.get("local_host") or "127.0.0.1"
            # Create multiple service sections for multiple ports
            for i, port in enumerate(ports):
                port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                base_service_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id
                local_addr = f"{target_host}:{port_num}"
                
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
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config)
            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass
            
        mode_flag = "-s" if mode == 'server' else "-c"
        log_path = self.config_dir / f"{tunnel_id}.log"
        log_fh = log_path.open("w", buffering=1)
        log_fh.write(f"Starting Rathole ({mode}) for tunnel {tunnel_id}\n")
        log_fh.flush()

        env = os.environ.copy()
        if "RUST_LOG" not in env:
            env["RUST_LOG"] = "warn"

        bin_path = "/usr/local/bin/rathole"
        if not os.path.exists(bin_path):
            found_bin = shutil.which("rathole")
            if found_bin:
                bin_path = found_bin

        cmd = [str(bin_path), mode_flag, str(config_path)]
        try:
            proc = await _spawn_core_subprocess(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env
            )
        except Exception:
            log_fh.close()
            raise

        self.processes[tunnel_id] = proc
        self.log_handles[tunnel_id] = log_fh
        _save_tunnel_pid(tunnel_id, proc.pid)

        await asyncio.sleep(0.5)
        if proc.returncode is not None:
            _remove_tunnel_pid(tunnel_id)
            error_output = ""
            try:
                error_output = log_path.read_text(encoding="utf-8")[-1000:]
            except Exception:
                pass
            if tunnel_id in self.log_handles:
                try:
                    self.log_handles[tunnel_id].close()
                except Exception:
                    pass
                del self.log_handles[tunnel_id]
            raise RuntimeError(f"rathole failed to start: {error_output}")
    
    async def remove(self, tunnel_id: str, purge: bool = False):
        """Remove Rathole tunnel and release bound ports"""
        pid = _get_tunnel_pid(tunnel_id)
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        ports_to_free: Set[int] = set()
        if config_path.exists():
            try:
                content = config_path.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'(?:bind_addr|remote_addr|local_addr)\s*=\s*["\']?(?:[^"\':\s]*:)?(\d{1,5})["\']?', content, re.IGNORECASE):
                    p_val = int(m.group(1))
                    if 0 < p_val <= 65535:
                        ports_to_free.add(p_val)
            except Exception:
                pass

        await safe_stop_subprocess(proc, patterns=[tunnel_id, f"{tunnel_id}.toml"], pid=pid)
        _remove_tunnel_pid(tunnel_id)
            
        if config_path.exists():
            try:
                config_path.unlink()
            except Exception:
                pass
        pfx_path = self.config_dir / f"{tunnel_id}.pfx"
        if pfx_path.exists():
            try:
                pfx_path.unlink()
            except Exception:
                pass
        ca_path = self.config_dir / f"{tunnel_id}_ca.crt"
        if ca_path.exists():
            try:
                ca_path.unlink()
            except Exception:
                pass

        for lf_name in [f"{tunnel_id}.log", f"{tunnel_id}.log.old"]:
            lf = self.config_dir / lf_name
            if lf.exists() and purge:
                try:
                    lf.unlink()
                except Exception:
                    pass

        if ports_to_free:
            await free_ports(ports_to_free)
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        config_path = self.config_dir / f"{tunnel_id}.toml"
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "rathole")
        
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
        "heartbeat",
        "channel_size",
        "insecure",
    ]

    BACKHAUL_NUMERIC_KEYS = {
        "keepalive_period", "heartbeat", "channel_size", "mux_con", "web_port",
        "mss", "so_rcvbuf", "so_sndbuf", "mux_version", "mux_framesize",
        "mux_recievebuffer", "mux_streambuffer", "connection_pool", "retry_interval",
        "dial_timeout"
    }

    BACKHAUL_BOOLEAN_KEYS = {
        "nodelay", "skip_optz", "sniffer", "proxy_protocol", "aggressive_pool", "accept_udp", "insecure"
    }

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
            Path("/usr/local/bin/backhaul"),
            Path("/usr/bin/backhaul"),
            Path("/opt/backhaul/backhaul"),
            Path("backhaul"),
        ]

    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Backhaul tunnel - supports both server and client modes"""
        if tunnel_id in self.processes or _is_tunnel_pid_alive(tunnel_id, "backhaul"):
            logger.info(f"Backhaul tunnel {tunnel_id} already exists or running via PID, removing it first")
            await self.remove(tunnel_id)

        mode = spec.get('mode', 'client')

        if mode == 'server':
            raw_transport = (spec.get("transport") or spec.get("type") or "tcpmux").lower()
            is_pure_udp = raw_transport == "udp"
            is_udp_over_tcp = (
                spec.get("accept_udp") is True
                or (spec.get("type") in ("udp", "tcp+udp"))
                or (spec.get("tunnel_type") in ("udp", "tcp+udp"))
                or is_pure_udp
            )
            if is_pure_udp:
                transport = "tcp"
            elif raw_transport in {"tcp", "ws", "wss", "wsmux", "wssmux", "tcpmux"}:
                transport = raw_transport
            else:
                transport = "tcpmux"

            server_options = dict(spec.get("server_options") or {})
            bind_addr = spec.get("bind_addr")
            if not bind_addr:
                control_port = spec.get("control_port") or spec.get("listen_port") or 3080
                bind_ip = spec.get("bind_ip", "0.0.0.0")
                bind_addr = format_address_port(bind_ip, control_port)
            else:
                bind_h, bind_p, _ = parse_address_port(bind_addr)
                if bind_h and bind_p:
                    bind_addr = format_address_port(bind_h, bind_p)

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

            target_host = spec.get("target_host", "127.0.0.1")
            if isinstance(ports, list):
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
                            # Port range: Backhaul Go binary forwards range directly without host prefix
                            processed_ports.append(p_clean)
                        else:
                            processed_ports.append(p_clean)
                    elif isinstance(p, (int, float)):
                        port_int = int(p)
                        processed_ports.append(f"{port_int}={target_host}:{port_int}")
                    elif isinstance(p, dict):
                        local = p.get("local") or p.get("listen_port") or p.get("public_port")
                        tgt_host = p.get("target_host") or target_host
                        tgt_port = p.get("target_port") or p.get("remote_port") or local
                        if local:
                            processed_ports.append(f"{local}={tgt_host}:{tgt_port}")
                    else:
                        processed_ports.append(str(p).strip())
                ports = processed_ports
            else:
                ports = [str(ports).strip()] if ports else []

            # Batch port freeing on server node
            ports_to_free: Set[int] = set()
            _, bind_port_num, _ = parse_address_port(bind_addr)
            if bind_port_num:
                ports_to_free.add(bind_port_num)

            for p_entry in ports:
                p_str = str(p_entry).strip()
                lp = p_str.split("=")[0].strip() if "=" in p_str else p_str
                if lp.isdigit():
                    ports_to_free.add(int(lp))
                elif "-" in lp:
                    parts = lp.split("-")
                    if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        s_p, e_p = int(parts[0].strip()), int(parts[1].strip())
                        if s_p <= e_p:
                            check_count = min(e_p - s_p + 1, 64)
                            for port_num in range(s_p, s_p + check_count):
                                ports_to_free.add(port_num)

            if ports_to_free:
                await free_ports(ports_to_free)

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
                "tls_cert", "tls_key", "sniffer", "web_port", "proxy_protocol",
                "mss", "so_rcvbuf", "so_sndbuf", "mux_version", "mux_framesize",
                "mux_recievebuffer", "mux_streambuffer"
            ]
            for key in SERVER_OPTION_KEYS:
                value = server_options.get(key)
                if value is None or value == "":
                    value = spec.get(key)
                if value is not None and value != "":
                    if key in self.BACKHAUL_NUMERIC_KEYS:
                        try:
                            value = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key in self.BACKHAUL_BOOLEAN_KEYS:
                        if isinstance(value, str):
                            value = value.lower() in ("true", "1", "yes")
                        else:
                            value = bool(value)
                    server_config[key] = value

            if is_udp_over_tcp:
                server_config["accept_udp"] = True

            kp = server_options.get("keepalive_period") or spec.get("keepalive_period")
            if not kp or not isinstance(kp, (int, float)) or kp > 25:
                server_config["keepalive_period"] = 20
            else:
                server_config["keepalive_period"] = int(kp)

            hb = server_options.get("heartbeat") or spec.get("heartbeat")
            if not hb or not isinstance(hb, (int, float)) or hb > 25:
                server_config["heartbeat"] = 20
            else:
                server_config["heartbeat"] = int(hb)

            server_config.setdefault("nodelay", True)

            # Write TLS certificates if provided as in-memory PEM
            if spec.get("tls_cert_pem") and spec.get("tls_key_pem"):
                cert_path = self.config_dir / f"{tunnel_id}_cert.pem"
                key_path = self.config_dir / f"{tunnel_id}_key.pem"
                await asyncio.to_thread(cert_path.write_text, spec["tls_cert_pem"], "utf-8")
                await asyncio.to_thread(key_path.write_text, spec["tls_key_pem"], "utf-8")
                try:
                    os.chmod(key_path, 0o600)
                except Exception:
                    pass
                server_config["tls_cert"] = str(cert_path)
                server_config["tls_key"] = str(key_path)

            config_path = self.config_dir / f"{tunnel_id}.toml"
            rendered_cfg = self._render_toml({"server": server_config})
            await asyncio.to_thread(config_path.write_text, rendered_cfg, "utf-8")
            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass

            binary_path = self._resolve_binary_path()
            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
                try:
                    log_path.write_text("", encoding="utf-8")
                except Exception:
                    pass
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul server for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml(sanitize_spec_for_log({"server": server_config})))
            log_fh.flush()

            try:
                proc = await _spawn_core_subprocess(
                    [str(binary_path), "-c", str(config_path)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
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

            raw_transport = (spec.get("transport") or spec.get("type") or "tcp").lower()
            is_pure_udp = raw_transport == "udp"
            is_udp_over_tcp = (
                spec.get("accept_udp") is True
                or (spec.get("type") in ("udp", "tcp+udp"))
                or (spec.get("tunnel_type") in ("udp", "tcp+udp"))
                or is_pure_udp
            )
            if is_pure_udp:
                transport = "tcp"
            elif raw_transport in {"tcp", "ws", "wss", "wsmux", "wssmux", "tcpmux"}:
                transport = raw_transport
            else:
                transport = "tcpmux"
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
                if key in self.BACKHAUL_NUMERIC_KEYS:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        pass
                elif key in self.BACKHAUL_BOOLEAN_KEYS:
                    if isinstance(value, str):
                        value = value.lower() in ("true", "1", "yes")
                    else:
                        value = bool(value)
                config_dict[key] = value

            if is_udp_over_tcp:
                config_dict["accept_udp"] = True

            if transport in ("wss", "wssmux") or spec.get("insecure") or client_options.get("insecure"):
                config_dict.setdefault("insecure", True)

            config_dict.setdefault("nodelay", True)

            if "connection_pool" not in config_dict:
                config_dict["connection_pool"] = 8
            if "retry_interval" not in config_dict:
                config_dict["retry_interval"] = 3
            if "dial_timeout" not in config_dict:
                config_dict["dial_timeout"] = 10

            kp = client_options.get("keepalive_period") or spec.get("keepalive_period")
            if not kp or not isinstance(kp, (int, float)) or kp > 25:
                config_dict["keepalive_period"] = 20
            else:
                config_dict["keepalive_period"] = int(kp)

            hb = client_options.get("heartbeat") or spec.get("heartbeat")
            if not hb or not isinstance(hb, (int, float)) or hb > 25:
                config_dict["heartbeat"] = 20
            else:
                config_dict["heartbeat"] = int(hb)

            if "aggressive_pool" not in config_dict:
                config_dict["aggressive_pool"] = True

            config_path = self.config_dir / f"{tunnel_id}.toml"
            rendered_cfg = self._render_toml({"client": config_dict})
            await asyncio.to_thread(config_path.write_text, rendered_cfg, "utf-8")
            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass

            binary_path = self._resolve_binary_path()

            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
                try:
                    log_path.write_text("", encoding="utf-8")
                except Exception:
                    pass
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul client for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml({"client": sanitize_spec_for_log(config_dict)}))
            log_fh.flush()

            try:
                proc = await _spawn_core_subprocess(
                    [str(binary_path), "-c", str(config_path)],
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                log_fh.close()
                raise

        # Track process and PID immediately to prevent zombie leak on cancellation
        self.processes[tunnel_id] = proc
        _save_tunnel_pid(tunnel_id, proc.pid)
        self.log_handles[tunnel_id] = log_fh

        try:
            await asyncio.sleep(0.5)
            if proc.returncode is not None:
                error_output = ""
                try:
                    error_output = log_path.read_text(encoding="utf-8")[-1000:]
                except Exception:
                    pass
                raise RuntimeError(f"backhaul failed to start: {error_output}")
        except BaseException:
            await self.remove(tunnel_id)
            raise

    async def remove(self, tunnel_id: str, purge: bool = False):
        """Remove Backhaul tunnel and release bound ports"""
        pid = _get_tunnel_pid(tunnel_id)
        config_path = self.config_dir / f"{tunnel_id}.toml"
        cert_path = self.config_dir / f"{tunnel_id}_cert.pem"
        key_path = self.config_dir / f"{tunnel_id}_key.pem"
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        ports_to_free: Set[int] = set()
        if config_path.exists():
            try:
                txt = config_path.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'(?:bind_addr|remote_addr|local_addr)\s*=\s*["\']?(?:[^"\':\s]*:)?(\d{1,5})["\']?', txt, re.IGNORECASE):
                    p_val = int(m.group(1))
                    if 0 < p_val <= 65535:
                        ports_to_free.add(p_val)
                for m in re.finditer(r'["\'](\d{1,5})/(?:tcp|udp)["\']', txt, re.IGNORECASE):
                    p_val = int(m.group(1))
                    if 0 < p_val <= 65535:
                        ports_to_free.add(p_val)
            except Exception:
                pass

        await safe_stop_subprocess(proc, patterns=[f"{tunnel_id}.toml"], pid=pid)
        _remove_tunnel_pid(tunnel_id)

        for p in (config_path, cert_path, key_path):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        for lf_name in [f"backhaul_{tunnel_id}.log", f"backhaul_{tunnel_id}.log.old"]:
            lf = self.config_dir / lf_name
            if lf.exists() and purge:
                try:
                    lf.unlink()
                except Exception:
                    pass

        if ports_to_free:
            await free_ports(ports_to_free)

    def status(self, tunnel_id: str) -> Dict[str, Any]:
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.get(tunnel_id)
        is_running = proc is not None and proc.returncode is None
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "backhaul")
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
                escaped_items = []
                for item in value:
                    item_str = str(item).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')
                    escaped_items.append(f'"{item_str}"')
                rendered = ",\n  ".join(escaped_items)
                return "[\n  " + rendered + "\n]"
            value_str = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n')
            return f'"{value_str}"'

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
        """Apply Chisel tunnel - supports both server and client modes with TLS, UDP, Camouflage and Persistent Keys"""
        mode = spec.get('mode', 'client')
        binary_path = self._resolve_binary_path()
        transport = (spec.get('transport_type') or spec.get('transport') or 'ws').lower()
        use_tls = (transport in ['wss', 'https', 'tls']) or bool(spec.get('websocket_tls', False) or spec.get('tls', False))
        tunnel_proto = (spec.get('type') or spec.get('tunnel_type') or 'tcp').lower()
        is_reverse = spec.get('is_reverse', True)
        if is_reverse is None:
            is_reverse = True
        else:
            is_reverse = bool(is_reverse)

        # Early validation BEFORE removing running process
        if mode == 'client':
            server_url = spec.get('server_url') or spec.get('remote_addr') or spec.get('server_addr')
            if not server_url:
                raise ValueError("Chisel client requires 'server_url' or 'remote_addr' in spec")
            ports = spec.get('ports') or []
            local_port = spec.get('local_port') or spec.get('port')
            remote_port = spec.get('remote_port')
            if not ports and not local_port and tunnel_proto != 'socks5':
                raise ValueError("Chisel client requires at least one port mapping")

        if tunnel_id in self.processes:
            logger.info(f"Chisel tunnel {tunnel_id} already exists, removing it first")
            await self.remove(tunnel_id)
        
        if mode == 'server':
            control_port = spec.get('control_port') or spec.get('server_port') or spec.get('listen_port') or 8080
            # Free control port and any reverse service ports server will bind
            ports_to_free = [int(control_port)]
            if is_reverse:
                for p in spec.get('ports') or []:
                    if isinstance(p, (int, str)) and str(p).isdigit():
                        ports_to_free.append(int(p))
                    elif isinstance(p, dict):
                        p_num = p.get('remote_port') or p.get('remote') or p.get('port')
                        if p_num and str(p_num).isdigit():
                            ports_to_free.append(int(p_num))
            await free_ports(ports_to_free)

            auth_token = spec.get('token') or spec.get('auth_token') or spec.get('auth')
            key = spec.get('key')
            reverse_only = spec.get('reverse_only')
            if reverse_only is None:
                reverse_only = is_reverse
            
            cmd = [str(binary_path), "server", "--port", str(control_port)]
            
            host = spec.get('host') or spec.get('bind_ip') or spec.get('listen_ip')
            use_ipv6 = spec.get('use_ipv6', False)
            if host:
                cmd.extend(["--host", str(host).strip()])
            elif use_ipv6:
                cmd.extend(["--host", "::"])

            if auth_token:
                cmd.extend(["--auth", auth_token])
            
            # Persistent SSH key to preserve host fingerprint across restarts
            ssh_key_pem = spec.get('ssh_key_pem') or spec.get('keyfile_content')
            keyfile_path = self.config_dir / f"{tunnel_id}_ssh.key"
            if ssh_key_pem:
                with open(keyfile_path, "w") as kf:
                    kf.write(ssh_key_pem)
                try:
                    os.chmod(keyfile_path, 0o600)
                except Exception:
                    pass
                cmd.extend(["--keyfile", str(keyfile_path)])
            elif spec.get('keyfile'):
                cmd.extend(["--keyfile", str(spec.get('keyfile'))])
            elif key:
                cmd.extend(["--key", key])
            else:
                if not keyfile_path.exists():
                    try:
                        kproc = await asyncio.create_subprocess_exec(
                            str(binary_path), "server", "--keygen", str(keyfile_path),
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL
                        )
                        await asyncio.wait_for(kproc.wait(), timeout=5.0)
                        try:
                            os.chmod(keyfile_path, 0o600)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"Could not auto-generate persistent SSH key for chisel: {e}")
                if keyfile_path.exists():
                    cmd.extend(["--keyfile", str(keyfile_path)])
            
            # TLS / WSS Configuration
            if use_tls:
                tls_cert_pem = spec.get('tls_cert_pem') or spec.get('tls_cert')
                tls_key_pem = spec.get('tls_key_pem') or spec.get('tls_key')
                cert_path = self.config_dir / f"{tunnel_id}_cert.pem"
                key_path = self.config_dir / f"{tunnel_id}_key.pem"
                
                if tls_cert_pem and tls_key_pem:
                    with open(cert_path, "w") as cf:
                        cf.write(tls_cert_pem)
                    with open(key_path, "w") as kf:
                        kf.write(tls_key_pem)
                    try:
                        os.chmod(cert_path, 0o600)
                        os.chmod(key_path, 0o600)
                    except Exception:
                        pass
                elif not cert_path.exists() or not key_path.exists():
                    try:
                        cproc = await asyncio.create_subprocess_exec(
                            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", str(key_path), "-out", str(cert_path), "-days", "3650",
                            "-subj", "/CN=chisel-tunnel",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL
                        )
                        await asyncio.wait_for(cproc.wait(), timeout=5.0)
                        try:
                            os.chmod(key_path, 0o600)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"Could not auto-generate self-signed cert for chisel: {e}")
                
                if cert_path.exists() and key_path.exists():
                    cmd.extend(["--tls-cert", str(cert_path), "--tls-key", str(key_path)])

            # Active Probing Camouflage / Decoy Backend
            backend_url = spec.get('backend_url') or spec.get('backend') or spec.get('decoy_url')
            if backend_url:
                cmd.extend(["--backend", str(backend_url).strip()])
            
            # SOCKS5 dynamic proxy support
            if tunnel_proto == 'socks5' or spec.get('socks5'):
                cmd.append("--socks5")

            if reverse_only:
                cmd.append("--reverse")
            
            keepalive = spec.get('keepalive')
            if keepalive:
                cmd.extend(["--keepalive", str(keepalive)])
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            try:
                if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                    old_log = self.config_dir / f"{tunnel_id}.log.old"
                    if old_log.exists():
                        old_log.unlink()
                    log_file.rename(old_log)
            except Exception:
                pass

            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel server for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {sanitize_cmd_for_log(cmd)}\n")
                log_f.flush()
                proc = await _spawn_core_subprocess(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("chisel binary not found. Please install chisel.")
        else:
            server_url = spec.get('server_url') or spec.get('remote_addr') or spec.get('server_addr')
            if not server_url:
                raise ValueError("Chisel client requires 'server_url' or 'remote_addr' in spec")
            
            # Ensure correct protocol prefix
            if use_tls:
                if server_url.startswith("http://"):
                    server_url = "https://" + server_url[7:]
                elif server_url.startswith("ws://"):
                    server_url = "wss://" + server_url[5:]
                elif not server_url.startswith("https://") and not server_url.startswith("wss://"):
                    server_url = f"https://{server_url}"
            else:
                if server_url.startswith("https://"):
                    server_url = "http://" + server_url[8:]
                elif server_url.startswith("wss://"):
                    server_url = "ws://" + server_url[6:]
                elif not server_url.startswith("http://") and not server_url.startswith("ws://"):
                    server_url = f"http://{server_url}"
            
            auth_token = spec.get('token') or spec.get('auth_token') or spec.get('auth')
            fingerprint = spec.get('fingerprint')
            key = spec.get('key')
            keepalive = spec.get('keepalive', '10s')
            max_retry_count = spec.get('max_retry_count')
            max_retry_interval = spec.get('max_retry_interval') or '10s'
            
            target_host = spec.get("target_host") or spec.get("local_host") or "127.0.0.1"
            remotes = []
            ports = spec.get('ports') or []

            # In direct mode (not is_reverse), client binds local service ports
            if not is_reverse:
                client_ports_to_free = []
                if tunnel_proto == 'socks5':
                    sp = spec.get('local_port') or (ports[0] if ports else 1080)
                    if str(sp).isdigit():
                        client_ports_to_free.append(int(sp))
                elif not ports:
                    lp = spec.get('local_port') or spec.get('port')
                    if lp and str(lp).isdigit():
                        client_ports_to_free.append(int(lp))
                else:
                    for port_item in ports:
                        if isinstance(port_item, dict):
                            lp = port_item.get('local_port') or port_item.get('port')
                            if lp and str(lp).isdigit():
                                client_ports_to_free.append(int(lp))
                        elif isinstance(port_item, str) and ":" in port_item:
                            parts = port_item.split(":")
                            if len(parts) == 2 and parts[0].isdigit():
                                client_ports_to_free.append(int(parts[0]))
                        elif isinstance(port_item, (int, str)) and str(port_item).isdigit():
                            client_ports_to_free.append(int(port_item))
                if client_ports_to_free:
                    await free_ports(client_ports_to_free)

            def _build_remote_entries(local_p, remote_p, is_rev: bool, proto: str) -> List[str]:
                entries = []
                # In reverse mode: R:<remote_p>:<target_host>:<local_p>
                # In direct mode:  <local_p>:<target_host>:<remote_p>
                if is_rev:
                    prefix = f"R:{remote_p}:{target_host}:{local_p}"
                else:
                    prefix = f"{local_p}:{target_host}:{remote_p}"
                
                if proto == 'udp':
                    entries.append(f"{prefix}/udp")
                elif proto in ['tcp+udp', 'all']:
                    entries.append(prefix)
                    entries.append(f"{prefix}/udp")
                else:
                    entries.append(prefix)
                return entries
            
            if tunnel_proto == 'socks5':
                socks_port = spec.get('local_port') or (ports[0] if ports else 1080)
                remotes.append(f"R:{socks_port}:socks" if is_reverse else f"{socks_port}:socks")
            elif not ports:
                local_port = spec.get('local_port') or spec.get('port')
                remote_port = spec.get('remote_port') or local_port
                if local_port and remote_port:
                    remotes.extend(_build_remote_entries(local_port, remote_port, is_reverse, tunnel_proto))
            else:
                for port_item in ports:
                    remote_p = None
                    local_p = None
                    if isinstance(port_item, dict):
                        local_p = port_item.get('local_port') or port_item.get('port')
                        remote_p = port_item.get('remote_port') or local_p
                    elif isinstance(port_item, str) and ":" in port_item:
                        parts = port_item.split(":")
                        if len(parts) == 2:
                            if is_reverse:
                                remote_p, local_p = parts
                            else:
                                local_p, remote_p = parts
                    else:
                        port_num = int(port_item) if isinstance(port_item, (int, str)) and str(port_item).isdigit() else port_item
                        remote_p = local_p = port_num

                    if remote_p and local_p:
                        remotes.extend(_build_remote_entries(local_p, remote_p, is_reverse, tunnel_proto))
            
            if not remotes:
                raise ValueError("Chisel client requires at least one port mapping")
            
            cmd = [str(binary_path), "client"]
            if auth_token:
                cmd.extend(["--auth", auth_token])
            if fingerprint:
                cmd.extend(["--fingerprint", fingerprint.strip()])
            if key:
                cmd.extend(["--key", str(key).strip()])
            if keepalive:
                cmd.extend(["--keepalive", str(keepalive)])
            if max_retry_count is not None:
                cmd.extend(["--max-retry-count", str(max_retry_count)])
            if max_retry_interval:
                cmd.extend(["--max-retry-interval", str(max_retry_interval)])
            
            # Anti-DPI & TLS options
            if use_tls:
                tls_skip_verify = spec.get('tls_skip_verify', True)
                if tls_skip_verify:
                    cmd.append("--tls-skip-verify")
                
                tls_ca_pem = spec.get('tls_ca_cert_pem') or spec.get('tls_ca')
                if tls_ca_pem:
                    ca_path = self.config_dir / f"{tunnel_id}_ca.pem"
                    with open(ca_path, "w") as cf:
                        cf.write(tls_ca_pem)
                    try:
                        os.chmod(ca_path, 0o600)
                    except Exception:
                        pass
                    cmd.extend(["--tls-ca", str(ca_path)])
                
                custom_sni = spec.get('custom_sni') or spec.get('stealth_domain') or spec.get('sni')
                if custom_sni:
                    cmd.extend(["--sni", str(custom_sni).strip()])
            
            custom_host = spec.get('custom_host') or spec.get('hostname')
            if custom_host:
                cmd.extend(["--hostname", str(custom_host).strip()])
            
            user_agent = spec.get('user_agent')
            if user_agent:
                cmd.extend(["--header", f"User-Agent: {user_agent.strip()}"])
            
            custom_headers = spec.get('custom_headers')
            if isinstance(custom_headers, list):
                for h in custom_headers:
                    if h and ":" in str(h):
                        cmd.extend(["--header", str(h).strip()])
            elif isinstance(custom_headers, dict):
                for hk, hv in custom_headers.items():
                    cmd.extend(["--header", f"{hk}: {hv}"])
            
            proxy = spec.get('proxy') or spec.get('upstream_proxy')
            if proxy:
                cmd.extend(["--proxy", str(proxy).strip()])
            
            cmd.append(server_url)
            cmd.extend(remotes)
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            try:
                if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                    old_log = self.config_dir / f"{tunnel_id}.log.old"
                    if old_log.exists():
                        old_log.unlink()
                    log_file.rename(old_log)
            except Exception:
                pass

            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel client for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {sanitize_cmd_for_log(cmd)}\n")
                log_f.flush()
                proc = await _spawn_core_subprocess(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("chisel binary not found. Please install chisel.")
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        _save_tunnel_pid(tunnel_id, proc.pid)
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
            _remove_tunnel_pid(tunnel_id)
            raise RuntimeError(f"chisel failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
    
    async def remove(self, tunnel_id: str, purge: bool = False):
        """Remove Chisel tunnel, free bound ports, and clean up associated key/cert files"""
        pid = _get_tunnel_pid(tunnel_id)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        ports_to_free: Set[int] = set()
        log_file = self.config_dir / f"{tunnel_id}.log"
        if log_file.exists():
            try:
                txt = log_file.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'(?:R:|\s:?)(\d{1,5}):', txt):
                    p_val = int(m.group(1))
                    if 0 < p_val <= 65535:
                        ports_to_free.add(p_val)
            except Exception:
                pass

        await safe_stop_subprocess(proc, patterns=[tunnel_id, f"{tunnel_id}.log"], pid=pid)
        _remove_tunnel_pid(tunnel_id)

        # Clean up temporary certificate/key files
        suffixes = ["_cert.pem", "_key.pem", "_ca.pem"]
        if purge:
            suffixes.extend(["_ssh.key", ".log", ".log.old"])
        for suffix in suffixes:
            fpath = self.config_dir / f"{tunnel_id}{suffix}"
            if fpath.exists():
                try:
                    fpath.unlink()
                except Exception:
                    pass

        if ports_to_free:
            await free_ports(ports_to_free)
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "chisel")
        
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
        
    def _resolve_server_binary_path(self) -> Path:
        """Resolve frps binary path"""
        env_path = os.environ.get("FRPS_BINARY")
        if env_path:
            resolved = Path(env_path)
            if resolved.exists() and resolved.is_file():
                return resolved
        
        common_paths = [
            Path("/usr/local/bin/frps"),
            Path("/usr/bin/frps"),
        ]
        
        for path in common_paths:
            if path.exists() and path.is_file():
                return path
        
        resolved = shutil.which("frps")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "frps binary not found. Expected at FRPS_BINARY, '/usr/local/bin/frps', or in PATH."
        )
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply FRP tunnel - supports both server and client modes"""
        logger.info(f"FRP tunnel {tunnel_id} pre-cleaning any existing processes")
        await self.remove(tunnel_id)
        await asyncio.sleep(0.3)
        
        mode = spec.get('mode', 'client')
        is_provider = bool(spec.get('is_provider'))
        is_visitor = bool(spec.get('is_visitor'))
        is_reverse = spec.get('is_reverse', True)
        secret_key = spec.get('secret_key', '')
        
        if mode == 'server':
            bind_port = spec.get('bind_port', 7000)
            if isinstance(bind_port, str) and str(bind_port).isdigit():
                bind_port = int(bind_port)
            elif not isinstance(bind_port, int):
                bind_port = 7000
            
            ports_to_free = [bind_port]
            if not is_provider and is_reverse:
                # In reverse mode, server on Iran node binds remote service ports as well
                for p_item in (spec.get('ports') or []):
                    if isinstance(p_item, (int, str)) and str(p_item).isdigit():
                        ports_to_free.append(int(p_item))
                    elif isinstance(p_item, dict):
                        p_val = p_item.get('remote_port') or p_item.get('remote') or p_item.get('port') or p_item.get('listen_port')
                        if p_val and str(p_val).isdigit():
                            ports_to_free.append(int(p_val))
            await free_ports(ports_to_free)

            token = spec.get('token')
            clean_token = str(token).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '') if token else None
            
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

            force_tls = bool(spec.get('force_tls')) or (spec.get('security_type') in ['tls', 'force_tls']) or bool(spec.get('tls_enable'))
            if transport_proto in ['wss', 'quic']:
                force_tls = True

            tunnel_type = (spec.get('tunnel_type') or spec.get('type') or 'tcp').lower()

            # Handle server TLS certificates if provided
            tls_cert_pem = spec.get('tls_cert_pem')
            tls_key_pem = spec.get('tls_key_pem')
            cert_file = None
            key_file = None
            if tls_cert_pem and tls_key_pem:
                cert_file = self.config_dir / f"{tunnel_id}_cert.pem"
                key_file = self.config_dir / f"{tunnel_id}_key.pem"
                with open(cert_file, 'w', encoding='utf-8') as cf:
                    cf.write(tls_cert_pem)
                with open(key_file, 'w', encoding='utf-8') as kf:
                    kf.write(tls_key_pem)
                try:
                    os.chmod(cert_file, 0o600)
                    os.chmod(key_file, 0o600)
                except Exception:
                    pass

            config_file = self.config_dir / f"frps_{tunnel_id}.yaml"
            config_content = f"""bindAddr: "::"
bindPort: {bind_port}
"""
            if transport_proto == 'kcp':
                config_content += f"kcpBindPort: {bind_port}\nquicBindPort: 0\n"
            elif transport_proto == 'quic':
                config_content += f"kcpBindPort: 0\nquicBindPort: {bind_port}\n"
            else:
                config_content += "kcpBindPort: 0\nquicBindPort: 0\n"

            first_service_port = None
            for p_item in (spec.get('ports') or []):
                if isinstance(p_item, (int, str)) and str(p_item).isdigit():
                    first_service_port = int(p_item)
                    break
                elif isinstance(p_item, dict):
                    p_val = p_item.get('remote_port') or p_item.get('remote') or p_item.get('port') or p_item.get('listen_port')
                    if p_val and str(p_val).isdigit():
                        first_service_port = int(p_val)
                        break

            if tunnel_type == 'http' and not is_provider:
                hp = spec.get('vhost_http_port') or (first_service_port if first_service_port and first_service_port != bind_port else (bind_port + 1 if bind_port == 80 else 80))
                config_content += f"vhostHTTPPort: {hp}\n"
            elif tunnel_type == 'https' and not is_provider:
                hp = spec.get('vhost_https_port') or (first_service_port if first_service_port and first_service_port != bind_port else (bind_port + 1 if bind_port == 443 else 443))
                config_content += f"vhostHTTPSPort: {hp}\n"

            config_content += f"""transport:
  maxPoolCount: 8
  heartbeatTimeout: 30
  tcpMux: true
  tcpMuxKeepaliveInterval: 25
  tls:
    force: {'true' if force_tls else 'false'}
"""
            if cert_file and key_file:
                cert_posix = cert_file.resolve().as_posix()
                key_posix = key_file.resolve().as_posix()
                config_content += f"""    certFile: "{cert_posix}"
    keyFile: "{key_posix}"
"""

            if clean_token:
                config_content += f"""auth:
  method: token
  token: "{clean_token}"
  additionalScopes:
    - HeartBeats
    - NewWorkConns
"""
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            try:
                os.chmod(config_file, 0o600)
            except Exception:
                pass
            
            logger.info(f"FRP server tunnel {tunnel_id}: bind_port={bind_port}, proto={transport_proto}, tls={'force' if force_tls else 'off'}, token={'set' if clean_token else 'none'}, is_provider={is_provider}")
            
            binary_path = self._resolve_server_binary_path()
            config_file_abs = config_file.resolve()
            cmd = [
                str(binary_path),
                "-c", str(config_file_abs)
            ]
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                try:
                    log_file.write_text("")
                except Exception:
                    pass
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting FRP server for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"Config: bind_port={bind_port}, token={'set' if clean_token else 'none'}\n")
                log_f.flush()
                proc = await _spawn_core_subprocess(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
                self.log_handles[tunnel_id] = log_f
                self.processes[tunnel_id] = proc
                _save_tunnel_pid(tunnel_id, proc.pid)
            except Exception as e:
                log_f.close()
                if isinstance(e, FileNotFoundError):
                    raise RuntimeError("FRP server binary (frps) not found. Please install FRP.")
                raise

            provider_proc = None
            provider_log_f = None
            provider_log_file = None
            if is_provider:
                await asyncio.sleep(0.3)
                # Build and launch local frpc provider on Foreign node connecting to 127.0.0.1:bind_port
                provider_config_file = self.config_dir / f"frpc_{tunnel_id}_provider.yaml"
                provider_config_content = f"""serverAddr: "127.0.0.1"
serverPort: {bind_port}
loginFailExit: false
auth:
  method: token
  token: "{clean_token or ''}"
proxies:
"""
                use_encryption = spec.get('use_encryption', True)
                use_compression = spec.get('use_compression', True)
                bandwidth_limit = spec.get('bandwidth_limit')
                bandwidth_limit_mode = spec.get('bandwidth_limit_mode', 'client')
                health_check_type = spec.get('health_check_type')
                health_check_interval = int(spec.get('health_check_interval_s') or 10)
                health_check_timeout = int(spec.get('health_check_timeout_s') or 3)
                health_check_max_failed = int(spec.get('health_check_max_failed') or 3)
                health_check_path = str(spec.get('health_check_path', '/')).strip()
                if not health_check_path.startswith('/'):
                    health_check_path = f"/{health_check_path}"
                health_check_path = health_check_path.replace('"', '').replace('\n', '').replace('\r', '')

                raw_ports = spec.get('ports') or []
                clean_target_host = str(spec.get('local_ip') or spec.get('target_host') or '127.0.0.1').replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')

                for i, port_config in enumerate(raw_ports):
                    if isinstance(port_config, dict):
                        l_port = port_config.get('local') or port_config.get('local_port') or port_config.get('port') or port_config.get('remote') or port_config.get('remote_port')
                    else:
                        l_port = port_config
                    try:
                        l_port_num = int(l_port)
                    except (ValueError, TypeError):
                        continue

                    proxy_base_name = f"{tunnel_id}_{i}" if len(raw_ports) > 1 else tunnel_id
                    effective_compression = False if (tunnel_type in ['udp', 'tcp+udp'] or spec.get('gaming_mode')) else bool(use_compression)

                    def _build_provider_proxy_block(p_name: str, p_type: str, port_num: int) -> str:
                        clean_p_name = str(p_name).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        clean_sec_key = str(secret_key).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        blk = f"""  - name: "{clean_p_name}"
    type: {p_type}
    secretKey: "{clean_sec_key}"
    localIP: "{clean_target_host}"
    localPort: {port_num}
    transport:
      useEncryption: {'true' if use_encryption else 'false'}
      useCompression: {'true' if effective_compression else 'false'}
"""
                        if bandwidth_limit:
                            blk += f"""      bandwidthLimit: "{bandwidth_limit}"
      bandwidthLimitMode: "{bandwidth_limit_mode}"
"""
                        if health_check_type and p_type == 'stcp':
                            hc_type = "http" if (tunnel_type == 'http' and health_check_type == 'http') else "tcp"
                            blk += f"""    healthCheck:
      type: {hc_type}
"""
                            if hc_type == 'http':
                                blk += f'      path: "{health_check_path}"\n'
                            blk += f"""      timeoutSeconds: {health_check_timeout}
      maxFailed: {health_check_max_failed}
      intervalSeconds: {health_check_interval}
"""
                        return blk

                    if tunnel_type == 'tcp+udp':
                        provider_config_content += _build_provider_proxy_block(f"{proxy_base_name}_tcp", "stcp", l_port_num)
                        provider_config_content += _build_provider_proxy_block(f"{proxy_base_name}_udp", "sudp", l_port_num)
                    elif tunnel_type == 'udp':
                        provider_config_content += _build_provider_proxy_block(f"{proxy_base_name}_udp", "sudp", l_port_num)
                    else:
                        provider_config_content += _build_provider_proxy_block(proxy_base_name, "stcp", l_port_num)

                with open(provider_config_file, 'w') as pf:
                    pf.write(provider_config_content)
                try:
                    os.chmod(provider_config_file, 0o600)
                except Exception:
                    pass

                provider_binary = self._resolve_binary_path()
                provider_cmd = [str(provider_binary), "-c", str(provider_config_file.resolve())]
                provider_log_file = self.config_dir / f"{tunnel_id}_provider.log"
                provider_log_f = open(provider_log_file, 'w', buffering=1)
                try:
                    provider_log_f.write(f"Starting FRP provider for tunnel {tunnel_id}\n")
                    provider_log_f.write(f"Command: {' '.join(provider_cmd)}\n")
                    provider_log_f.flush()
                    provider_proc = await _spawn_core_subprocess(
                        provider_cmd,
                        stdout=provider_log_f,
                        stderr=subprocess.STDOUT,
                    )
                except Exception as e:
                    provider_log_f.close()
                    await self.remove(tunnel_id)
                    raise RuntimeError(f"FRP provider failed to spawn: {e}")

                self.processes[f"{tunnel_id}_provider"] = provider_proc
                self.log_handles[f"{tunnel_id}_provider"] = provider_log_f
                _save_tunnel_pid(f"{tunnel_id}_provider", provider_proc.pid)
        else:
            logger.info(f"FRP tunnel {tunnel_id} received spec: {sanitize_spec_for_log(spec)}")
            
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
            
            custom_sni = spec.get('custom_sni') or spec.get('stealth_domain') or spec.get('server_name') or os.getenv('FRP_DEFAULT_SNI')
            
            # Layer-2 proxy payload encryption & compression
            use_encryption = spec.get('use_encryption', True)
            use_compression = spec.get('use_compression', True)
            
            # Bandwidth limit per proxy (clean format: e.g. 10MB, 500KB)
            bandwidth_limit = spec.get('bandwidth_limit')
            rate_limit_mbps = spec.get('rate_limit_mbps')
            if not bandwidth_limit and rate_limit_mbps and float(rate_limit_mbps) > 0:
                bandwidth_limit = f"{int(float(rate_limit_mbps))}MB"
            if bandwidth_limit:
                clean_bw = str(bandwidth_limit).strip().upper().replace(" ", "")
                if clean_bw.isdigit():
                    clean_bw = f"{clean_bw}MB"
                import re
                if re.match(r'^\d+(KB|MB|GB|B)$', clean_bw):
                    bandwidth_limit = clean_bw
                else:
                    bandwidth_limit = None
            bandwidth_limit_mode = spec.get('bandwidth_limit_mode', 'client')
            if bandwidth_limit_mode not in ['client', 'server']:
                bandwidth_limit_mode = 'client'

            # Proxy protocol version (v1, v2)
            proxy_protocol_version = spec.get('proxy_protocol_version')
            if proxy_protocol_version not in ['v1', 'v2']:
                proxy_protocol_version = None

            # Health check configuration
            health_check_type = spec.get('health_check_type')
            health_check_interval = int(spec.get('health_check_interval_s') or 10)
            health_check_timeout = int(spec.get('health_check_timeout_s') or 3)
            health_check_max_failed = int(spec.get('health_check_max_failed') or 3)
            health_check_path = str(spec.get('health_check_path', '/')).strip()
            if not health_check_path.startswith('/'):
                health_check_path = f"/{health_check_path}"
            health_check_path = health_check_path.replace('"', '').replace('\n', '').replace('\r', '')

            # Custom domains for HTTP/HTTPS
            custom_domains = spec.get('custom_domains') or []
            if isinstance(custom_domains, str):
                custom_domains = [d.strip() for d in custom_domains.replace(",", "\n").split("\n") if d.strip()]

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
                            if 1 <= int(start) <= 65535 and 1 <= int(end) <= 65535 and int(end) - int(start) <= 200:
                                for p in range(int(start), int(end) + 1):
                                    ports.append({'local': p, 'remote': p})
                        except Exception:
                            pass
            
            logger.info(f"FRP tunnel {tunnel_id} parsed: server_addr='{server_addr}', server_port={server_port}, proto={transport_proto}, tls={tls_enable}, sni={custom_sni}, ports={len(ports)}, is_visitor={is_visitor}")
            
            if not server_addr:
                raise ValueError("FRP client requires 'server_addr' (server address) in spec")
            if not ports:
                raise ValueError("FRP client requires 'ports' array or 'remote_port'/'listen_port' in spec")
            if tunnel_type not in ['tcp', 'udp', 'tcp+udp', 'http', 'https']:
                raise ValueError(f"FRP only supports 'tcp', 'udp', 'tcp+udp', 'http', and 'https' types, got '{tunnel_type}'")
            
            if server_addr.startswith('[') and server_addr.endswith(']'):
                server_addr = server_addr[1:-1]
            
            if not server_addr or server_addr in ["0.0.0.0", "localhost", "127.0.0.1", "::1"]:
                raise ValueError(f"Invalid FRP server_addr: {server_addr}. Must be a valid server IP address or hostname.")
            
            clean_server_addr = str(server_addr).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
            clean_token = str(token).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '') if token else None
            clean_sni = str(custom_sni).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '') if custom_sni else None

            config_file = self.config_dir / f"frpc_{tunnel_id}.yaml"
            config_content = f"""serverAddr: "{clean_server_addr}"
serverPort: {server_port}
loginFailExit: false
transport:
  protocol: "{transport_proto}"
  heartbeatInterval: 10
  heartbeatTimeout: 30
  dialServerTimeout: 15
"""
            if transport_proto != 'quic':
                config_content += """  tcpMux: true
  tcpMuxKeepaliveInterval: 25
  poolCount: 5
"""
            if tls_enable and transport_proto != 'quic':
                config_content += """  tls:
    enable: true
    disableCustomTLSFirstByte: true
"""
                if clean_sni:
                    config_content += f"""    serverName: "{clean_sni}"\n"""

            if clean_token:
                config_content += f"""auth:
  method: token
  token: "{clean_token}"
  additionalScopes:
    - HeartBeats
    - NewWorkConns
"""
            
            def _build_proxy_block(p_name: str, p_type: str, l_port: int, r_port: Optional[int]) -> str:
                clean_p_name = str(p_name).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                clean_local_ip = str(local_ip).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                name_val = f'"{clean_p_name}"' if any(c in clean_p_name for c in [':', ' ', '#', '"', "'", '@', '%']) else clean_p_name
                local_ip_val = f'"{clean_local_ip}"' if ':' in clean_local_ip else clean_local_ip
                block = f"""  - name: {name_val}
    type: {p_type}
    localIP: {local_ip_val}
    localPort: {l_port}
"""
                if p_type in ['tcp', 'udp'] and r_port is not None:
                    block += f"    remotePort: {r_port}\n"
                elif p_type in ['http', 'https']:
                    domains = custom_domains if custom_domains else [clean_server_addr]
                    block += "    customDomains:\n"
                    for d in domains:
                        clean_d = str(d).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        block += f'      - "{clean_d}"\n'

                effective_compression = False if (p_type == 'udp' or spec.get('gaming_mode')) else bool(use_compression)
                block += f"""    transport:
      useEncryption: {'true' if use_encryption else 'false'}
      useCompression: {'true' if effective_compression else 'false'}
"""
                if bandwidth_limit:
                    block += f"""      bandwidthLimit: "{bandwidth_limit}"
      bandwidthLimitMode: "{bandwidth_limit_mode}"
"""
                if proxy_protocol_version and p_type in ['tcp', 'http', 'https']:
                    block += f"""      proxyProtocolVersion: "{proxy_protocol_version}"
"""
                if health_check_type and p_type in ['tcp', 'http', 'https']:
                    hc_type = "http" if (p_type == 'http' and health_check_type == 'http') else "tcp"
                    block += f"""    healthCheck:
      type: {hc_type}
"""
                    if hc_type == 'http':
                        clean_path = str(health_check_path).replace('"', '\\"')
                        block += f'      path: "{clean_path}"\n'
                    block += f"""      timeoutSeconds: {health_check_timeout}
      maxFailed: {health_check_max_failed}
      intervalSeconds: {health_check_interval}
"""
                return block

            if is_visitor:
                # Direct mode: Iran node runs visitor, binds local service ports on 0.0.0.0
                ports_to_free = []
                for p_item in ports:
                    if isinstance(p_item, dict):
                        p_val = p_item.get('remote') or p_item.get('remote_port') or p_item.get('local') or p_item.get('local_port') or p_item.get('port')
                    else:
                        p_val = p_item
                    if p_val and str(p_val).isdigit():
                        ports_to_free.append(int(p_val))
                if ports_to_free:
                    await free_ports(ports_to_free)

                config_content += "\nvisitors:\n"
                effective_compression = False if (tunnel_type in ['udp', 'tcp+udp'] or spec.get('gaming_mode')) else bool(use_compression)
                for i, port_config in enumerate(ports):
                    if isinstance(port_config, dict):
                        local_port = port_config.get('local') or port_config.get('remote') or port_config.get('port')
                    else:
                        local_port = port_config
                    p_num = int(local_port) if str(local_port).isdigit() else 8080
                    proxy_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id

                    def _build_visitor_block(v_name: str, v_type: str, s_name: str, b_port: int) -> str:
                        clean_v_name = str(v_name).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        clean_s_name = str(s_name).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        clean_sec_key = str(secret_key).replace('\\', '\\\\').replace('"', '\\"').replace('\r', '').replace('\n', '')
                        return f"""  - name: "{clean_v_name}"
    type: {v_type}
    serverName: "{clean_s_name}"
    secretKey: "{clean_sec_key}"
    bindAddr: "0.0.0.0"
    bindPort: {b_port}
    transport:
      useEncryption: {'true' if use_encryption else 'false'}
      useCompression: {'true' if effective_compression else 'false'}
"""

                    if tunnel_type == 'tcp+udp':
                        config_content += _build_visitor_block(f"{proxy_name}_tcp_visitor", "stcp", f"{proxy_name}_tcp", p_num)
                        config_content += _build_visitor_block(f"{proxy_name}_udp_visitor", "sudp", f"{proxy_name}_udp", p_num)
                    elif tunnel_type == 'udp':
                        config_content += _build_visitor_block(f"{proxy_name}_udp_visitor", "sudp", f"{proxy_name}_udp", p_num)
                    else:
                        config_content += _build_visitor_block(f"{proxy_name}_visitor", "stcp", proxy_name, p_num)
            else:
                # Reverse mode: Foreign node runs standard proxies targeting local services
                config_content += "\nproxies:\n"
                for i, port_config in enumerate(ports):
                    if isinstance(port_config, dict):
                        local_port = port_config.get('local')
                        remote_port = port_config.get('remote')
                    else:
                        local_port = remote_port = port_config
                    
                    proxy_name = f"{tunnel_id}_{i}" if len(ports) > 1 else tunnel_id
                    if tunnel_type == 'tcp+udp':
                        config_content += _build_proxy_block(f"{proxy_name}_tcp", "tcp", local_port, remote_port)
                        config_content += _build_proxy_block(f"{proxy_name}_udp", "udp", local_port, remote_port)
                    else:
                        config_content += _build_proxy_block(proxy_name, tunnel_type, local_port, remote_port)
            
            with open(config_file, 'w') as f:
                f.write(config_content)
            try:
                os.chmod(config_file, 0o600)
            except Exception:
                pass
            
            logger.info(f"FRP tunnel {tunnel_id}: type={tunnel_type}, proto={transport_proto}, local={local_ip}, server={clean_server_addr}:{server_port}, is_visitor={is_visitor}")
            
            binary_path = self._resolve_binary_path()
            config_file_abs = config_file.resolve()
            
            cmd = [
                str(binary_path),
                "-c", str(config_file_abs)
            ]
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
                try:
                    log_file.unlink()
                except Exception:
                    pass
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting FRP client for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {' '.join(cmd)}\n")
                log_f.write(f"Config: type={tunnel_type}, local={local_ip}, server={clean_server_addr}:{server_port}, is_visitor={is_visitor}\n")
                log_f.flush()
                proc = await _spawn_core_subprocess(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError:
                log_f.close()
                raise RuntimeError("FRP binary (frpc) not found. Please install FRP.")
            except Exception:
                log_f.close()
                raise
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        _save_tunnel_pid(tunnel_id, proc.pid)
        await asyncio.sleep(1.0)
        if proc.returncode is not None:
            stderr = ""
            if log_file.exists():
                with open(log_file, 'r') as f:
                    stderr = f.read()
            if tunnel_id in self.log_handles:
                try:
                    self.log_handles[tunnel_id].close()
                except Exception:
                    pass
                del self.log_handles[tunnel_id]
            _remove_tunnel_pid(tunnel_id)
            raise RuntimeError(f"FRP failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")

        if mode == 'server' and is_provider and provider_proc is not None:
            if provider_proc.returncode is not None:
                stderr = ""
                if provider_log_file and provider_log_file.exists():
                    with open(provider_log_file, 'r') as pf:
                        stderr = pf.read()
                await self.remove(tunnel_id)
                raise RuntimeError(f"FRP provider failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
    
    async def remove(self, tunnel_id: str, purge: bool = False):
        """Remove FRP tunnel (handles both server and client modes, and provider subprocess) and free bound ports"""
        ports_to_free: Set[int] = set()
        for sub_id in [tunnel_id, f"{tunnel_id}_provider"]:
            for cfg_name in [f"frps_{sub_id}.yaml", f"frpc_{sub_id}.yaml", f"frps_{sub_id}.toml", f"frpc_{sub_id}.toml"]:
                cfg_path = self.config_dir / cfg_name
                if cfg_path.exists():
                    try:
                        txt = cfg_path.read_text(encoding="utf-8", errors="ignore")
                        for m in re.finditer(r'(?:bindPort|remotePort|localPort|serverPort|port)\s*[:=]\s*(\d{1,5})', txt, re.IGNORECASE):
                            p_val = int(m.group(1))
                            if 0 < p_val <= 65535:
                                ports_to_free.add(p_val)
                    except Exception:
                        pass

            pid = _get_tunnel_pid(sub_id)
            proc = self.processes.pop(sub_id, None)
            if sub_id in self.log_handles:
                try:
                    self.log_handles[sub_id].close()
                except Exception:
                    pass
                del self.log_handles[sub_id]

            await safe_stop_subprocess(
                proc,
                patterns=[
                    f"frps_{tunnel_id}.yaml",
                    f"frpc_{tunnel_id}.yaml",
                    f"frpc_{tunnel_id}_provider.yaml",
                    f"frps_{tunnel_id}.toml",
                    f"frpc_{tunnel_id}.toml",
                    f"frpc_{tunnel_id}_provider.toml"
                ],
                pid=pid
            )
            _remove_tunnel_pid(sub_id)

        for cfg_name in [
            f"frps_{tunnel_id}.yaml",
            f"frpc_{tunnel_id}.yaml",
            f"frpc_{tunnel_id}_provider.yaml",
            f"frps_{tunnel_id}.toml",
            f"frpc_{tunnel_id}.toml",
            f"frpc_{tunnel_id}_provider.toml",
            f"{tunnel_id}.log",
            f"{tunnel_id}_provider.log",
            f"{tunnel_id}_cert.pem",
            f"{tunnel_id}_key.pem"
        ]:
            cfg_path = self.config_dir / cfg_name
            if cfg_path.exists():
                try:
                    cfg_path.unlink()
                except Exception:
                    pass

        if ports_to_free:
            await free_ports(ports_to_free)
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "frp")
            
        provider_id = f"{tunnel_id}_provider"
        if provider_id in self.processes:
            provider_proc = self.processes[provider_id]
            provider_running = provider_proc.returncode is None
            is_running = is_running and provider_running
        elif _get_tunnel_pid(provider_id):
            is_running = is_running and _is_tunnel_pid_alive(provider_id, "frp")
        
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
        
        # 1. Validate spec requirements FIRST before mutating or removing running process
        is_reverse = spec.get('is_reverse', False)
        mode = spec.get('mode', 'client')
        control_port = spec.get('control_port') or spec.get('remote_port')
        if not control_port:
            raise ValueError("GOST requires 'control_port' or 'remote_port' in spec")
            
        if mode == 'client':
            server_ip = spec.get('server_ip') or spec.get('remote_ip')
            if not server_ip:
                raise ValueError("GOST client requires 'server_ip' or 'remote_ip' in spec")
            ports = spec.get('ports') or []
            if not ports:
                listen_port = spec.get('listen_port')
                if listen_port:
                    ports = [int(listen_port) if isinstance(listen_port, (int, str)) and str(listen_port).isdigit() else listen_port]
            if not ports and not spec.get("port_ranges"):
                raise ValueError("GOST client requires 'ports' array or 'listen_port' or 'port_ranges' in spec")
        
        # 2. Spec validation passed: cleanly stop existing process if any
        if tunnel_id in self.processes:
            logger.info(f"GOST tunnel {tunnel_id} already exists, removing it first")
            await self.remove(tunnel_id)
            
        auth_token = spec.get('auth_token', '')
        transport_type = (spec.get('transport_type') or spec.get('transport') or spec.get('gost_type') or 'tcp').lower()
        security_type = (spec.get('security_type') or 'none').lower()
        use_ipv6 = spec.get('use_ipv6', False)
        
        if transport_type in ["multiplex ws", "multiplex_ws", "wsmux"]:
            transport_type = "mws"
        elif transport_type in ["tcpmux", "tcp_mux"]:
            transport_type = "mtcp"

        gost_type = transport_type
        if transport_type == "ws" and security_type in ["tls", "utls"]:
            gost_type = "wss"
        elif transport_type == "mws" and security_type in ["tls", "utls"]:
            gost_type = "mwss"
        elif transport_type == "wssmux":
            gost_type = "mwss"
        elif transport_type == "tcp" and security_type in ["tls", "utls"]:
            gost_type = "tls"
        elif transport_type == "mtcp" and security_type in ["tls", "utls"]:
            gost_type = "mtls"
        
        tunnel_proto = (spec.get("type") or spec.get("tunnel_type") or "tcp").lower()
        if tunnel_proto not in ["tcp", "udp", "tcp+udp"]:
            tunnel_proto = "tcp"
        is_udp_mode = tunnel_proto in ["udp", "tcp+udp"]
        mux_type = spec.get("mux_type") or "yamux"
        is_reverse_mode = bool(is_reverse)
        # For reverse tunnels (rtcp), multiplexing (yamux) is mandatory to prevent socket closure after the first connection
        enable_mux = (bool(spec.get("gaming_mode")) or bool(spec.get("multiplex")) or is_udp_mode or is_reverse_mode) and gost_type not in ["mws", "mwss", "mtcp", "mtls", "grpc"]

        config = {
            "services": [],
            "chains": [],
            "log": {
                "level": "warn"
            }
        }
        
        # Add Resolvers if specified
        raw_resolvers = spec.get("dns_resolvers")
        if isinstance(raw_resolvers, str):
            raw_resolvers = [r.strip() for r in re.split(r'[\r\n,]+', raw_resolvers) if r.strip()]
        if raw_resolvers and isinstance(raw_resolvers, list):
            resolver_nodes = []
            for i, res in enumerate(raw_resolvers):
                resolver_nodes.append({"name": f"dns-{tunnel_id}-{i}", "addr": res})
            config["resolvers"] = [{
                "name": f"resolver-{tunnel_id}",
                "nodes": resolver_nodes
            }]
            
        # Add Bypasses if specified
        raw_bypass = spec.get("bypass_ips")
        if isinstance(raw_bypass, str):
            raw_bypass = [b.strip() for b in re.split(r'[\r\n,]+', raw_bypass) if b.strip()]
        if raw_bypass and isinstance(raw_bypass, list):
            config["bypasses"] = [{
                "name": f"bypass-{tunnel_id}",
                "matchers": raw_bypass
            }]
        
        if mode == 'server':
            # 1. Server Configuration (Foreign Node in direct mode, Iran Node in reverse mode)
            if control_port:
                await free_port(control_port)
            if is_reverse:
                server_ports = []
                if spec.get("ports"):
                    for p in spec.get("ports"):
                        if isinstance(p, dict):
                            p_num = p.get('local_port') or p.get('local') or p.get('port')
                        elif isinstance(p, str) and "=" in p:
                            p_num = p.split("=", 1)[0].strip()
                        else:
                            p_num = p
                        if isinstance(p_num, (int, str)) and str(p_num).isdigit():
                            server_ports.append(int(p_num))
                if spec.get("port_ranges"):
                    pr_list = spec.get("port_ranges")
                    if isinstance(pr_list, str):
                        pr_list = [x.strip() for x in re.split(r'[\r\n,]+', pr_list) if x.strip()]
                    for pr in pr_list:
                        if isinstance(pr, str) and "-" in pr:
                            try:
                                start_p, end_p = pr.split("-", 1)
                                if int(end_p) - int(start_p) <= 500:
                                    server_ports.extend(range(int(start_p), int(end_p) + 1))
                            except Exception:
                                pass
                if server_ports:
                    await free_ports(server_ports)
            bind_addr = f"[::]:{control_port}" if use_ipv6 else f"0.0.0.0:{control_port}"
            
            # Handler & Protocol Selection
            handler_type = spec.get("handler_type") or "relay"
            
            if gost_type in ["kcp", "quic", "udp", "rudp"]:
                listener_metadata = {}
                if is_reverse:
                    listener_metadata["bind"] = True
                if enable_mux and gost_type not in ["udp", "rudp"]:
                    listener_metadata["mux.type"] = mux_type
                    listener_metadata["mux"] = True
                    listener_metadata["nodelay"] = True
                if gost_type == "kcp":
                    listener_metadata["nodelay"] = True
                    listener_metadata["interval"] = "20ms"
                    listener_metadata["resend"] = 2
                    listener_metadata["nc"] = 1
                    kcp_key = spec.get("kcp_key") or auth_token or "smite-kcp-secure"
                    listener_metadata["crypt"] = spec.get("kcp_crypt") or "aes"
                    listener_metadata["key"] = kcp_key
            else:
                keepalive_interval = f"{spec.get('keepalive_interval') or 15}s" if not str(spec.get('keepalive_interval', '')).endswith('s') else str(spec.get('keepalive_interval'))
                listener_metadata = {
                    "keepAlive": True,
                    "keepAliveInterval": keepalive_interval,
                    "keepAliveTimeout": "30s",
                    "idleTimeout": "120s",
                    "nodelay": True,
                    "bufferSize": 65536,
                }
                if spec.get("ws_path"):
                    listener_metadata["path"] = spec.get("ws_path")
                if is_reverse:
                    listener_metadata["bind"] = True
                if enable_mux:
                    listener_metadata["mux.type"] = mux_type
                    listener_metadata["mux"] = True
                    listener_metadata["nodelay"] = True
                
            server_listener_type = "sshd" if gost_type == "ssh" else gost_type
            listener = {"type": server_listener_type}

            if gost_type in ["ssh", "sshd"]:
                ssh_key_path = self.config_dir / f"ssh_host_key_{tunnel_id}.pem"
                ssh_key_pem = spec.get("ssh_key_pem") or spec.get("keyfile_content")
                if ssh_key_pem:
                    ssh_key_path.write_text(ssh_key_pem.strip())
                    try:
                        os.chmod(ssh_key_path, 0o600)
                    except Exception:
                        pass
                elif not ssh_key_path.exists():
                    try:
                        subprocess.run([
                            "openssl", "genpkey", "-algorithm", "RSA",
                            "-out", str(ssh_key_path),
                            "-pkeyopt", "rsa_keygen_bits:2048"
                        ], check=True, timeout=5, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                        os.chmod(ssh_key_path, 0o600)
                    except Exception as e:
                        logger.warning(f"Could not generate SSH host key with openssl: {e}")
                if ssh_key_path.exists():
                    listener_metadata["hostKey"] = str(ssh_key_path)
                if auth_token:
                    listener["auth"] = {
                        "username": "smite",
                        "password": auth_token
                    }

            if listener_metadata:
                listener["metadata"] = listener_metadata
            
            if (security_type in ["tls", "utls"] or gost_type in ["wss", "mwss", "tls", "mtls", "quic", "grpc"]) and gost_type not in ["tcp", "udp", "rtcp", "rudp", "kcp", "ssh", "sshd"]:
                cert_path = self.config_dir / f"cert_{tunnel_id}.pem"
                key_path = self.config_dir / f"key_{tunnel_id}.pem"
                
                # Check if custom cert/key provided in spec
                if spec.get("tls_cert") and spec.get("tls_key"):
                    cert_path.write_text(spec["tls_cert"].strip())
                    key_path.write_text(spec["tls_key"].strip())
                elif not cert_path.exists() or not key_path.exists():
                    sni_domain = spec.get("custom_sni") or spec.get("stealth_domain") or spec.get("sni") or "www.cloudflare.com"
                    try:
                        subprocess.run([
                            "openssl", "req", "-new", "-newkey", "rsa:2048", "-days", "3650",
                            "-nodes", "-x509", "-subj", f"/CN={sni_domain}",
                            "-keyout", str(key_path), "-out", str(cert_path)
                        ], check=True, timeout=5, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    except Exception as e:
                        logger.error(f"Failed to generate self-signed cert for SNI {sni_domain}: {e}")
                
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
                
            handler_metadata = {}
            if gost_type not in ["kcp", "quic", "udp", "rudp"]:
                handler_metadata["keepAlive"] = True
            if is_reverse:
                handler_metadata["bind"] = True
            if enable_mux:
                handler_metadata["mux.type"] = mux_type
                handler_metadata["mux"] = True
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
                raw_ranges = spec.get("port_ranges")
                range_list = [x.strip() for x in re.split(r'[\r\n,]+', raw_ranges) if x.strip()] if isinstance(raw_ranges, str) else raw_ranges
                for port_range in range_list:
                    if isinstance(port_range, str) and '-' in port_range:
                        try:
                            start, end = port_range.split('-', 1)
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
            
            # Free ports ONLY in direct mode on client! In reverse mode, client is on Foreign Node!
            if not is_reverse:
                await free_ports(ports)
                
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
                    import secrets
                    utls_client = secrets.choice(["chrome", "firefox", "ios", "android", "edge", "safari"])
                dialer_tls["utls"] = {"client": utls_client}
                if not dialer_tls.get("serverName"):
                    dialer_tls["serverName"] = "www.google.com"  # fallback spoofed SNI for uTLS
                # When using uTLS/TLS often we don't have valid cert for our own server IP
                dialer_tls["secure"] = False
            elif security_type == "tls":
                if not dialer_tls.get("serverName"):
                    dialer_tls["serverName"] = "www.google.com"
                dialer_tls["secure"] = False

            # Align authority for gRPC and TLS dialers in GOST v3
            # Go's gRPC client requires transport credentials authority to match dial option / metadata host
            if gost_type == "grpc" or dialer_tls.get("serverName") or security_type in ["tls", "utls"]:
                effective_sni = dialer_tls.get("serverName") or dialer_metadata.get("host")
                if not effective_sni and gost_type == "grpc":
                    effective_sni = "www.google.com"
                    dialer_tls["serverName"] = effective_sni
                    dialer_tls["secure"] = False
                if effective_sni:
                    dialer_metadata["host"] = effective_sni
                    if gost_type == "grpc":
                        dialer_metadata["grpc.host"] = effective_sni
                
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
                try:
                    mbps_val = float(spec.get("rate_limit_mbps"))
                    if mbps_val > 0:
                        rate_bytes = int(mbps_val * 125000) # Mbps to Bytes/sec
                        config["limiters"] = [
                            {
                                "name": f"limiter-{tunnel_id}",
                                "limits": [
                                    f"{rate_bytes}B"
                                ]
                            }
                        ]
                except (ValueError, TypeError):
                    pass
                
            dialer_client_type = "ssh" if gost_type in ["ssh", "sshd"] else gost_type
            dialer = {"type": dialer_client_type}
            if gost_type in ["ssh", "sshd"] and auth_token:
                dialer["auth"] = {
                    "username": "smite",
                    "password": auth_token
                }
            if spec.get("bypass_ips"):
                dialer["bypass"] = f"bypass-{tunnel_id}"
            if spec.get("dns_resolvers"):
                dialer["resolver"] = f"resolver-{tunnel_id}"
            
            # keepalive & socket metadata for stability
            if gost_type in ["kcp", "quic", "udp", "rudp"]:
                dialer_metadata.pop("keepAlive", None)
                dialer_metadata.pop("keepAliveInterval", None)
                dialer_metadata.pop("keepAliveTimeout", None)
                dialer_metadata.pop("idleTimeout", None)
                dialer_metadata.pop("timeout", None)
                if gost_type == "kcp":
                    dialer_metadata["nodelay"] = True
                    dialer_metadata["interval"] = "20ms"
                    dialer_metadata["resend"] = 2
                    dialer_metadata["nc"] = 1
                    kcp_key = spec.get("kcp_key") or auth_token or "smite-kcp-secure"
                    dialer_metadata["crypt"] = spec.get("kcp_crypt") or "aes"
                    dialer_metadata["key"] = kcp_key
                if enable_mux and gost_type not in ["udp", "rudp"]:
                    dialer_metadata["mux.type"] = mux_type
                    dialer_metadata["mux"] = True
                    dialer_metadata["nodelay"] = True
            else:
                keepalive_interval = f"{spec.get('keepalive_interval') or 15}s" if not str(spec.get('keepalive_interval', '')).endswith('s') else str(spec.get('keepalive_interval'))
                dialer_metadata["keepAlive"] = True
                dialer_metadata["keepAliveInterval"] = keepalive_interval
                dialer_metadata["keepAliveTimeout"] = "30s"
                dialer_metadata["timeout"] = "20s"
                dialer_metadata["idleTimeout"] = "120s"
                dialer_metadata["nodelay"] = True
                dialer_metadata["bufferSize"] = 65536
                if enable_mux:
                    dialer_metadata["mux.type"] = mux_type
                    dialer_metadata["mux"] = True
                    dialer_metadata["nodelay"] = True
            
            if dialer_metadata:
                dialer["metadata"] = dialer_metadata
            if (security_type in ["tls", "utls"] or gost_type in ["wss", "mwss", "tls", "mtls", "quic", "grpc"]) and gost_type not in ["udp", "kcp", "ssh", "sshd"]:
                if dialer_tls:
                    dialer["tls"] = dialer_tls
                else:
                    dialer["tls"] = {"secure": False}
                
            # Generate node objects for primary and failover IPs
            hop_nodes = []
            
            connector_metadata = {
                "bufferSize": 65536
            }
            if enable_mux:
                connector_metadata["mux.type"] = mux_type
                connector_metadata["mux"] = True
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
            raw_fips = spec.get("failover_ips") or []
            failover_ips = []
            if isinstance(raw_fips, str):
                failover_ips = [x.strip() for x in re.split(r'[\r\n,]+', raw_fips) if x.strip()]
            elif isinstance(raw_fips, list):
                for item in raw_fips:
                    if isinstance(item, str):
                        failover_ips.extend([x.strip() for x in re.split(r'[\r\n,]+', item) if x.strip()])
                    elif item:
                        failover_ips.append(str(item).strip())

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
                strategy = spec.get("selector_strategy") or spec.get("strategy") or "fifo"
                if strategy not in ["fifo", "round", "parallel", "rand", "hash"]:
                    strategy = "fifo"
                max_fails = int(spec.get("max_fails") or 2)
                fail_timeout = str(spec.get("fail_timeout") or "15s")
                if not str(fail_timeout).endswith("s"):
                    fail_timeout = f"{fail_timeout}s"

                hop_obj["selector"] = {
                    "strategy": strategy,
                    "maxFails": max_fails,
                    "failTimeout": fail_timeout
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
            default_target_address = spec.get("target_host") or '127.0.0.1'
            for port in ports:
                if isinstance(port, dict):
                    local_port = port.get('local_port') or port.get('local') or port.get('port')
                    target_address = port.get('target_address') or port.get('target_host') or default_target_address
                    target_port = port.get('target_port') or port.get('remote') or local_port
                    port_num = int(local_port) if isinstance(local_port, (int, str)) and str(local_port).isdigit() else local_port
                    target_port_num = int(target_port) if isinstance(target_port, (int, str)) and str(target_port).isdigit() else target_port
                elif isinstance(port, str) and "=" in port:
                    parts = port.split("=", 1)
                    port_num = int(parts[0].strip()) if parts[0].strip().isdigit() else parts[0].strip()
                    rhs = parts[1].strip()
                    if ":" in rhs:
                        target_address, tp = rhs.rsplit(":", 1)
                        target_port_num = int(tp) if tp.isdigit() else port_num
                    else:
                        target_address = default_target_address
                        target_port_num = int(rhs) if rhs.isdigit() else port_num
                else:
                    port_num = int(port) if isinstance(port, (int, str)) and str(port).isdigit() else port
                    target_address = default_target_address
                    target_port_num = port_num
                    
                target_addr_formatted = f"[{target_address}]:{target_port_num}" if ":" in target_address and not target_address.startswith("[") else f"{target_address}:{target_port_num}"
                listen_addr = f":{port_num}" if is_reverse else (f"[::]:{port_num}" if use_ipv6 else f"0.0.0.0:{port_num}")
                
                if tunnel_proto in ["tcp", "tcp+udp"]:
                    if is_reverse:
                        listener_tcp = {
                            "type": "rtcp",
                            "chain": f"chain-{tunnel_id}",
                            "metadata": {
                                "keepAlive": True,
                                "ttl": "10s",
                                "keepalive.idle": "10s",
                                "keepalive.interval": "10s",
                                "bufferSize": 65536
                            }
                        }
                        handler_tcp = {
                            "type": "tcp",
                            "retries": 3,
                            "metadata": {
                                "retryDelay": "1s"
                            }
                        }
                    else:
                        listener_tcp = {"type": "tcp"}
                        handler_tcp = {
                            "type": "tcp",
                            "chain": f"chain-{tunnel_id}",
                            "retries": 3,
                            "metadata": {
                                "retryDelay": "1s"
                            }
                        }
                    
                    service_tcp = {
                        "name": f"tcp-in-{port_num}-{tunnel_id}",
                        "addr": f":{port_num}" if is_reverse else listen_addr,
                        "handler": handler_tcp,
                        "listener": listener_tcp,
                        "forwarder": {
                            "nodes": [
                                {"name": f"target-tcp-{port_num}-{tunnel_id}", "addr": target_addr_formatted}
                            ]
                        }
                    }
                    if spec.get("rate_limit_mbps"):
                        service_tcp["limiter"] = f"limiter-{tunnel_id}"
                    
                    config["services"].append(service_tcp)
                
                if tunnel_proto in ["udp", "tcp+udp"]:
                    udp_handler_metadata = {
                        "ttl": "30s",
                        "readTimeout": "60s",
                        "bufferSize": 65536,
                        "retryDelay": "1s"
                    }
                    if is_reverse:
                        listener_udp = {
                            "type": "rudp",
                            "chain": f"chain-{tunnel_id}",
                            "metadata": {
                                "keepAlive": True,
                                "ttl": "10s",
                                "keepalive.idle": "10s",
                                "keepalive.interval": "10s",
                                "readTimeout": "60s",
                                "bufferSize": 65536
                            }
                        }
                        handler_udp = {
                            "type": "udp",
                            "retries": 3,
                            "metadata": udp_handler_metadata
                        }
                    else:
                        listener_udp = {
                            "type": "udp",
                            "metadata": {
                                "readTimeout": "60s",
                                "bufferSize": 65536
                            }
                        }
                        handler_udp = {
                            "type": "udp",
                            "chain": f"chain-{tunnel_id}",
                            "retries": 3,
                            "metadata": udp_handler_metadata
                        }
                    
                    service_udp = {
                        "name": f"udp-in-{port_num}-{tunnel_id}",
                        "addr": f":{port_num}" if is_reverse else listen_addr,
                        "handler": handler_udp,
                        "listener": listener_udp,
                        "forwarder": {
                            "nodes": [
                                {"name": f"target-udp-{port_num}-{tunnel_id}", "addr": target_addr_formatted}
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

        binary_path = self._resolve_binary_path()
        cmd = [str(binary_path), "-C", str(config_file)]
        
        log_file = self.config_dir / f"{tunnel_id}.log"
        if log_file.exists() and log_file.stat().st_size > 5 * 1024 * 1024:
            try:
                old_log = self.config_dir / f"{tunnel_id}.log.old"
                if old_log.exists():
                    old_log.unlink()
                log_file.rename(old_log)
            except Exception:
                try:
                    log_file.write_text("")
                except Exception:
                    pass
        log_f = open(log_file, 'w', buffering=1)
        try:
            log_f.write(f"Starting GOST v3 forwarding for tunnel {tunnel_id} (Mode: {mode})\n")
            log_f.write(f"Command: {' '.join(cmd)}\n")
            log_f.flush()
            
            proc = await _spawn_core_subprocess(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
            )
        except Exception as e:
            log_f.close()
            raise RuntimeError(f"Failed to start GOST: {e}")
        
        self.log_handles[tunnel_id] = log_f
        self.processes[tunnel_id] = proc
        _save_tunnel_pid(tunnel_id, proc.pid)
        
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
            _remove_tunnel_pid(tunnel_id)
            raise RuntimeError(f"GOST failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
        
        logger.info(f"GOST v3 forwarding started for tunnel {tunnel_id} (Mode: {mode})")
    
    async def remove(self, tunnel_id: str, purge: bool = False):
        """Remove GOST tunnel and release bound ports"""
        pid = _get_tunnel_pid(tunnel_id)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        config_file = self.config_dir / f"{tunnel_id}.json"
        ports_to_free: Set[int] = set()
        if config_file.exists():
            try:
                txt = config_file.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"addr"\s*:\s*["\']?(?:[^"\':\s]*:)?(\d{1,5})["\']?', txt, re.IGNORECASE):
                    p_val = int(m.group(1))
                    if 0 < p_val <= 65535:
                        ports_to_free.add(p_val)
            except Exception:
                pass

        await safe_stop_subprocess(proc, patterns=[tunnel_id, f"{tunnel_id}.json"], pid=pid)
        _remove_tunnel_pid(tunnel_id)

        if config_file.exists():
            try:
                config_file.unlink()
            except Exception:
                pass

        if purge:
            for lf_name in [f"{tunnel_id}.log", f"{tunnel_id}.log.old"]:
                log_file = self.config_dir / lf_name
                if log_file.exists():
                    try:
                        log_file.unlink()
                    except Exception:
                        pass

        for extra in [
            self.config_dir / f"cert_{tunnel_id}.pem",
            self.config_dir / f"key_{tunnel_id}.pem",
            self.config_dir / f"ssh_host_key_{tunnel_id}.pem",
        ]:
            if extra.exists():
                try:
                    extra.unlink()
                except Exception:
                    pass

        if ports_to_free:
            await free_ports(ports_to_free)
    
    def status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get status"""
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "gost")
        
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
        self._tunnel_locks: Dict[str, asyncio.Lock] = {}
        self._io_lock = threading.Lock()
        logger.info(f"Tunnel persistence file: {self.tunnels_file}")
    
    def _get_tunnel_lock(self, tunnel_id: str) -> asyncio.Lock:
        if tunnel_id not in self._tunnel_locks:
            self._tunnel_locks[tunnel_id] = asyncio.Lock()
        return self._tunnel_locks[tunnel_id]

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
        with self._io_lock:
            temp_file = None
            try:
                logger.info(f"Saving {len(self.tunnel_configs)} tunnel configurations to {self.tunnels_file}")
                
                temp_file = self.tunnels_file.with_name(f"tunnels_{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp")
                with open(temp_file, 'w') as f:
                    json.dump(self.tunnel_configs, f, indent=2)
                    f.flush()
                try:
                    os.chmod(temp_file, 0o600)
                except Exception:
                    pass
                temp_file.replace(self.tunnels_file)
                temp_file = None
                
                if self.tunnels_file.exists():
                    file_size = self.tunnels_file.stat().st_size
                    logger.info(f"Successfully saved tunnel configurations to {self.tunnels_file} (size: {file_size} bytes, tunnels: {list(self.tunnel_configs.keys())})")
                else:
                    logger.error(f"File {self.tunnels_file} was not created after write operation")
            except Exception as e:
                logger.error(f"Failed to save tunnel configurations to {self.tunnels_file}: {e}", exc_info=True)
            finally:
                if temp_file and temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
    
    async def restore_tunnels(self):
        """Restore all persisted tunnels on startup"""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"Starting tunnel restoration from {self.tunnels_file}")
        logger.info(f"Config directory exists: {self.config_dir.exists()}, writable: {os.access(self.config_dir, os.W_OK) if self.config_dir.exists() else False}")
        logger.info(f"Tunnels file exists: {self.tunnels_file.exists()}")
        
        self._load_tunnels()
        self.start_watchdog()
        
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
                
                # Check if process is ALREADY running and healthy (non-destructive adoption)
                tunnel_status = adapter.status(tunnel_id)
                if tunnel_status.get("process_running", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                    self.active_tunnels[tunnel_id] = adapter
                    restored += 1
                    logger.info(f"Tunnel {tunnel_id} core ({tunnel_core}) is ALREADY running healthy (PID {_get_tunnel_pid(tunnel_id)}). Preserved connection without restart.")
                    continue

                mode = spec.get('mode', 'N/A')
                logger.info(f"Restoring tunnel {tunnel_id}: core={tunnel_core}, mode={mode}, spec_keys={list(spec.keys())}")
                
                if tunnel_core in ["rathole", "backhaul", "chisel", "frp", "gost"] and mode == 'N/A':
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

    @staticmethod
    def _extract_spec_ports(spec: Dict[str, Any]) -> Set[int]:
        """Extract all service and control ports defined in a tunnel spec"""
        ports: Set[int] = set()
        if not spec or not isinstance(spec, dict):
            return ports
        raw_ports = spec.get("ports", [])
        if isinstance(raw_ports, list):
            for p in raw_ports:
                if isinstance(p, (int, str)) and str(p).isdigit() and int(p) > 0:
                    ports.add(int(p))
                elif isinstance(p, str) and "=" in p:
                    lhs = p.split("=", 1)[0].strip()
                    if lhs.isdigit() and int(lhs) > 0:
                        ports.add(int(lhs))
                    elif "-" in lhs:
                        parts = lhs.split("-", 1)
                        if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                            start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                            if 0 <= end_p - start_p <= 500:
                                ports.update(range(start_p, end_p + 1))
                elif isinstance(p, str) and "-" in p:
                    parts = p.split("-", 1)
                    if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                        if 0 <= end_p - start_p <= 500:
                            ports.update(range(start_p, end_p + 1))
                elif isinstance(p, dict):
                    for k in ("remote", "remote_port", "local", "local_port", "port", "listen_port"):
                        v = p.get(k)
                        if v and str(v).isdigit() and int(v) > 0:
                            ports.add(int(v))
        elif isinstance(raw_ports, str):
            for p in re.split(r'[\r\n,]+', raw_ports):
                p = p.strip()
                if "=" in p:
                    p = p.split("=", 1)[0].strip()
                if p.isdigit() and int(p) > 0:
                    ports.add(int(p))
                elif "-" in p:
                    parts = p.split("-", 1)
                    if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                        if 0 <= end_p - start_p <= 500:
                            ports.update(range(start_p, end_p + 1))

        # Also extract port_ranges
        raw_ranges = spec.get("port_ranges")
        if raw_ranges:
            range_list = [x.strip() for x in re.split(r'[\r\n,]+', raw_ranges) if x.strip()] if isinstance(raw_ranges, str) else raw_ranges
            for pr in range_list:
                if isinstance(pr, str) and "-" in pr:
                    parts = pr.split("-", 1)
                    if parts[0].strip().isdigit() and parts[1].strip().isdigit():
                        start_p, end_p = int(parts[0].strip()), int(parts[1].strip())
                        if 0 < end_p - start_p <= 500:
                            ports.update(range(start_p, end_p + 1))

        for k in ["proxy_port", "remote_port", "listen_port", "bind_port", "control_port", "server_port", "vhost_http_port", "vhost_https_port"]:
            val = spec.get(k)
            if val and str(val).isdigit() and int(val) > 0:
                ports.add(int(val))
        bind_addr = str(spec.get("bind_addr", ""))
        if ":" in bind_addr:
            port_str = bind_addr.split(":")[-1]
            if port_str.isdigit() and int(port_str) > 0:
                ports.add(int(port_str))
        return ports

    async def _watchdog_loop(self):
        """Continuous Self-Healing Watchdog: inspects tunnel processes every 15s and auto-recovers dead tunnels with exponential backoff and port conflict safety"""
        logger.info("AdapterManager self-healing watchdog loop started (interval: 15s)")
        backoff_delay: Dict[str, int] = {}
        next_retry_at: Dict[str, float] = {}
        last_log_sweep: float = 0.0
        
        while True:
            try:
                await asyncio.sleep(15)
                now = time.time()

                # Periodic log truncation check (every 5 minutes / 300s) to prevent VPS disk exhaustion
                if now - last_log_sweep >= 300:
                    last_log_sweep = now
                    try:
                        log_patterns = list(self.config_dir.glob("*.log")) + list(self.config_dir.glob("*/*.log"))
                        for log_p in log_patterns:
                            if log_p.is_file():
                                try:
                                    if log_p.stat().st_size > 10 * 1024 * 1024:
                                        with open(log_p, "r+", encoding="utf-8", errors="ignore") as lf:
                                            lf.seek(-256 * 1024, os.SEEK_END)
                                            tail = lf.read()
                                            lf.seek(0)
                                            lf.write(f"[LOG ROTATED BY SMITE WATCHDOG AT {time.ctime()}]\n" + tail)
                                            lf.truncate()
                                            lf.flush()
                                        logger.info(f"Watchdog: Rotated oversized log file {log_p} to last 256KB")
                                except Exception as e_rot:
                                    logger.debug(f"Watchdog: Could not rotate log file {log_p}: {e_rot}")
                    except Exception as e_sweep:
                        logger.debug(f"Watchdog: Error during log sweep: {e_sweep}")

                for tunnel_id in list(self.tunnel_configs.keys()):
                    # Respect exponential backoff window
                    if tunnel_id in next_retry_at and now < next_retry_at[tunnel_id]:
                        continue

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
                        # 1. Port-conflict protection check before reviving dead tunnel
                        dead_tunnel_ports = self._extract_spec_ports(spec)
                        conflicting_active_tunnel = None
                        
                        if dead_tunnel_ports:
                            for other_id, other_adapter in list(self.active_tunnels.items()):
                                if other_id == tunnel_id:
                                    continue
                                try:
                                    other_st = other_adapter.status(other_id)
                                    if other_st.get("process_running", False) or other_st.get("active", False):
                                        other_spec = self.tunnel_configs.get(other_id, {}).get("spec", {})
                                        other_ports = self._extract_spec_ports(other_spec)
                                        collision = dead_tunnel_ports.intersection(other_ports)
                                        if collision:
                                            conflicting_active_tunnel = (other_id, sorted(list(collision))[0])
                                            break
                                except Exception:
                                    pass
                        
                        if conflicting_active_tunnel:
                            logger.error(
                                f"Watchdog: SAFETY ABORT - Cannot auto-recover tunnel {tunnel_id} ({tunnel_core}) "
                                f"because port {conflicting_active_tunnel[1]} is currently in use by ACTIVE tunnel "
                                f"{conflicting_active_tunnel[0]}! Skipping recovery to prevent process kill loop."
                            )
                            cur_delay = backoff_delay.get(tunnel_id, 30)
                            next_delay = min(cur_delay * 2, 300)
                            backoff_delay[tunnel_id] = next_delay
                            next_retry_at[tunnel_id] = now + next_delay
                            continue
                        
                        # 2. Proceed with safe recovery
                        cur_delay = backoff_delay.get(tunnel_id, 15)
                        logger.warning(f"Watchdog: tunnel {tunnel_id} ({tunnel_core}) is inactive/dead! Auto-recovering...")
                        try:
                            async with self._get_tunnel_lock(tunnel_id):
                                status = adapter.status(tunnel_id)
                                if not (status.get("process_running", False) or status.get("active", False)):
                                    # Cleanly remove old process/sockets and reapply
                                    await adapter.remove(tunnel_id)
                                    await asyncio.sleep(0.2)
                                    await adapter.apply(tunnel_id, spec)
                                    self.active_tunnels[tunnel_id] = adapter
                            backoff_delay.pop(tunnel_id, None)
                            next_retry_at.pop(tunnel_id, None)
                            logger.info(f"Watchdog: successfully revived and restored tunnel {tunnel_id} ({tunnel_core})")
                        except Exception as e:
                            logger.error(f"Watchdog: failed to auto-recover tunnel {tunnel_id}: {e}")
                            next_delay = min(cur_delay * 2, 120)
                            backoff_delay[tunnel_id] = next_delay
                            next_retry_at[tunnel_id] = now + next_delay
                    else:
                        backoff_delay.pop(tunnel_id, None)
                        next_retry_at.pop(tunnel_id, None)
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
    
    async def _remove_tunnel_unlocked(
        self,
        tunnel_id: str,
        purge: bool = False,
        ports: Optional[Iterable[Any]] = None,
        control_port: Optional[int] = None,
        core: Optional[str] = None
    ):
        """Internal unlocked tunnel removal helper with guaranteed complete process kill and port freeing"""
        logger.info(f"Removing tunnel {tunnel_id} (purge={purge}, core={core})")

        # 1. Gather all potential ports used by this tunnel
        target_ports: Set[int] = set()

        if ports:
            for p in ports:
                try:
                    p_int = int(p)
                    if 0 < p_int <= 65535:
                        target_ports.add(p_int)
                except (ValueError, TypeError):
                    pass

        if control_port:
            try:
                cp_int = int(control_port)
                if 0 < cp_int <= 65535:
                    target_ports.add(cp_int)
            except (ValueError, TypeError):
                pass

        # From stored config in memory
        saved_spec = self.tunnel_configs.get(tunnel_id, {}).get("spec", {})
        if saved_spec:
            target_ports.update(_extract_ports_from_spec_dict(saved_spec))

        # From all disk configuration files
        core_dirs = [
            Path("/etc/smite-node/rathole"),
            Path("/etc/smite-node/backhaul"),
            Path("/etc/smite-node/gost"),
            Path("/etc/smite-node/frp"),
            Path("/etc/smite-node/chisel"),
        ]
        for cdir in core_dirs:
            if not cdir.exists():
                continue
            try:
                for fpath in cdir.glob(f"*{tunnel_id}*"):
                    if fpath.is_file() and fpath.suffix in (".toml", ".json", ".yaml", ".yml"):
                        try:
                            content = fpath.read_text(encoding="utf-8", errors="ignore")
                            for m in re.finditer(r'(?:bind_addr|remote_addr|local_addr|addr|port|listen_port|proxy_port|remotePort|localPort|bindPort)\s*[:=]\s*["\']?(?:[^"\':\s]*:)?(\d{1,5})["\']?', content, re.IGNORECASE):
                                p_num = int(m.group(1))
                                if 0 < p_num <= 65535:
                                    target_ports.add(p_num)
                            for m in re.finditer(r'["\'](\d{1,5})/(?:tcp|udp)["\']', content, re.IGNORECASE):
                                p_num = int(m.group(1))
                                if 0 < p_num <= 65535:
                                    target_ports.add(p_num)
                        except Exception:
                            pass
            except Exception:
                pass

        # 2. Stop through primary adapter
        primary_adapter = self.active_tunnels.pop(tunnel_id, None)
        if not primary_adapter:
            t_core = core or self.tunnel_configs.get(tunnel_id, {}).get("core")
            if t_core:
                primary_adapter = self.get_adapter(t_core)

        if primary_adapter:
            try:
                await primary_adapter.remove(tunnel_id, purge=purge)
            except TypeError:
                await primary_adapter.remove(tunnel_id)
            except Exception as e:
                logger.warning(f"Error in primary adapter remove for {tunnel_id}: {e}")

        # 3. Check and clean across ALL adapters (prevents ghost instances if core changed)
        for c_name, adapter in list(self.adapters.items()):
            if adapter is primary_adapter:
                continue
            has_proc = tunnel_id in getattr(adapter, "processes", {})
            has_disk_cfg = hasattr(adapter, "config_dir") and any(adapter.config_dir.glob(f"*{tunnel_id}*"))
            if has_proc or has_disk_cfg or _is_tunnel_pid_alive(tunnel_id, c_name):
                try:
                    logger.info(f"Purging secondary adapter instance for tunnel {tunnel_id} under core {c_name}")
                    await adapter.remove(tunnel_id, purge=purge)
                except Exception as e:
                    logger.warning(f"Error in secondary adapter remove for {tunnel_id} ({c_name}): {e}")

        # 4. Sweep process table for any orphan core process matching tunnel_id
        patterns = [tunnel_id, f"{tunnel_id}.toml", f"{tunnel_id}.json", f"{tunnel_id}.yaml"]
        rec_pid = _get_tunnel_pid(tunnel_id)
        await safe_stop_subprocess(patterns=patterns, pid=rec_pid)
        _remove_tunnel_pid(tunnel_id)

        prov_pid = _get_tunnel_pid(f"{tunnel_id}_provider")
        if prov_pid:
            await safe_stop_subprocess(pid=prov_pid)
            _remove_tunnel_pid(f"{tunnel_id}_provider")

        # 5. Free and verify all target ports
        if target_ports:
            logger.info(f"Freeing and verifying ports {sorted(list(target_ports))} for tunnel {tunnel_id}")
            await free_ports(target_ports)
            await asyncio.sleep(0.2)

            # Verification pass: check if any proxy process is still holding any port
            for p in target_ports:
                held_pids = _find_pids_by_port_procfs(p)
                for hp in held_pids:
                    if _is_safe_core_process(hp):
                        logger.warning(f"Verification: killing core process {hp} still holding port {p}")
                        try:
                            os.kill(hp, signal.SIGKILL)
                        except Exception:
                            pass

            if os.name == 'posix':
                for p in target_ports:
                    try:
                        proc_kill = await asyncio.create_subprocess_exec(
                            "ss", "-K", f"( sport = :{p} or dport = :{p} )",
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL
                        )
                        await asyncio.wait_for(proc_kill.wait(), timeout=0.5)
                    except Exception:
                        pass

        # 6. Delete config and persist
        if tunnel_id in self.tunnel_configs:
            del self.tunnel_configs[tunnel_id]
            self._save_tunnels()

        logger.info(f"Tunnel {tunnel_id} removal complete. Ports freed: {sorted(list(target_ports))}")

    async def apply_tunnel(self, tunnel_id: str, tunnel_core: str, spec: Dict[str, Any]):
        """Apply tunnel using appropriate adapter - idempotent and zero-downtime if spec is unchanged"""
        async with self._get_tunnel_lock(tunnel_id):
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Applying tunnel {tunnel_id}: core={tunnel_core}")
            
            adapter = self.get_adapter(tunnel_core)
            if not adapter:
                error_msg = f"Unknown tunnel core: {tunnel_core}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            
            # Check if already running with exact same configuration (Idempotent Apply)
            force_restart = bool(spec.get("force_restart", False))
            if tunnel_id in self.active_tunnels and not force_restart:
                existing_config = self.tunnel_configs.get(tunnel_id, {})
                if existing_config.get("core") == tunnel_core and existing_config.get("spec") == spec:
                    t_status = adapter.status(tunnel_id)
                    if t_status.get("process_running", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                        logger.info(f"Tunnel {tunnel_id} ({tunnel_core}) is already active and healthy with identical configuration. Skipping restart to keep traffic 100% uninterrupted.")
                        return
                
                logger.info(f"Tunnel {tunnel_id} configuration changed or process inactive, applying new configuration")
                if tunnel_id in self.active_tunnels:
                    old_adapter = self.active_tunnels[tunnel_id]
                    try:
                        await old_adapter.remove(tunnel_id)
                    except Exception as e:
                        logger.warning(f"Error removing old adapter process for tunnel {tunnel_id}: {e}")
                    del self.active_tunnels[tunnel_id]
            
            adapter_name = getattr(adapter, "name", tunnel_core)
            logger.info(f"Using adapter: {adapter_name}, mode={spec.get('mode', 'N/A')}")
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
    
    async def remove_tunnel(
        self,
        tunnel_id: str,
        purge: bool = False,
        ports: Optional[Iterable[Any]] = None,
        control_port: Optional[int] = None,
        core: Optional[str] = None
    ):
        """Remove tunnel and guarantee complete process termination and port freeing"""
        async with self._get_tunnel_lock(tunnel_id):
            await self._remove_tunnel_unlocked(
                tunnel_id,
                purge=purge,
                ports=ports,
                control_port=control_port,
                core=core
            )
        self._tunnel_locks.pop(tunnel_id, None)
    
    async def get_tunnel_status(self, tunnel_id: str) -> Dict[str, Any]:
        """Get tunnel status with non-destructive live adoption fallback"""
        if tunnel_id in self.active_tunnels:
            adapter = self.active_tunnels[tunnel_id]
            return adapter.status(tunnel_id)
        
        # Fallback 1: check persisted configs and live PID
        if tunnel_id in self.tunnel_configs:
            tunnel_core = self.tunnel_configs[tunnel_id].get("core")
            if tunnel_core:
                adapter = self.get_adapter(tunnel_core)
                if adapter:
                    st = adapter.status(tunnel_id)
                    if st.get("process_running", False) or st.get("active", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                        self.active_tunnels[tunnel_id] = adapter
                        return st
        
        # Fallback 2: check all registered adapters for living PID
        for core_name, adapter in self.adapters.items():
            if _is_tunnel_pid_alive(tunnel_id, core_name):
                st = adapter.status(tunnel_id)
                self.active_tunnels[tunnel_id] = adapter
                return st

        return {"active": False}
    
    async def inspect_tunnel_health(
        self,
        tunnel_id: str,
        tunnel_core: Optional[str] = None,
        mode: str = "server",
        ports: Optional[List[int]] = None,
        control_port: Optional[int] = None,
        proto: str = "udp"
    ) -> Dict[str, Any]:
        """Inspect running process and listening sockets for a tunnel with high precision"""
        config = self.tunnel_configs.get(tunnel_id, {})
        core = tunnel_core or config.get("core", "")
        spec = config.get("spec", {})
        actual_mode = mode or spec.get("mode", "server")
        
        adapter = self.active_tunnels.get(tunnel_id) or (self.get_adapter(core) if core else None)
        proc_alive = False
        if adapter:
            st = adapter.status(tunnel_id)
            proc_alive = bool(st.get("process_running", False) or st.get("active", False))
        if not proc_alive:
            proc_alive = bool(_is_tunnel_pid_alive(tunnel_id, core))
            
        checked_ports = ports or spec.get("ports", [])
        if not checked_ports and spec.get("remote_port"):
            checked_ports = [spec.get("remote_port")]
            
        ctrl_port = control_port or spec.get("control_port") or spec.get("bind_port")
        
        listening_ports = []
        missing_ports = []
        
        if actual_mode == "server":
            transport = (spec.get("transport_type") or spec.get("transport") or spec.get("gost_type") or "").lower()
            ctrl_proto = "udp" if transport in ["quic", "kcp", "udp", "rudp"] else "tcp"
            if ctrl_port:
                is_ctrl_listening = await asyncio.to_thread(is_port_listening_locally, int(ctrl_port), proto=ctrl_proto)
                if not is_ctrl_listening and ctrl_proto == "udp":
                    is_ctrl_listening = await asyncio.to_thread(is_port_listening_locally, int(ctrl_port), proto="tcp")
                if is_ctrl_listening:
                    listening_ports.append({"port": int(ctrl_port), "type": f"control_{ctrl_proto}"})
                else:
                    missing_ports.append({"port": int(ctrl_port), "type": f"control_{ctrl_proto}"})
                    
            # In direct GOST/Chisel/FRP server mode, the server only terminates control_port.
            # Service ports listen on the Iran client side.
            skip_service_ports = (core in ["gost", "chisel", "frp"] and not spec.get("is_reverse", False))
            if not skip_service_ports:
                for p in checked_ports:
                    try:
                        p_num = None
                        if isinstance(p, (int, str)) and str(p).isdigit():
                            p_num = int(p)
                        elif isinstance(p, str) and "=" in p:
                            left = p.split("=", 1)[0].strip()
                            if left.isdigit():
                                p_num = int(left)
                            elif "-" in left:
                                parts = left.split("-", 1)
                                if parts[0].strip().isdigit():
                                    p_num = int(parts[0].strip())
                        elif isinstance(p, str) and "-" in p:
                            parts = p.split("-", 1)
                            if parts[0].strip().isdigit():
                                p_num = int(parts[0].strip())
                        elif isinstance(p, dict):
                            p_val = p.get("remote") or p.get("remote_port") or p.get("port") or p.get("listen_port") or p.get("local_port")
                            if p_val and str(p_val).isdigit():
                                p_num = int(p_val)
                        if p_num:
                            eff_proto = "any" if (spec.get("tunnel_type") in ["tcp+udp", "all"] or spec.get("type") in ["tcp+udp", "all"] or proto in ["any", "tcp+udp"]) else proto
                            is_svc_listening = await asyncio.to_thread(is_port_listening_locally, p_num, proto=eff_proto)
                            if is_svc_listening:
                                listening_ports.append({"port": p_num, "type": f"service_{eff_proto}"})
                            else:
                                missing_ports.append({"port": p_num, "type": f"service_{eff_proto}"})
                    except Exception:
                        pass
        elif actual_mode == "client" and core in ["gost", "chisel", "frp"] and not spec.get("is_reverse", False):
            # Direct GOST / Chisel / FRP tunnel: Iran client node listens locally on service ports
            for p in checked_ports:
                try:
                    p_num = None
                    if isinstance(p, (int, str)) and str(p).isdigit():
                        p_num = int(p)
                    elif isinstance(p, str) and "=" in p:
                        left = p.split("=", 1)[0].strip()
                        if left.isdigit():
                            p_num = int(left)
                        elif "-" in left:
                            parts = left.split("-", 1)
                            if parts[0].strip().isdigit():
                                p_num = int(parts[0].strip())
                    elif isinstance(p, str) and "-" in p:
                        parts = p.split("-", 1)
                        if parts[0].strip().isdigit():
                            p_num = int(parts[0].strip())
                    elif isinstance(p, dict):
                        p_val = p.get("local_port") or p.get("local") or p.get("port") or p.get("listen_port") or p.get("remote") or p.get("remote_port")
                        if p_val and str(p_val).isdigit():
                            p_num = int(p_val)
                    if p_num:
                        eff_proto = "any" if (spec.get("tunnel_type") in ["tcp+udp", "all"] or spec.get("type") in ["tcp+udp", "all"] or proto in ["any", "tcp+udp"]) else proto
                        is_svc_listening = await asyncio.to_thread(is_port_listening_locally, p_num, proto=eff_proto)
                        if is_svc_listening:
                            listening_ports.append({"port": p_num, "type": f"service_{eff_proto}"})
                        else:
                            missing_ports.append({"port": p_num, "type": f"service_{eff_proto}"})
                except Exception:
                    pass
                        
        is_healthy = proc_alive and (len(missing_ports) == 0)
        
        return {
            "healthy": is_healthy,
            "process_running": proc_alive,
            "mode": actual_mode,
            "core": core,
            "listening_ports": listening_ports,
            "missing_ports": missing_ports
        }

    async def cleanup(self, kill_processes: bool = False):
        """Cleanup on agent shutdown. Preserves running background proxy processes for zero-downtime adoption on next startup."""
        self.stop_watchdog()
        if kill_processes:
            for tunnel_id, adapter in list(self.active_tunnels.items()):
                try:
                    await adapter.remove(tunnel_id)
                except Exception:
                    pass
        self.active_tunnels.clear()

