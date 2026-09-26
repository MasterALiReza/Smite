"""Core adapters for different tunnel types"""
from typing import Protocol, Dict, Any, Optional, List, Set
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
        "server_key", "client_key"
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
        return True
    except Exception:
        return False


async def free_port(port: Optional[Any]) -> None:
    """Safely terminate any core proxy process holding the specified port without affecting system or node services."""
    if not port:
        return
    try:
        port_num = int(port)
    except (ValueError, TypeError):
        return
    if port_num <= 0:
        return
    
    current_pid = os.getpid()
    ignored_pids = {current_pid, os.getppid(), 1}
    
    # Method 1: Linux /proc/net + /proc/{pid}/fd scan
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

    # Method 2: psutil process and connection scanning
    try:
        for p in psutil.process_iter(['pid', 'name']):
            if p.pid in ignored_pids:
                continue
            try:
                if not _is_safe_core_process(p.pid):
                    continue
                for conn in p.net_connections(kind='all'):
                    if conn.laddr and conn.laddr.port == port_num:
                        logger.warning(f"psutil: Terminating core process {p.pid} ({p.name()}) holding port {port_num}")
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
        logger.debug(f"Error freeing port {port_num} via psutil: {e}")

    # Method 3: Tear down lingering half-open kernel TCP sockets on Linux via ss -K (SOCK_DESTROY)
    if os.name == 'posix':
        try:
            proc_kill = await asyncio.create_subprocess_exec(
                "ss", "-K", f"( sport = :{port_num} or dport = :{port_num} )",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc_kill.wait(), timeout=1.0)
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
                my_pgid = os.getpgid(os.getpid())
                if pgid != my_pgid and pgid > 1:
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
                    my_pgid = os.getpgid(os.getpid())
                    if pgid != my_pgid and pgid > 1:
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception:
                    pass
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=1.5)
            except Exception:
                pass

    if patterns:
        current_pid = os.getpid()
        ignored_pids = {current_pid, os.getppid(), 1}
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
            
            config = f"""[server]
bind_addr = "{bind_host}:{bind_port}"
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
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config)
            
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
    
    async def remove(self, tunnel_id: str):
        """Remove Rathole tunnel"""
        _remove_tunnel_pid(tunnel_id)
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[tunnel_id, f"{tunnel_id}.toml"])
            
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
            is_udp = (
                spec.get("accept_udp") is True
                or spec.get("type") in ("udp", "tcp+udp")
                or spec.get("tunnel_type") in ("udp", "tcp+udp")
                or transport == "udp"
            )
            if is_udp:
                transport = "tcp"
            elif transport == "udp" or transport not in {"tcp", "ws", "wsmux", "tcpmux"}:
                transport = "tcpmux"
            
            server_options = dict(spec.get("server_options") or {})
            bind_addr = spec.get("bind_addr")
            if not bind_addr:
                control_port = spec.get("control_port") or spec.get("listen_port") or 3080
                bind_ip = spec.get("bind_ip", "0.0.0.0")
                bind_addr = f"{bind_ip}:{control_port}"
            
            bind_host, bind_port_num, _ = parse_address_port(bind_addr)
            if bind_port_num:
                await free_port(bind_port_num)
            
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

            if is_udp:
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
            
            config_path = self.config_dir / f"{tunnel_id}.toml"
            config_path.write_text(self._render_toml({"server": server_config}), encoding="utf-8")

            binary_path = self._resolve_binary_path()
            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul server for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml(sanitize_spec_for_log({"server": server_config})))
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
            is_udp = (
                spec.get("accept_udp") is True
                or spec.get("type") in ("udp", "tcp+udp")
                or spec.get("tunnel_type") in ("udp", "tcp+udp")
                or transport == "udp"
            )
            if is_udp:
                transport = "tcp"
            elif transport == "udp" or transport not in {"tcp", "ws", "wsmux", "tcpmux"}:
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
                config_dict[key] = value

            if is_udp:
                config_dict["accept_udp"] = True

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
            config_path.write_text(self._render_toml({"client": config_dict}), encoding="utf-8")

            binary_path = self._resolve_binary_path()

            log_path = self.config_dir / f"backhaul_{tunnel_id}.log"
            log_fh = log_path.open("w", buffering=1)
            log_fh.write(f"Starting Backhaul client for tunnel {tunnel_id}\n")
            log_fh.write(self._render_toml({"client": sanitize_spec_for_log(config_dict)}))
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
            _remove_tunnel_pid(tunnel_id)
            raise RuntimeError(f"backhaul failed to start: {error_output}")

        self.processes[tunnel_id] = proc
        _save_tunnel_pid(tunnel_id, proc.pid)
        self.log_handles[tunnel_id] = log_fh

    async def remove(self, tunnel_id: str):
        _remove_tunnel_pid(tunnel_id)
        config_path = self.config_dir / f"{tunnel_id}.toml"
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[tunnel_id])

        if config_path.exists():
            try:
                config_path.unlink()
            except Exception:
                pass

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
            control_port = spec.get('control_port') or spec.get('listen_port') or 8080
            await free_port(control_port)
            auth_token = spec.get('token') or spec.get('auth_token')
            key = spec.get('key')
            reverse_only = spec.get('reverse_only', True)
            
            cmd = ["chisel", "server", "--port", str(control_port)]
            if auth_token:
                cmd.extend(["--auth", auth_token])
            if key:
                cmd.extend(["--key", key])
            if reverse_only:
                cmd.append("--reverse")
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel server for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {sanitize_cmd_for_log(cmd)}\n")
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
            server_url = spec.get('server_url') or spec.get('remote_addr') or spec.get('server_addr')
            if not server_url:
                raise ValueError("Chisel client requires 'server_url' or 'remote_addr' in spec")
            
            if not server_url.startswith("http://") and not server_url.startswith("https://"):
                server_url = f"http://{server_url}"
            
            auth_token = spec.get('token') or spec.get('auth_token')
            fingerprint = spec.get('fingerprint')
            keepalive = spec.get('keepalive', '25s')
            max_retry_count = spec.get('max_retry_count')
            max_retry_interval = spec.get('max_retry_interval')
            
            reverse_specs = []
            ports = spec.get('ports') or []
            if not ports:
                local_port = spec.get('local_port') or spec.get('port')
                remote_port = spec.get('remote_port')
                if local_port and remote_port:
                    reverse_specs.append(f"R:{remote_port}:127.0.0.1:{local_port}")
                elif local_port:
                    reverse_specs.append(f"R:{local_port}:127.0.0.1:{local_port}")
            else:
                for port_item in ports:
                    if isinstance(port_item, dict):
                        local_p = port_item.get('local_port') or port_item.get('port')
                        remote_p = port_item.get('remote_port') or local_p
                        if local_p:
                            reverse_specs.append(f"R:{remote_p}:127.0.0.1:{local_p}")
                    elif isinstance(port_item, str) and ":" in port_item:
                        parts = port_item.split(":")
                        if len(parts) == 2:
                            remote_p, local_p = parts
                            reverse_specs.append(f"R:{remote_p}:127.0.0.1:{local_p}")
                    else:
                        port_num = int(port_item) if isinstance(port_item, (int, str)) and str(port_item).isdigit() else port_item
                        reverse_specs.append(f"R:{port_num}:127.0.0.1:{port_num}")
            
            if not reverse_specs:
                raise ValueError("Chisel client requires at least one port mapping (R:remote:local)")
            
            cmd = ["chisel", "client"]
            if auth_token:
                cmd.extend(["--auth", auth_token])
            if fingerprint:
                cmd.extend(["--fingerprint", fingerprint])
            if keepalive:
                cmd.extend(["--keepalive", keepalive])
            if max_retry_count is not None:
                cmd.extend(["--max-retry-count", str(max_retry_count)])
            if max_retry_interval:
                cmd.extend(["--max-retry-interval", max_retry_interval])
            
            cmd.append(server_url)
            cmd.extend(reverse_specs)
            
            log_file = self.config_dir / f"{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel client for tunnel {tunnel_id}\n")
                log_f.write(f"Command: {sanitize_cmd_for_log(cmd)}\n")
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
    
    async def remove(self, tunnel_id: str):
        """Remove Chisel tunnel"""
        _remove_tunnel_pid(tunnel_id)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(proc, patterns=[tunnel_id])
    
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
                config_content += """  tls:
    enable: true
    disableCustomTLSFirstByte: true
"""
                if custom_sni:
                    config_content += f"""    serverName: "{custom_sni}"\n"""

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
            raise RuntimeError(f"FRP failed to start: {stderr[-500:] if len(stderr) > 500 else stderr}")
    
    async def remove(self, tunnel_id: str):
        """Remove FRP tunnel (handles both server and client modes)"""
        _remove_tunnel_pid(tunnel_id)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        await safe_stop_subprocess(
            proc,
            patterns=[tunnel_id]
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
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "frp")
        
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
        transport_type = (spec.get('transport_type') or spec.get('transport') or 'tcp').lower()
        security_type = (spec.get('security_type') or 'none').lower()
        use_ipv6 = spec.get('use_ipv6', False)
        
        if transport_type in ["multiplex ws", "multiplex_ws"]:
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
            if control_port:
                await free_port(control_port)
            bind_addr = f"[::]:{control_port}" if use_ipv6 else f"0.0.0.0:{control_port}"
            
            # Handler & Protocol Selection
            handler_type = spec.get("handler_type") or "relay"
            mux_type = spec.get("mux_type") or "yamux"
            
            keepalive_interval = f"{spec.get('keepalive_interval') or 15}s" if not str(spec.get('keepalive_interval', '')).endswith('s') else str(spec.get('keepalive_interval'))
            listener_metadata = {
                "keepAlive": True,
                "keepAliveInterval": keepalive_interval,
                "keepAliveTimeout": "60s",
                "idleTimeout": "0s",
                "nodelay": True,
            }
            if spec.get("ws_path"):
                listener_metadata["path"] = spec.get("ws_path")
            if is_reverse:
                listener_metadata["bind"] = True
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                listener_metadata["mux.type"] = mux_type
                listener_metadata["nodelay"] = True
            if gost_type == "kcp":
                listener_metadata["nodelay"] = True
                listener_metadata["interval"] = "20ms"
                listener_metadata["resend"] = 2
                listener_metadata["nc"] = 1
                
            server_listener_type = "sshd" if gost_type == "ssh" else gost_type
            listener = {"type": server_listener_type}
            if listener_metadata:
                listener["metadata"] = listener_metadata
            
            if (security_type in ["tls", "utls"] or gost_type in ["wss", "mwss", "tls", "quic", "grpc"]) and gost_type not in ["tcp", "udp", "rtcp", "rudp", "kcp", "ssh", "sshd"]:
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
            
            for p in ports:
                await free_port(p)
                
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
                
            dialer_client_type = "ssh" if gost_type in ["ssh", "sshd"] else gost_type
            dialer = {"type": dialer_client_type}
            if spec.get("bypass_ips"):
                dialer["bypass"] = f"bypass-{tunnel_id}"
            if spec.get("dns_resolvers"):
                dialer["resolver"] = f"resolver-{tunnel_id}"
            
            # keepalive & socket metadata for stability
            keepalive_interval = f"{spec.get('keepalive_interval') or 15}s" if not str(spec.get('keepalive_interval', '')).endswith('s') else str(spec.get('keepalive_interval'))
            dialer_metadata["keepAlive"] = True
            dialer_metadata["keepAliveInterval"] = keepalive_interval
            dialer_metadata["keepAliveTimeout"] = "60s"
            dialer_metadata["timeout"] = "20s"
            dialer_metadata["idleTimeout"] = "0s"
            dialer_metadata["nodelay"] = True
            
            if gost_type == "kcp":
                dialer_metadata["nodelay"] = True
                dialer_metadata["interval"] = "20ms"
                dialer_metadata["resend"] = 2
                dialer_metadata["nc"] = 1
            
            mux_type = spec.get("mux_type") or "yamux"
            if (spec.get("gaming_mode") or spec.get("multiplex")) and gost_type not in ["mws", "mwss"]:
                dialer_metadata["mux.type"] = mux_type
                dialer_metadata["nodelay"] = True
            
            if dialer_metadata:
                dialer["metadata"] = dialer_metadata
            if (security_type in ["tls", "utls"] or gost_type in ["wss", "mwss", "tls", "quic", "grpc"]) and gost_type not in ["udp", "kcp", "ssh", "sshd"]:
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
                close_fds=(os.name == 'posix')
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
    
    async def remove(self, tunnel_id: str):
        """Remove GOST tunnel"""
        _remove_tunnel_pid(tunnel_id)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        config_file = self.config_dir / f"{tunnel_id}.json"
        
        await safe_stop_subprocess(proc, patterns=[tunnel_id])

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
                
                # Check if process is ALREADY running and healthy (non-destructive adoption)
                tunnel_status = adapter.status(tunnel_id)
                if tunnel_status.get("process_running", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                    self.active_tunnels[tunnel_id] = adapter
                    restored += 1
                    logger.info(f"Tunnel {tunnel_id} core ({tunnel_core}) is ALREADY running healthy (PID {_get_tunnel_pid(tunnel_id)}). Preserved connection without restart.")
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
        elif isinstance(raw_ports, str):
            for p in raw_ports.split(","):
                if p.strip().isdigit() and int(p.strip()) > 0:
                    ports.add(int(p.strip()))
        for k in ["proxy_port", "remote_port", "listen_port", "bind_port", "control_port", "server_port"]:
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
        
        while True:
            try:
                await asyncio.sleep(15)
                now = time.time()
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
    
    async def _remove_tunnel_unlocked(self, tunnel_id: str):
        """Internal unlocked tunnel removal helper"""
        if tunnel_id in self.active_tunnels:
            adapter = self.active_tunnels[tunnel_id]
            await adapter.remove(tunnel_id)
            del self.active_tunnels[tunnel_id]
        
        if tunnel_id in self.tunnel_configs:
            del self.tunnel_configs[tunnel_id]
            self._save_tunnels()

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
            if tunnel_id in self.active_tunnels:
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
    
    async def remove_tunnel(self, tunnel_id: str):
        """Remove tunnel"""
        async with self._get_tunnel_lock(tunnel_id):
            await self._remove_tunnel_unlocked(tunnel_id)
    
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
            
        ctrl_port = control_port or spec.get("control_port")
        
        listening_ports = []
        missing_ports = []
        
        if actual_mode == "server":
            if ctrl_port:
                if is_port_listening_locally(int(ctrl_port), proto="tcp"):
                    listening_ports.append({"port": int(ctrl_port), "type": "control_tcp"})
                else:
                    missing_ports.append({"port": int(ctrl_port), "type": "control_tcp"})
                    
            for p in checked_ports:
                try:
                    p_num = int(p) if isinstance(p, (int, str)) and str(p).isdigit() else None
                    if p_num:
                        if is_port_listening_locally(p_num, proto=proto):
                            listening_ports.append({"port": p_num, "type": f"service_{proto}"})
                        else:
                            missing_ports.append({"port": p_num, "type": f"service_{proto}"})
                except Exception:
                    pass
        elif actual_mode == "client" and core == "gost" and not spec.get("is_reverse", False):
            # Direct GOST tunnel: Iran client node listens locally on service ports
            for p in checked_ports:
                try:
                    p_num = int(p) if isinstance(p, (int, str)) and str(p).isdigit() else None
                    if p_num:
                        if is_port_listening_locally(p_num, proto=proto):
                            listening_ports.append({"port": p_num, "type": f"service_{proto}"})
                        else:
                            missing_ports.append({"port": p_num, "type": f"service_{proto}"})
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

