"""Core adapters for different tunnel types"""
from typing import Protocol, Dict, Any, Optional, List, Set, Iterable, Sequence, Tuple
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
import contextvars
import json
import random

logger = logging.getLogger(__name__)

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
_PENDING_CORE_WORKERS: Set[asyncio.Task] = set()


async def _run_core_worker(func, *args):
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    _PENDING_CORE_WORKERS.add(task)
    task.add_done_callback(_PENDING_CORE_WORKERS.discard)
    # Cancelling the caller cannot stop its thread; keep it tracked until the thread exits.
    return await asyncio.shield(task)


# Idle time after which a UDP client session of a GOST tunnel is released
UDP_SESSION_TTL = "120s"

# Environment tags set on every core process this agent starts
AGENT_TAG_ENV = "SMITE_NODE_AGENT"
TUNNEL_TAG_ENV = "SMITE_TUNNEL_ID"
# Several node instances may share one host (pid: host); each tags its processes
# with its own instance id (the agent API port is unique per host).
AGENT_INSTANCE_ID = os.environ.get("SMITE_AGENT_INSTANCE") or f"port-{os.environ.get('NODE_API_PORT', '8888')}"
SAFE_TUNNEL_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

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

# Tunnel on whose behalf ports are currently being freed. Set by adapter
# apply()/remove() entry points so free_ports() never kills a process that
# belongs to a *different* live tunnel on this node.
_PORT_OWNER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("smite_port_owner", default=None)

# Callable returning the set of tunnel ids that are currently configured on this
# node. Registered by AdapterManager; processes of these tunnels are protected.
_protected_tunnels_provider: Optional[Any] = None

# Only processes started by this agent (their config lives here) may be killed
# when freeing ports. Processes of the panel or other software are never touched.
NODE_MANAGED_PATH_MARKERS = ("/etc/smite-node/",)


def _set_port_owner(tunnel_id: Optional[str]) -> None:
    _PORT_OWNER.set(tunnel_id)


def _get_protected_tunnel_ids() -> Set[str]:
    provider = _protected_tunnels_provider
    if provider is None:
        return set()
    try:
        return {str(t) for t in provider() if t}
    except Exception:
        return set()


def _find_pids_by_port_procfs(port: int, listen_only: bool = True) -> Set[int]:
    """Find process IDs holding a port by scanning Linux /proc net entries and fds directly.

    With listen_only=True (default) only TCP sockets in LISTEN state and unconnected
    UDP sockets match. Established connections whose *ephemeral* local port happens
    to equal the target port (e.g. another tunnel's outbound pool connection) are
    ignored, so freeing a port can never kill an unrelated tunnel.
    """
    inodes = _socket_inodes_for_port(port, listen_only)
    if not inodes:
        return set()
    return _pids_owning_inodes(inodes)


PROC_NET_ROOT = "/proc/net"


def _socket_inodes_for_port(port: int, listen_only: bool = True) -> Set[str]:
    """Socket inodes bound to `port` (see _find_pids_by_port_procfs for listen_only)."""
    hex_p = f"{port:04X}"
    inodes: Set[str] = set()
    for proto in ["tcp", "tcp6", "udp", "udp6"]:
        path = f"{PROC_NET_ROOT}/{proto}"
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 10:
                        local_addr = parts[1]
                        if ":" in local_addr and local_addr.rsplit(":", 1)[1].upper() == hex_p:
                            if listen_only:
                                if proto.startswith("tcp"):
                                    if parts[3].upper() != "0A":  # TCP_LISTEN
                                        continue
                                else:
                                    rem_port = parts[2].rsplit(":", 1)[-1] if ":" in parts[2] else ""
                                    if rem_port and rem_port != "0000":  # connected UDP socket
                                        continue
                            inode = parts[9]
                            if inode != "0":
                                inodes.add(inode)
        except Exception:
            pass
    return inodes


def _pids_owning_inodes(inodes: Set[str]) -> Set[int]:
    """PIDs that hold any of the given socket inodes (Linux /proc/<pid>/fd scan)."""
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
                                if target.startswith("socket:[") and target.endswith("]"):
                                    s_inode = target[8:-1]
                                    if s_inode in inodes:
                                        pids.add(pid)
                                        break
                                elif any(f"[{inode}]" in target for inode in inodes):
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

PROTECTED_PROCESS_NAMES = {
    "python", "python3", "docker", "dockerd", "containerd", "containerd-shim",
    "systemd", "sshd", "ssh", "bash", "sh", "zsh", "dash", "nginx", "uvicorn",
    "init", "kthreadd", "systemd-journald", "systemd-udevd", "systemd-networkd",
    "systemd-resolved", "dbus-daemon", "cron", "rsyslogd"
}


def _is_safe_core_process(pid: int) -> bool:
    """Check if a process belongs strictly to a known proxy core binary before terminating."""
    try:
        p = psutil.Process(pid)
        raw_name = p.name().lower().replace(".exe", "")
        if raw_name in PROTECTED_PROCESS_NAMES:
            return False
        if raw_name in ALLOWED_CORE_BINARIES:
            return True

        cmdline = p.cmdline()
        if not cmdline:
            return False

        # Extract executable name from cmdline[0]
        exe_base = os.path.basename(cmdline[0].split()[0]).lower().replace(".exe", "")
        if exe_base in PROTECTED_PROCESS_NAMES:
            return False
        if exe_base in ALLOWED_CORE_BINARIES:
            return True

        # Check exe path if available
        try:
            exe_path = os.path.basename(p.exe().lower()).replace(".exe", "")
            if exe_path in PROTECTED_PROCESS_NAMES:
                return False
            if exe_path in ALLOWED_CORE_BINARIES:
                return True
        except Exception:
            pass
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
    """Save running tunnel process PID (and its start time) to disk for zero-downtime adoption.

    The start time lets later checks detect PID reuse: on long-running hosts a dead
    tunnel's PID can be recycled by an unrelated process.
    """
    try:
        pdir = _get_pid_dir()
        content = str(pid)
        try:
            content = f"{pid} {psutil.Process(pid).create_time():.3f}"
        except Exception:
            pass
        (pdir / f"{tunnel_id}.pid").write_text(content)
    except Exception as e:
        logger.debug(f"Failed to save PID for tunnel {tunnel_id}: {e}")


def _read_tunnel_pid_record(tunnel_id: str) -> Tuple[Optional[int], Optional[float]]:
    """Return (pid, create_time) from the PID file; create_time is None for legacy files."""
    try:
        pdir = _get_pid_dir()
        pid_file = pdir / f"{tunnel_id}.pid"
        if pid_file.exists():
            parts = pid_file.read_text().strip().split()
            if parts and parts[0].isdigit():
                ctime = None
                if len(parts) > 1:
                    try:
                        ctime = float(parts[1])
                    except ValueError:
                        ctime = None
                return int(parts[0]), ctime
    except Exception:
        pass
    return None, None


def _all_recorded_pids() -> Dict[str, int]:
    """Map of tunnel-id (pid file stem) -> recorded PID for every PID file on disk."""
    result: Dict[str, int] = {}
    try:
        for pf in _get_pid_dir().glob("*.pid"):
            pid, _ = _read_tunnel_pid_record(pf.stem)
            if pid:
                result[pf.stem] = pid
    except Exception:
        pass
    return result


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
    pid, _ = _read_tunnel_pid_record(tunnel_id)
    return pid


def _is_tunnel_pid_alive(tunnel_id: str, core_name: Optional[str] = None) -> bool:
    """Check if the detached tunnel process is still active and healthy on the host."""
    pid, recorded_ctime = _read_tunnel_pid_record(tunnel_id)
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
        if recorded_ctime is not None:
            # Exact identity check: the PID must still belong to the process we started
            return abs(p.create_time() - recorded_ctime) < 1.0
        cmdline = " ".join(p.cmdline()).lower()
        if tunnel_id.lower() in cmdline:
            return True
        # Legacy PID file without start time: chisel's command line does not carry
        # the tunnel id, so fall back to the binary name for chisel only. For the
        # other cores the config path always contains the tunnel id, so a missing id
        # means the PID was recycled by another process.
        if core_name and core_name.lower() == "chisel" and "chisel" in p.name().lower():
            return True
        return False
    except Exception:
        return False


def _get_verified_tunnel_pid(tunnel_id: str, core_name: Optional[str] = None) -> Optional[int]:
    """Recorded PID of a tunnel, but only if it provably still belongs to that tunnel.

    Prevents killing an unrelated process that recycled the PID of a dead tunnel.
    """
    pid = _get_tunnel_pid(tunnel_id)
    if not pid:
        return None
    if not _is_tunnel_pid_alive(tunnel_id, core_name):
        return None
    for stem, other_pid in _all_recorded_pids().items():
        if other_pid == pid and stem != tunnel_id:
            return None
    return pid


def _cmdline_of(pid: int) -> str:
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except Exception:
        return ""


def _process_tags(pid: int) -> Tuple[bool, Optional[str]]:
    """(started_by_agent, tunnel_id) read from the environment tag of a core process."""
    try:
        env = psutil.Process(pid).environ()
    except Exception:
        return False, None
    return env.get(AGENT_TAG_ENV) == AGENT_INSTANCE_ID, (env.get(TUNNEL_TAG_ENV) or None)


def _is_node_managed_process(pid: int, recorded_pids: Optional[Dict[str, int]] = None) -> bool:
    """True if the process was started by this node agent (tagged, recorded PID, or config under /etc/smite-node)."""
    if recorded_pids is None:
        recorded_pids = _all_recorded_pids()
    if pid in recorded_pids.values():
        return True
    tagged, _ = _process_tags(pid)
    if tagged:
        return True
    cmdline = _cmdline_of(pid)
    return any(marker in cmdline for marker in NODE_MANAGED_PATH_MARKERS)


def _owning_tunnels(pid: int, candidate_ids: Iterable[str], recorded_pids: Dict[str, int]) -> Set[str]:
    """Return the tunnel ids (from candidate_ids) that the process belongs to."""
    owners: Set[str] = set()
    cmdline = _cmdline_of(pid)
    _, tag = _process_tags(pid)
    for tid in candidate_ids:
        if not tid:
            continue
        if tag and tag == tid:
            owners.add(tid)
            continue
        if tid in cmdline:
            owners.add(tid)
            continue
        if recorded_pids.get(tid) == pid or recorded_pids.get(f"{tid}_provider") == pid:
            owners.add(tid)
    return owners


def _find_tagged_core_processes(tunnel_id: Optional[str] = None) -> Dict[int, Optional[str]]:
    """Core processes started by this agent: {pid: tunnel_id}. Filter by tunnel_id when given."""
    found: Dict[int, Optional[str]] = {}
    me = {os.getpid(), os.getppid(), 1}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            if p.pid in me:
                continue
            name = (p.info.get("name") or "").lower().replace(".exe", "")
            if name not in ALLOWED_CORE_BINARIES:
                continue
            tagged, tag = _process_tags(p.pid)
            if not tagged:
                continue
            if tunnel_id is not None and tag != tunnel_id:
                continue
            found[p.pid] = tag
    except Exception:
        pass
    return found


def _kill_pids(pids: Iterable[int], timeout: float = 3.0) -> None:
    """SIGTERM, wait, then SIGKILL the given core processes (best effort)."""
    procs = []
    for pid in pids:
        try:
            if not _is_safe_core_process(pid):
                continue
            p = psutil.Process(pid)
            p.terminate()
            procs.append(p)
        except Exception:
            pass
    if not procs:
        return
    try:
        _, alive = psutil.wait_procs(procs, timeout=timeout)
    except Exception:
        alive = procs
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    try:
        psutil.wait_procs(alive, timeout=1.0)
    except Exception:
        pass


def _may_kill_port_holder(pid: int, owner: Optional[str], protected_ids: Set[str], recorded_pids: Dict[str, int]) -> bool:
    """Decide whether a process holding a port may be killed to free it.

    Rules (all must hold):
    - it is a known proxy core binary (never python/sshd/docker/etc.)
    - it was started by this node agent
    - it does not belong to another tunnel that is configured on this node
    """
    if not _is_safe_core_process(pid):
        return False
    if not _is_node_managed_process(pid, recorded_pids):
        return False
    others = protected_ids - ({owner} if owner else set())
    if others and _owning_tunnels(pid, others, recorded_pids):
        return False
    return True



def _extract_ports_from_spec_dict(spec: Any) -> Set[int]:
    """Helper to extract all listening and service ports from a tunnel spec dictionary."""
    extracted: Set[int] = set()
    if isinstance(spec, str):
        try:
            import json
            spec = json.loads(spec)
        except Exception:
            return extracted
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
    owner = _PORT_OWNER.get()
    protected_ids = _get_protected_tunnel_ids()
    recorded_pids = _all_recorded_pids()
    killed: Set[int] = set()

    def _try_kill(pid: int, port_num: int, source: str) -> None:
        if pid in ignored_pids or pid in killed:
            return
        if not _may_kill_port_holder(pid, owner, protected_ids, recorded_pids):
            logger.debug(f"{source}: port {port_num} is held by protected/foreign process {pid}; leaving it untouched")
            return
        logger.warning(f"{source}: terminating stale core process {pid} holding port {port_num} (owner={owner})")
        try:
            os.kill(pid, _SIGKILL)
            killed.add(pid)
        except Exception:
            pass

    # Method 1: Linux /proc/net + /proc/{pid}/fd scan (listening sockets only)
    for port_num in target_ports:
        try:
            for pid in _find_pids_by_port_procfs(port_num, listen_only=True):
                _try_kill(pid, port_num, "Kernel socket scan")
        except Exception as e:
            logger.debug(f"Error in procfs port scan for port {port_num}: {e}")

    # Method 2: psutil scan (non-Linux fallback). Only listening TCP / unconnected UDP sockets count.
    if not os.path.exists("/proc/net/tcp"):
        try:
            for p in psutil.process_iter(['pid', 'name']):
                if p.pid in ignored_pids or p.pid in killed:
                    continue
                try:
                    if not _is_safe_core_process(p.pid):
                        continue
                    conn_fn = getattr(p, "net_connections", None) or getattr(p, "connections")
                    for conn in conn_fn(kind='inet'):
                        if not conn.laddr or conn.laddr.port not in target_ports:
                            continue
                        if conn.type == socket.SOCK_STREAM and conn.status != psutil.CONN_LISTEN:
                            continue
                        if conn.type == socket.SOCK_DGRAM and conn.raddr:
                            continue
                        _try_kill(p.pid, conn.laddr.port, "psutil")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Error freeing ports {target_ports} via psutil: {e}")

    # NOTE: established connections are intentionally never torn down here (no `ss -K`).
    # Killing a process already closes its sockets; destroying sockets by port number
    # also hit unrelated tunnels, user sessions and even SSH sharing that port number.
    if killed:
        await asyncio.sleep(0.1)


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
                    clean_pat = str(pat).strip() if pat else ""
                    if len(clean_pat) >= 3 and clean_pat in cmdline_str:
                        logger.info(f"Terminating orphan core process {p.pid} matching '{clean_pat}'")
                        p.kill()
                        try:
                            p.wait(timeout=0.5)
                        except Exception:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            except Exception:
                pass

def _open_core_log(path: Any):
    """Truncate a core log file and reopen it in append mode for the child process.

    O_APPEND is required so the watchdog can truncate oversized logs in place: with a
    plain 'w' descriptor the child keeps writing at its old offset and the file never
    actually shrinks (sparse file that keeps "growing").
    """
    p = Path(path)
    with open(p, "w", encoding="utf-8"):
        pass
    return open(p, "a", buffering=1, encoding="utf-8")


async def _spawn_core_subprocess(
    cmd: List[str],
    stdout: Any = None,
    stderr: Any = None,
    env: Optional[Dict[str, str]] = None,
) -> asyncio.subprocess.Process:
    """Spawns a core process with a dedicated process group so signals never hit uvicorn.

    Every core process is tagged with SMITE_TUNNEL_ID / SMITE_NODE_AGENT in its
    environment (readable from /proc/<pid>/environ). The tag survives agent
    reloads and lets removal find orphans whose command line does not carry the
    tunnel id (e.g. chisel clients), even if their PID file was lost.
    """
    if stdout is None:
        stdout = subprocess.DEVNULL
    if stderr is None:
        stderr = subprocess.DEVNULL
    kwargs = {"stdout": stdout, "stderr": stderr}
    child_env = dict(env if env is not None else os.environ)
    owner = _PORT_OWNER.get()
    child_env[AGENT_TAG_ENV] = AGENT_INSTANCE_ID
    if owner:
        child_env[TUNNEL_TAG_ENV] = owner
    kwargs["env"] = child_env
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
                            if local_addr.upper().endswith(f":{port_hex}"):
                                if "tcp" in pfile:
                                    if state.upper() == "0A":  # TCP_LISTEN
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

    @staticmethod
    def _resolve_transport(spec: Dict[str, Any]) -> Tuple[str, bool, bool, bool]:
        """Return (transport, use_websocket, use_noise, websocket_tls)."""
        transport = (spec.get('transport_type') or spec.get('transport') or 'tcp').lower()
        if transport in ('tls', 'https'):
            transport = 'wss'
        if transport in ['ws', 'websocket', 'wss']:
            use_websocket = True
            use_noise = False
        elif transport == 'noise':
            use_websocket = False
            use_noise = True
        elif transport in ('tcp', 'raw', ''):
            use_websocket = False
            use_noise = False
        else:
            # Silent TCP fallback would serve an unencrypted tunnel while the
            # caller believes another transport is active.
            raise ValueError(
                f"Rathole transport '{transport}' is not supported; allowed: tcp, noise, ws, wss"
            )
        websocket_tls = (transport == 'wss') or bool(spec.get('websocket_tls', False) or spec.get('tls', False))
        return transport, use_websocket, use_noise, websocket_tls

    @staticmethod
    def _effective_ports(spec: Dict[str, Any]) -> List[Any]:
        ports = spec.get('ports') or []
        if not ports:
            proxy_port = spec.get('proxy_port') or spec.get('remote_port') or spec.get('listen_port')
            if proxy_port:
                ports = [proxy_port]
        if not ports:
            local_addr = spec.get('local_addr', '127.0.0.1:8080')
            _, local_port, _ = parse_address_port(local_addr)
            ports = [local_port or 8080]
        return list(ports)

    @staticmethod
    def _validate_ports(ports: Iterable[Any]) -> List[int]:
        cleaned: List[int] = []
        for port in ports:
            text = str(port).strip()
            if not text.isdigit():
                raise ValueError(f"Rathole service port '{port}' is not a valid port number")
            number = int(text)
            if not 1 <= number <= 65535:
                raise ValueError(f"Rathole service port {number} is outside 1-65535")
            cleaned.append(number)
        return cleaned

    async def preflight(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Validate a Rathole spec before the running tunnel is stopped."""
        mode = spec.get('mode', 'client')
        transport, use_websocket, use_noise, websocket_tls = self._resolve_transport(spec)

        token = sanitize_config_str((spec.get('token') or '').strip())
        if not token:
            raise ValueError(f"Rathole {mode} requires 'token' in spec")

        if mode == 'server':
            bind_addr = spec.get('bind_addr', '0.0.0.0:23333')
            _, bind_port, _ = parse_address_port(bind_addr)
            if bind_port is not None and not 1 <= bind_port <= 65535:
                raise ValueError(f"Rathole bind port {bind_port} is outside 1-65535")
            ports = self._effective_ports(spec)
            self._validate_ports(ports)
            if websocket_tls and not use_noise:
                pfx_path = self.config_dir / f"{tunnel_id}.pfx"
                if not spec.get('tls_pkcs12_b64') and not pfx_path.exists():
                    raise ValueError(
                        f"Rathole server in WSS mode requires 'tls_pkcs12_b64' or {pfx_path}"
                    )
            return

        remote_addr = (spec.get('remote_addr') or '').strip()
        for prefix in ('wss://', 'ws://'):
            if remote_addr.startswith(prefix):
                remote_addr = remote_addr[len(prefix):]
                break
        host, port, _ = parse_address_port(remote_addr)
        if not host or not port:
            raise ValueError("Rathole client requires 'remote_addr' (host:port) in spec")
        self._validate_ports(self._effective_ports(spec))

    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Rathole tunnel - supports both server and client modes"""
        # Validate the replacement config BEFORE stopping a healthy tunnel.
        await self.preflight(tunnel_id, spec)

        # Always remove any previous or orphan instance for this tunnel before applying
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)

        mode = spec.get('mode', 'client')

        transport, use_websocket, use_noise, websocket_tls = self._resolve_transport(spec)
        tunnel_type = (spec.get('tunnel_type') or spec.get('type') or 'tcp').lower()
        
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
            
            bind_addr_str = format_address_port(bind_host, bind_port)
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

                    remote_host, _, _ = parse_address_port(remote_addr)
                    sni = sanitize_config_str(
                        spec.get('custom_sni')
                        or spec.get('stealth_domain')
                        or spec.get('hostname')
                        or remote_host
                        or remote_addr
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
                local_addr = format_address_port(str(target_host), port_num)
                
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
        log_fh = _open_core_log(log_path)
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
        _set_port_owner(tunnel_id)
        pid = _get_verified_tunnel_pid(tunnel_id, self.name)
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


# Backhaul v0.7.2 parses `ports` itself (internal/server/transport/*.go):
#  - "443", "443-600", "4000=5000", "443=1.1.1.1:5201", "443-600=1.1.1.1:5201"
#  - "127.0.0.2:443=1.1.1.1:5201" (single local port only; local ranges need a bare port)
# and its client resolves targets with strings.Split(addr, ":")[1], so an IPv6
# target host can never be dialed. Unsupported spellings must be rejected before
# the running tunnel is stopped instead of being silently rewritten.
BACKHAUL_DEFAULT_TARGET_HOST = "127.0.0.1"
BACKHAUL_MAX_RANGE_PORTS = 500


def _backhaul_port_number(value: Any, field: str = "port") -> int:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"Backhaul {field} '{text}' is not a valid port number")
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValueError(f"Backhaul {field} {port} is outside 1-65535")
    return port


def _backhaul_parse_range(text: str) -> Optional[Tuple[int, int]]:
    match = re.fullmatch(r"\s*(\d{1,5})\s*-\s*(\d{1,5})\s*", str(text))
    if not match:
        return None
    start = _backhaul_port_number(match.group(1), "range start")
    end = _backhaul_port_number(match.group(2), "range end")
    if end < start:
        raise ValueError(f"Backhaul port range '{text}' is reversed")
    return start, end


def _backhaul_validate_target_host(host: str) -> str:
    clean = str(host or "").strip()
    if not clean:
        raise ValueError("Backhaul target host is empty")
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]
    try:
        import ipaddress
        ipaddress.IPv6Address(clean)
    except (ValueError, ipaddress.AddressValueError):
        pass
    else:
        raise ValueError(
            "Backhaul v0.7.2 cannot dial IPv6 target hosts; use an IPv4/hostname "
            "target or another tunnel core"
        )
    if ":" in clean:
        raise ValueError(f"Backhaul target host '{host}' must not include a port")
    return clean


def _backhaul_normalize_edge_ip(value: Any) -> str:
    """`edge_ip` is concatenated as `edge_ip:port` by upstream, so IPv6 must be bracketed."""
    import ipaddress

    text = str(value or "").strip()
    if not text:
        raise ValueError("Backhaul edge_ip is empty")
    candidate = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    try:
        ipaddress.IPv6Address(candidate)
    except (ValueError, ipaddress.AddressValueError):
        if ":" in candidate:
            raise ValueError(
                f"Backhaul edge_ip '{text}' must be a host without a port"
            ) from None
        return candidate
    return f"[{candidate}]"


def _backhaul_split_host_port(text: str, field: str, *, allow_ipv6: bool) -> Tuple[str, int]:
    clean = str(text or "").strip()
    host, port, is_ipv6 = parse_address_port(clean)
    bracketed = clean.startswith("[")
    if not host or port is None:
        raise ValueError(f"Backhaul {field} '{clean}' must be host:port")
    if is_ipv6 and not bracketed:
        raise ValueError(
            f"Backhaul {field} '{clean}' must bracket an IPv6 address, e.g. [::1]:443"
        )
    if is_ipv6 and not allow_ipv6:
        raise ValueError(
            f"Backhaul v0.7.2 cannot forward to IPv6 target '{clean}'; "
            "use an IPv4/hostname target or another tunnel core"
        )
    return (host, _backhaul_port_number(port, field))


def _backhaul_expand_range(start: int, end: int, target_host: str) -> List[str]:
    """Expand a 1:1 range for targets Backhaul cannot express as a range."""
    count = end - start + 1
    if count > BACKHAUL_MAX_RANGE_PORTS:
        raise ValueError(
            f"Backhaul range {start}-{end} has {count} ports and cannot be expanded "
            f"to target host {target_host} (limit {BACKHAUL_MAX_RANGE_PORTS}); "
            "use the default 127.0.0.1 target or split the range"
        )
    host = _backhaul_validate_target_host(target_host)
    return [f"{port}={format_address_port(host, port)}" for port in range(start, end + 1)]


def _normalize_backhaul_ports(ports: Any, target_host: Optional[str]) -> List[str]:
    """Translate a Smite ports list into the exact syntax Backhaul v0.7.2 parses.

    Range-to-range mappings (the old "A-B=host:A-B" shorthand) are expanded
    into supported 1:1 mappings so the target host is never dropped; other
    target ranges and IPv6 targets are rejected explicitly.
    """
    if isinstance(ports, str):
        entries: List[Any] = [entries for entries in re.split(r"[\r\n,]+", ports) if entries.strip()]
    elif isinstance(ports, (list, tuple, set)):
        entries = list(ports)
    else:
        raise ValueError("Backhaul 'ports' must be a list or comma-separated string")

    target = str(target_host or BACKHAUL_DEFAULT_TARGET_HOST).strip() or BACKHAUL_DEFAULT_TARGET_HOST
    if target != BACKHAUL_DEFAULT_TARGET_HOST:
        _backhaul_validate_target_host(target)

    normalized: List[str] = []
    for entry in entries:
        if entry is None or entry == "":
            continue
        if isinstance(entry, dict):
            local = entry.get("local") or entry.get("listen_port") or entry.get("public_port")
            remote_host = entry.get("target_host") or target
            remote_port = entry.get("target_port") or entry.get("remote_port") or local
            if not local:
                raise ValueError("Backhaul port mapping is missing its local port")
            local_range = _backhaul_parse_range(str(local))
            remote_host = _backhaul_validate_target_host(remote_host)
            if local_range:
                start, end = local_range
                remote_port_num = _backhaul_port_number(remote_port, "target port")
                normalized.append(
                    f"{start}-{end}={format_address_port(remote_host, remote_port_num)}"
                )
            else:
                local_port = _backhaul_port_number(local, "local port")
                remote_port_num = _backhaul_port_number(remote_port, "target port")
                normalized.append(
                    f"{local_port}={format_address_port(remote_host, remote_port_num)}"
                )
            continue

        if isinstance(entry, (int, float)):
            entry = str(int(entry))
        if not isinstance(entry, str):
            raise ValueError(f"Unsupported Backhaul port entry: {entry!r}")

        text = entry.strip()
        if not text:
            continue

        if "=" in text:
            parts = [part.strip() for part in text.split("=")]
            if len(parts) != 2:
                raise ValueError(
                    f"Backhaul port mapping '{text}' is invalid: exactly one '=' is allowed"
                )
            left, right = parts
            left_range = _backhaul_parse_range(left)
            if left_range:
                start, end = left_range
                right_range = _backhaul_parse_range(right)
                if right_range:
                    if right_range != left_range:
                        raise ValueError(
                            f"Backhaul port mapping '{text}' is unsupported: a local range "
                            "can only target a single port"
                        )
                    # Legacy 1:1 range spelling: preserve the host by expanding it.
                    normalized.extend(_backhaul_expand_range(start, end, target))
                    continue
                if ":" in right:
                    host_part, _, port_part = right.rpartition(":")
                    host_range = _backhaul_parse_range(port_part)
                    if host_range:
                        if host_range != left_range:
                            raise ValueError(
                                f"Backhaul port mapping '{text}' is unsupported: the target "
                                f"range {port_part} must match the local range {start}-{end}"
                            )
                        # Legacy 1:1 spelling with an explicit host: keep the host.
                        normalized.extend(
                            _backhaul_expand_range(start, end, host_part.strip())
                        )
                        continue
                    remote_host, remote_port = _backhaul_split_host_port(
                        right, "target", allow_ipv6=False
                    )
                    right_repr = format_address_port(remote_host, remote_port)
                else:
                    right_repr = str(_backhaul_port_number(right, "target port"))
                normalized.append(f"{start}-{end}={right_repr}")
                continue

            if ":" in left:
                local_host, local_port = _backhaul_split_host_port(
                    left, "local bind", allow_ipv6=True
                )
                left_repr = format_address_port(local_host, local_port)
            else:
                left_repr = str(_backhaul_port_number(left, "local port"))

            if ":" in right:
                host_part, _, port_part = right.rpartition(":")
                if _backhaul_parse_range(port_part):
                    raise ValueError(
                        f"Backhaul port mapping '{text}' is unsupported: a single local "
                        "port cannot target a port range"
                    )
                remote_host, remote_port = _backhaul_split_host_port(
                    right, "target", allow_ipv6=False
                )
                right_repr = format_address_port(remote_host, remote_port)
            else:
                right_repr = str(_backhaul_port_number(right, "target port"))
            normalized.append(f"{left_repr}={right_repr}")
            continue

        port_range = _backhaul_parse_range(text)
        if port_range:
            start, end = port_range
            if target == BACKHAUL_DEFAULT_TARGET_HOST:
                normalized.append(f"{start}-{end}")
            else:
                normalized.extend(_backhaul_expand_range(start, end, target))
            continue

        if ":" in text:
            raise ValueError(
                f"Backhaul port mapping '{text}' is unsupported: range-to-port must use "
                "'A-B=host:PORT' (the 'A-B:PORT' spelling is not parsed by v0.7.2)"
            )
        port = _backhaul_port_number(text, "port")
        if target == BACKHAUL_DEFAULT_TARGET_HOST:
            normalized.append(f"{port}={target}:{port}")
        else:
            normalized.append(f"{port}={format_address_port(target, port)}")
    return normalized


def _backhaul_local_ports(entries: Iterable[str]) -> Set[int]:
    """Local (listen) ports of already-normalized Backhaul mappings."""
    ports: Set[int] = set()
    for entry in entries:
        left = str(entry).split("=", 1)[0].strip()
        port_range = _backhaul_parse_range(left)
        if port_range:
            ports.update(range(port_range[0], port_range[1] + 1))
            continue
        if left.isdigit():
            ports.add(_backhaul_port_number(left, "local port"))
            continue
        _, port, _ = parse_address_port(left)
        if port:
            ports.add(port)
    return ports


def _backhaul_effective_transport(spec: Dict[str, Any], default: str) -> Tuple[str, bool]:
    """Resolve the carrier exactly like the adapter does, rejecting unknown values."""
    raw_transport = str(spec.get("transport") or spec.get("type") or default).strip().lower()
    raw_transport = {
        "websocket": "ws", "https": "wss", "tls": "wss", "mux": "tcpmux",
    }.get(raw_transport, raw_transport)
    if raw_transport == "tcp+udp":
        raw_transport = "tcp"
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
        raise ValueError(
            f"Backhaul transport '{raw_transport}' is not supported; "
            "allowed: tcp, tcpmux, ws, wsmux, wss, wssmux"
        )

    explicit_udp = (
        spec.get("type") in ("udp", "tcp+udp")
        or spec.get("tunnel_type") in ("udp", "tcp+udp")
        or is_pure_udp
    )
    # Upstream implements accept_udp only for transport="tcp"; a ws/wss carrier
    # requested for a UDP payload must not be silently replaced with TCP unless
    # the payload really is UDP.
    if is_udp_over_tcp and transport != "tcp":
        if transport in {"ws", "wss", "wsmux", "wssmux"} and not explicit_udp:
            is_udp_over_tcp = False
        else:
            transport = "tcp"
    return transport, is_udp_over_tcp


def _backhaul_effective_server_ports(spec: Dict[str, Any]) -> Any:
    """Ports list including the legacy single-port fallbacks, before normalization."""
    ports = spec.get("ports")
    if ports and not (isinstance(ports, list) and len(ports) == 0):
        return ports

    listen_port = spec.get("public_port") or spec.get("listen_port")
    target_addr = spec.get("target_addr")
    target_host = str(spec.get("target_host") or BACKHAUL_DEFAULT_TARGET_HOST).strip() or BACKHAUL_DEFAULT_TARGET_HOST
    if not target_addr:
        target_port = spec.get("target_port") or listen_port
        if target_port:
            target_addr = format_address_port(target_host, target_port)
    if listen_port and target_addr:
        return [f"{listen_port}={target_addr}"]
    if listen_port:
        return [str(listen_port)]
    return []


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
    ]

    BACKHAUL_NUMERIC_KEYS = {
        "keepalive_period", "heartbeat", "channel_size", "mux_con", "web_port",
        "mss", "so_rcvbuf", "so_sndbuf", "mux_version", "mux_framesize",
        "mux_recievebuffer", "mux_streambuffer", "connection_pool", "retry_interval",
        "dial_timeout", "mux_session"
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

    async def preflight(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Validate a Backhaul spec before the running tunnel is stopped."""
        self._resolve_binary_path()
        mode = spec.get("mode", "client")
        if mode == "server":
            _backhaul_effective_transport(spec, "tcpmux")
            bind_addr = spec.get("bind_addr")
            if bind_addr:
                _backhaul_split_host_port(str(bind_addr), "bind_addr", allow_ipv6=True)
            else:
                control_port = spec.get("control_port") or spec.get("listen_port") or 3080
                _backhaul_port_number(control_port, "control_port")
            ports = _backhaul_effective_server_ports(spec)
            logger.info(
                f"Backhaul {mode} tunnel {tunnel_id}: received ports from spec: {ports} (type: {type(ports)})"
            )
            _normalize_backhaul_ports(ports, spec.get("target_host"))
            if bool(spec.get("tls_cert_pem")) != bool(spec.get("tls_key_pem")):
                raise ValueError(
                    "Backhaul server requires both 'tls_cert_pem' and 'tls_key_pem', or neither"
                )
            return

        _backhaul_effective_transport(spec, "tcp")
        remote_addr = spec.get("remote_addr") or spec.get("control_addr") or spec.get("bind_addr")
        if not remote_addr:
            raise ValueError("Backhaul client requires 'remote_addr' in spec")
        remote_addr = str(remote_addr).strip()
        if remote_addr.startswith("ws://"):
            remote_addr = remote_addr[5:]
        elif remote_addr.startswith("wss://"):
            remote_addr = remote_addr[6:]
        elif "://" in remote_addr:
            remote_addr = remote_addr.split("://", 1)[1]
        if "/" in remote_addr:
            remote_addr = remote_addr.split("/", 1)[0]
        _backhaul_split_host_port(remote_addr, "remote_addr", allow_ipv6=True)
        client_options = dict(spec.get("client_options") or {})
        edge_ip = client_options.get("edge_ip") or spec.get("edge_ip")
        if edge_ip not in (None, ""):
            _backhaul_normalize_edge_ip(edge_ip)

    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        # Validate the replacement config BEFORE stopping a healthy tunnel.
        await self.preflight(tunnel_id, spec)

        # Always remove any previous or orphan instance for this tunnel before applying
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)

        mode = spec.get('mode', 'client')

        if mode == 'server':
            transport, is_udp_over_tcp = _backhaul_effective_transport(spec, "tcpmux")

            server_options = dict(spec.get("server_options") or {})
            bind_addr = spec.get("bind_addr")
            if not bind_addr:
                control_port = _backhaul_port_number(
                    spec.get("control_port") or spec.get("listen_port") or 3080,
                    "control_port",
                )
                bind_ip = str(spec.get("bind_ip", "0.0.0.0")).strip() or "0.0.0.0"
                bind_addr = format_address_port(bind_ip, control_port)
            else:
                bind_host, bind_port = _backhaul_split_host_port(
                    str(bind_addr), "bind_addr", allow_ipv6=True
                )
                bind_addr = format_address_port(bind_host, bind_port)

            ports = _backhaul_effective_server_ports(spec)
            logger.info(f"Backhaul {mode} tunnel {tunnel_id}: received ports from spec: {ports} (type: {type(ports)})")
            ports = _normalize_backhaul_ports(ports, spec.get("target_host"))

            # Batch port freeing on server node
            ports_to_free: Set[int] = set()
            _, bind_port_num, _ = parse_address_port(bind_addr)
            if bind_port_num:
                ports_to_free.add(bind_port_num)
            ports_to_free.update(_backhaul_local_ports(ports))

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
                "mux_recievebuffer", "mux_streambuffer", "mux_session", "sniffer_log"
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

            # Upstream implements accept_udp only for transport="tcp"
            if is_udp_over_tcp and transport == "tcp":
                server_config["accept_udp"] = True
            else:
                server_config.pop("accept_udp", None)

            kp = server_options.get("keepalive_period") or spec.get("keepalive_period")
            if not kp or isinstance(kp, bool) or not isinstance(kp, (int, float)) or kp <= 0:
                server_config["keepalive_period"] = 20
            else:
                server_config["keepalive_period"] = int(kp)

            hb = server_options.get("heartbeat") or spec.get("heartbeat")
            if not hb or isinstance(hb, bool) or not isinstance(hb, (int, float)) or hb <= 0:
                server_config["heartbeat"] = 20
            else:
                server_config["heartbeat"] = int(hb)

            server_config.setdefault("nodelay", True)
            if transport in ("tcpmux", "wsmux", "wssmux"):
                server_config.setdefault("mux_version", 2)
                if spec.get("gaming_mode"):
                    server_config.setdefault("mux_streambuffer", 2097152)
                else:
                    server_config.setdefault("mux_recievebuffer", 16777216)
                    server_config.setdefault("mux_streambuffer", 4194304)

            if server_config.get("sniffer") and not server_config.get("sniffer_log"):
                server_config["sniffer_log"] = str(self.config_dir / f"{tunnel_id}_sniffer.json")

            # Write TLS certificates if provided as in-memory PEM
            if spec.get("tls_cert_pem") and spec.get("tls_key_pem"):
                cert_path = self.config_dir / f"{tunnel_id}_cert.pem"
                key_path = self.config_dir / f"{tunnel_id}_key.pem"
                await _run_core_worker(cert_path.write_text, spec["tls_cert_pem"], "utf-8")
                await _run_core_worker(key_path.write_text, spec["tls_key_pem"], "utf-8")
                try:
                    os.chmod(key_path, 0o600)
                except Exception:
                    pass
                server_config["tls_cert"] = str(cert_path)
                server_config["tls_key"] = str(key_path)

            config_path = self.config_dir / f"{tunnel_id}.toml"
            rendered_cfg = self._render_toml({"server": server_config})
            await _run_core_worker(config_path.write_text, rendered_cfg, "utf-8")
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
            log_fh = _open_core_log(log_path)
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

            remote_addr = str(remote_addr).strip()
            if remote_addr.startswith('ws://'):
                remote_addr = remote_addr[5:]
            elif remote_addr.startswith('wss://'):
                remote_addr = remote_addr[6:]
            elif '://' in remote_addr:
                remote_addr = remote_addr.split('://', 1)[1]
            if '/' in remote_addr:
                remote_addr = remote_addr.split('/', 1)[0]
            remote_host, remote_port = _backhaul_split_host_port(
                remote_addr, "remote_addr", allow_ipv6=True
            )
            remote_addr = format_address_port(remote_host, remote_port)

            transport, is_udp_over_tcp = _backhaul_effective_transport(spec, "tcp")

            client_options = dict(spec.get("client_options") or {})

            # Upstream ws_dialer.go uses remote_addr as the WebSocket Host header & TLS SNI,
            # while dialing edge_ip:port on TCP when edge_ip is set.
            sni_domain = (spec.get("custom_sni") or spec.get("stealth_domain") or "").strip()
            if sni_domain and transport in ("ws", "wss", "wsmux", "wssmux"):
                if "://" in sni_domain:
                    sni_domain = sni_domain.split("://", 1)[1].split("/", 1)[0]
                sni_domain = sni_domain.split(":", 1)[0].strip()
                r_host, r_port, _ = parse_address_port(remote_addr)
                if sni_domain and r_host and r_port and sni_domain != r_host:
                    if not (client_options.get("edge_ip") or spec.get("edge_ip")):
                        client_options["edge_ip"] = r_host
                    remote_addr = format_address_port(sni_domain, r_port)

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
                elif key == "edge_ip":
                    value = _backhaul_normalize_edge_ip(value)
                config_dict[key] = value

            # accept_udp / insecure / heartbeat / channel_size are not part of
            # upstream ClientConfig (UDP is negotiated by the server; wss/wssmux
            # always skip cert verification), so they are never written here.
            config_dict.setdefault("nodelay", True)
            if transport in ("tcpmux", "wsmux", "wssmux"):
                config_dict.setdefault("mux_version", 2)
                if spec.get("gaming_mode"):
                    config_dict.setdefault("mux_streambuffer", 2097152)
                else:
                    config_dict.setdefault("mux_recievebuffer", 16777216)
                    config_dict.setdefault("mux_streambuffer", 4194304)

            if "connection_pool" not in config_dict:
                config_dict["connection_pool"] = 8
            if "retry_interval" not in config_dict:
                config_dict["retry_interval"] = 3
            if "dial_timeout" not in config_dict:
                config_dict["dial_timeout"] = 10

            kp = client_options.get("keepalive_period") or spec.get("keepalive_period")
            if not kp or isinstance(kp, bool) or not isinstance(kp, (int, float)) or kp <= 0:
                config_dict["keepalive_period"] = 20
            else:
                config_dict["keepalive_period"] = int(kp)

            if "aggressive_pool" not in config_dict:
                config_dict["aggressive_pool"] = True

            config_path = self.config_dir / f"{tunnel_id}.toml"
            rendered_cfg = self._render_toml({"client": config_dict})
            await _run_core_worker(config_path.write_text, rendered_cfg, "utf-8")
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
            log_fh = _open_core_log(log_path)
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
        _set_port_owner(tunnel_id)
        pid = _get_verified_tunnel_pid(tunnel_id, self.name)
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
                ports_block = re.search(r'^\s*ports\s*=\s*\[(.*?)\]', txt, re.DOTALL | re.MULTILINE)
                if ports_block:
                    for entry in re.findall(r'["\']([^"\']+)["\']', ports_block.group(1)):
                        local = entry.split("=")[0].strip()
                        if local.isdigit():
                            if 0 < int(local) <= 65535:
                                ports_to_free.add(int(local))
                        else:
                            m_r = re.fullmatch(r"(\d{1,5})-(\d{1,5})", local)
                            if m_r:
                                a, b = int(m_r.group(1)), int(m_r.group(2))
                                if 0 < a <= b <= 65535:
                                    ports_to_free.update(range(a, min(b, a + 499) + 1))
            except Exception:
                pass

        await safe_stop_subprocess(proc, patterns=[tunnel_id, f"{tunnel_id}.toml"], pid=pid)
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

_FALLBACK_DEFAULT_CERT = """-----BEGIN CERTIFICATE-----
MIICqzCCAZOgAwIBAgIBATANBgkqhkiG9w0BAQsFADAZMRcwFQYDVQQDDA5zbWl0
ZS1mYWxsYmFjazAeFw0yNDAxMDEwMDAwMDBaFw0zOTAxMDEwMDAwMDBaMBkxFzAV
BgNVBAMMDnNtaXRlLWZhbGxiYWNrMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIB
CgKCAQEAuqAherbo7dfP2U8jp0qcjN5glNrgbHMigpPjznUnSlGcFdrj8LpzhXrO
bbHOmpz9ZVCogcYKLRHRwTW6QZ6ifThE7PRPs/TCQTbLFQGjM7oE1KtCVUoa5OEV
bkx49N3nGQ2/v1zIYRhpGgd86x7fpkjd4h5zHuLJOHLxqSumUD7EK+N2QVe6Bz/C
K0ANha+t8PbZ12qh7iKC9uKyX+wNjLeTTdGen8jQ6ekcdyr3BDq/Sow3HNZ49Kwq
D4fuJG/lslKe8uVBgXy/reZ6fvW1xo08ZT4O1ArR5xNutO2lF5Yb3l2YsRJNdgyD
hCLWW1i9lHmzVS3TKH99WeGfDJ6MWwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQCb
nVNqkg+UKlLBm5yDjZRqBP00jkNXaazBQNsbcaV4W00rnt0CRxTAohtg6E0QLOh9
cI2hp0jLr7TTZbPWnnhA6eK0Z1tDdkcQGgCfk0LkaCxcCJaQkr4Ag9V7B7BG9aVb
RK5/RQcgk25MXd9eOvfJqveJlyShLu/6VTPrhDWVj/Zjfelm4+M6l3X5Zek0i88+
Xj5iowPVYJNA8f/3teI3xoatqOBsLVUbLR4Rw0pgK06+XMFv+fE7RelA273CLzyN
RiPhnc2ptnUmbrrQLAToivEtjymQytHJfELrnhQjPPIZqkjgxbczcHyOEL45Lf6v
e6L/L1vXM3CGehaHWbd4
-----END CERTIFICATE-----
"""

_FALLBACK_DEFAULT_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEogIBAAKCAQEAuqAherbo7dfP2U8jp0qcjN5glNrgbHMigpPjznUnSlGcFdrj
8LpzhXrObbHOmpz9ZVCogcYKLRHRwTW6QZ6ifThE7PRPs/TCQTbLFQGjM7oE1KtC
VUoa5OEVbkx49N3nGQ2/v1zIYRhpGgd86x7fpkjd4h5zHuLJOHLxqSumUD7EK+N2
QVe6Bz/CK0ANha+t8PbZ12qh7iKC9uKyX+wNjLeTTdGen8jQ6ekcdyr3BDq/Sow3
HNZ49KwqD4fuJG/lslKe8uVBgXy/reZ6fvW1xo08ZT4O1ArR5xNutO2lF5Yb3l2Y
sRJNdgyDhCLWW1i9lHmzVS3TKH99WeGfDJ6MWwIDAQABAoIBAETgj6qmMh6B3Wyy
+BIoh9G3ywZ1J4nvDkgKA/QxKcCUkRpB0ImKjtMvKVuz2XAXy6+mTqKisCPFJVh9
F1JblVXIvCuF2ZqtlToTWm1UzuJq35iL4m3u7TEW3113bNbWxRFqkR+zwyKOq0+X
fuOZ3+oTj4yCN3OpOKGUTptkiDup48R4qazJYypZO2pcJRBD9+DIteHZD7DZi64J
SWox9CnkKrA7mRt7xbz9tBvPlW4wRzUxum90IFKf0GH7rqIUbiq9gjHxc5y39YzW
PDkzyCTWDvw42w8p0+cxeyQaE49n2u+baT7wHNn4Pn9QpvejpNakwFVsbxKIG0P2
q0oNw1UCgYEA+uwLcLvY5aIg3x5qD/UBKuz14JJgEx8p0WPoL3m29+8Ysa0U3KjQ
5GrCS1TGReyB8GoeaZjurMZ4d0/ctodaVv4D/GdSUcc2tLibumF55SmkK5SsHRQI
vQvb6KvMLrlEBP0ALoeQXGNFaRg0/33jj9V2ijm8pjAiNQNz0nWX0C8CgYEAvmb7
788Yg80A5PhcTU+kjlfWrE7DYq31IGSQGGCBsrRrmeQG+WcaCJw4nOLp87yyjdf9
Jp1T2LQ3ABfhW70/jVOcLdzWEe2OubZgt4Qva946MEBgKjchy7BlNK5YJejCj8wi
kxpB7/NY/OLpnl1dK08MfDy50vrx6mCqD8pib5UCgYAoEEhDV7rp71kQRuroYVYu
ZGdd1AFDDFbA+BK6gdSVAbgPpnMOSGYSEnmzTXXa9EhK+qagdaqmK4pQgo/bFQF3
oldAlb6vD//MGy3CNI4h6OQtkS9p9SjwBlS2cbfIPgx+P2coBr0mAzOx9AuCP7d1
379tkopsfoL3s/VqRRxYQQKBgH2uhQba8RevLrP1rRfrRngfSzkaFKcCpRfoyaUN
rw9DQPOoLWJbyDd+x2/n6P5WxJYUyKnAmCQncVbCbKOd3GFxm1o6t6PF57x2wojg
3FybqFVJaTD0i35kxalsnAsKBcPdv8TSREwbmdX0ArnibymzWE551z+JHj7T5+3c
bfWxAoGAXQYe60+j83lUubdcm7ss04/Ujsv8XEt9QTIwMP3ODiolQFSCrVdI7qTm
kReW/GYclfsA/MY0gusKgju4q5iTENHfVS1CeQdNMD4T/+lr0e40Auvu870Go8it
m0xA9r2jtPLiDrdUSLhytX49XsWibgwYgio5EASppeFo8rEEUMk=
-----END RSA PRIVATE KEY-----
"""


def _ensure_self_signed_cert_and_key(cert_path: Path, key_path: Path, common_name: str = "smite-tunnel") -> bool:
    """Ensure cert and private key files exist using cryptography, openssl, or static fallback."""
    if cert_path.exists() and key_path.exists():
        return True

    # 1. Try python cryptography library
    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(common_name)]), critical=False)
            .sign(private_key, hashes.SHA256())
        )
        key_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
        key_path.write_bytes(key_bytes)
        cert_path.write_bytes(cert_bytes)
        try:
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.debug(f"Cryptography library not available for self-signed cert: {e}")

    # 2. Try system openssl CLI
    try:
        import shutil
        openssl_bin = shutil.which("openssl")
        if openssl_bin:
            res = subprocess.run([
                openssl_bin, "req", "-new", "-newkey", "rsa:2048", "-days", "3650",
                "-nodes", "-x509", "-subj", f"/CN={common_name}",
                "-keyout", str(key_path), "-out", str(cert_path)
            ], check=False, timeout=5, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            if res.returncode == 0 and cert_path.exists() and key_path.exists():
                try:
                    os.chmod(key_path, 0o600)
                    os.chmod(cert_path, 0o644)
                except Exception:
                    pass
                return True
    except Exception as e:
        logger.debug(f"OpenSSL CLI failed for self-signed cert: {e}")

    # 3. Fallback to pre-computed embedded valid certificate and private key
    try:
        key_path.write_text(_FALLBACK_DEFAULT_KEY.strip(), encoding="utf-8")
        cert_path.write_text(_FALLBACK_DEFAULT_CERT.strip(), encoding="utf-8")
        try:
            os.chmod(key_path, 0o600)
            os.chmod(cert_path, 0o644)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to write fallback certificate: {e}")
        return False


def _ensure_rsa_private_key(key_path: Path) -> bool:
    if key_path.exists():
        return True
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
        key_path.write_bytes(key_bytes)
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        pass

    try:
        import shutil
        openssl_bin = shutil.which("openssl")
        if openssl_bin:
            res = subprocess.run([
                openssl_bin, "genpkey", "-algorithm", "RSA",
                "-out", str(key_path),
                "-pkeyopt", "rsa_keygen_bits:2048"
            ], check=False, timeout=5, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            if res.returncode == 0 and key_path.exists():
                try:
                    os.chmod(key_path, 0o600)
                except Exception:
                    pass
                return True
    except Exception:
        pass

    try:
        key_path.write_text(_FALLBACK_DEFAULT_KEY.strip(), encoding="utf-8")
        try:
            os.chmod(key_path, 0o600)
        except Exception:
            pass
        return True
    except Exception:
        return False


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

    async def preflight(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Reject an unusable Chisel client spec before stopping the running tunnel."""
        mode = spec.get('mode', 'client')
        if mode != 'client':
            return
        transport = (spec.get('transport_type') or spec.get('transport') or 'ws').lower()
        use_tls = (transport in ['wss', 'https', 'tls']) or bool(spec.get('websocket_tls', False) or spec.get('tls', False))
        tunnel_proto = (spec.get('type') or spec.get('tunnel_type') or 'tcp').lower()
        server_url = spec.get('server_url') or spec.get('remote_addr') or spec.get('server_addr')
        if not server_url:
            raise ValueError("Chisel client requires 'server_url' or 'remote_addr' in spec")
        ports = spec.get('ports') or []
        local_port = spec.get('local_port') or spec.get('port')
        if not ports and not local_port and tunnel_proto != 'socks5':
            raise ValueError("Chisel client requires at least one port mapping")

        if use_tls:
            tls_ca_pem = spec.get('tls_ca_cert_pem') or spec.get('tls_ca')
            explicit_skip_verify = spec.get('tls_skip_verify')
            if explicit_skip_verify is None:
                tls_skip_verify = not bool(tls_ca_pem)
            else:
                tls_skip_verify = bool(explicit_skip_verify)
            # Fail closed BEFORE the healthy tunnel is stopped.
            if tls_skip_verify and not tls_ca_pem and not spec.get('fingerprint'):
                raise ValueError(
                    "Chisel TLS requires a CA certificate or SSH fingerprint; "
                    "refusing to connect without server authentication"
                )

    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply Chisel tunnel - supports both server and client modes with TLS, UDP, Camouflage and Persistent Keys"""
        # Validate the replacement config BEFORE stopping a healthy tunnel.
        await self.preflight(tunnel_id, spec)

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

        # Always remove any previous or orphan instance for this tunnel before applying
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)
        
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
                    _ensure_self_signed_cert_and_key(cert_path, key_path, "chisel-tunnel")
                
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

            log_f = _open_core_log(log_file)
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
                target_repr = format_address_port(str(target_host))
                if is_rev:
                    prefix = f"R:{remote_p}:{target_repr}:{local_p}"
                else:
                    prefix = f"{local_p}:{target_repr}:{remote_p}"
                
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
                tls_ca_pem = spec.get('tls_ca_cert_pem') or spec.get('tls_ca')
                explicit_skip_verify = spec.get('tls_skip_verify')
                if explicit_skip_verify is None:
                    # Fail closed: only skip verification when the operator
                    # explicitly asked for it; a supplied CA means verify.
                    tls_skip_verify = not bool(tls_ca_pem)
                else:
                    tls_skip_verify = bool(explicit_skip_verify)
                if tls_skip_verify and not tls_ca_pem and not fingerprint:
                    raise ValueError(
                        "Chisel TLS requires a CA certificate or SSH fingerprint; "
                        "refusing to connect without server authentication"
                    )
                if tls_skip_verify:
                    cmd.append("--tls-skip-verify")
                
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

            log_f = _open_core_log(log_file)
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
        _set_port_owner(tunnel_id)
        pid = _get_verified_tunnel_pid(tunnel_id, self.name)
        proc = self.processes.pop(tunnel_id, None)
        if tunnel_id in self.log_handles:
            try:
                self.log_handles[tunnel_id].close()
            except Exception:
                pass
            del self.log_handles[tunnel_id]

        # Ports are derived from the command line recorded in the log header only
        # (never from log lines: timestamps like "22:15:03" used to be parsed as port 22).
        ports_to_free: Set[int] = set()
        log_file = self.config_dir / f"{tunnel_id}.log"
        if log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as lf:
                    header = [lf.readline() for _ in range(3)]
                for line in header:
                    if not line.startswith("Command:"):
                        continue
                    for token in line.split()[1:]:
                        m = re.match(r'^(?:R:)?(?:[0-9.]+:|\[[0-9a-fA-F:]+\]:)?(\d{1,5}):', token)
                        if m:
                            p_val = int(m.group(1))
                            if 0 < p_val <= 65535:
                                ports_to_free.add(p_val)
                    m_port = re.search(r'--port\s+(\d{1,5})', line)
                    if m_port:
                        ports_to_free.add(int(m_port.group(1)))
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


def _frp_tcp_mux(spec: Dict[str, Any]) -> bool:
    """FRP stream multiplexing (default on).

    The panel only sends tcp_mux=false for latency-sensitive tunnels after both
    nodes confirmed they support it, because frps and frpc must agree. Without
    multiplexing each proxy connection gets its own TCP stream, so a game's UDP
    traffic never waits behind other traffic of the tunnel (no head-of-line
    blocking inside the shared yamux session).
    """
    value = spec.get("tcp_mux", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


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

    async def preflight(self, tunnel_id: str, spec: Dict[str, Any]) -> None:
        """Validate an FRP spec (binary, port, TLS material) before stopping the tunnel."""
        mode = spec.get('mode', 'client')
        is_provider = bool(spec.get('is_provider'))
        if bool(spec.get('tls_cert_pem')) != bool(spec.get('tls_key_pem')):
            raise ValueError(
                "FRP requires both 'tls_cert_pem' and 'tls_key_pem', or neither"
            )

        if mode == 'server':
            self._resolve_server_binary_path()
            if is_provider:
                self._resolve_binary_path()
            bind_port = spec.get('bind_port', 7000)
            try:
                bind_port = int(bind_port)
            except (ValueError, TypeError):
                raise ValueError(f"FRP bind_port '{bind_port}' is not a valid port number") from None
            if not 1 <= bind_port <= 65535:
                raise ValueError(f"FRP bind_port {bind_port} is outside 1-65535")
            return

        self._resolve_binary_path()
        server_addr = str(spec.get('server_addr') or '').strip()
        if not server_addr:
            raise ValueError("FRP client requires 'server_addr' in spec")
        server_port = spec.get('server_port', 7000)
        try:
            server_port = int(server_port)
        except (ValueError, TypeError):
            raise ValueError(f"FRP server_port '{server_port}' is not a valid port number") from None
        if not 1 <= server_port <= 65535:
            raise ValueError(f"FRP server_port {server_port} is outside 1-65535")
    
    async def apply(self, tunnel_id: str, spec: Dict[str, Any]):
        """Apply FRP tunnel - supports both server and client modes"""
        # Validate the replacement config BEFORE stopping a healthy tunnel.
        await self.preflight(tunnel_id, spec)
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
            elif transport_proto in ['wss', 'https', 'tls']:
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

            tcp_mux = _frp_tcp_mux(spec)
            config_content += f"""transport:
  maxPoolCount: 8
  heartbeatTimeout: 30
  tcpMux: {'true' if tcp_mux else 'false'}
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
            log_f = _open_core_log(log_file)
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
"""
                if not tcp_mux:
                    provider_config_content += """transport:
  tcpMux: false
"""
                provider_config_content += f"""auth:
  method: token
  token: "{clean_token or ''}"
"""
                if clean_token:
                    # frps above verifies the token on heartbeats and new work
                    # connections; without the same scopes here frps rejected every
                    # work connection of the provider, so direct-mode FRP tunnels
                    # never carried traffic.
                    provider_config_content += """  additionalScopes:
    - HeartBeats
    - NewWorkConns
"""
                provider_config_content += """proxies:
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
                provider_log_f = _open_core_log(provider_log_file)
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
            elif transport_proto in ['wss', 'https', 'tls']:
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
                # Mbps (megabits) -> FRP bytes/sec units: 1 Mbps = 125 KB/s
                bandwidth_limit = f"{max(1, int(float(rate_limit_mbps) * 125))}KB"
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
                config_content += f"""  tcpMux: {'true' if _frp_tcp_mux(spec) else 'false'}
  tcpMuxKeepaliveInterval: 25
  poolCount: 5
"""
            tls_ca_path = None
            tls_ca_pem = spec.get('tls_ca_cert_pem') or spec.get('tls_ca')
            if tls_enable and tls_ca_pem:
                ca_path = self.config_dir / f"frpc_{tunnel_id}_ca.pem"
                with open(ca_path, "w") as cf:
                    cf.write(tls_ca_pem)
                try:
                    os.chmod(ca_path, 0o600)
                except Exception:
                    pass
                tls_ca_path = str(ca_path)

            if transport_proto == 'quic':
                # QUIC always negotiates TLS from transport.tls; only serverName/trustedCaFile apply.
                if clean_sni or tls_ca_path:
                    config_content += "  tls:\n"
                    if clean_sni:
                        config_content += f'    serverName: "{clean_sni}"\n'
                    if tls_ca_path:
                        config_content += f'    trustedCaFile: "{tls_ca_path}"\n'
            elif tls_enable:
                config_content += """  tls:
    enable: true
    disableCustomTLSFirstByte: true
"""
                if clean_sni:
                    config_content += f"""    serverName: "{clean_sni}"\n"""
                if tls_ca_path:
                    config_content += f"""    trustedCaFile: "{tls_ca_path}"\n"""
            else:
                # FRP >= 0.50 enables TLS by default; opt out explicitly to keep plain transport plain.
                config_content += """  tls:
    enable: false
"""

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
            log_f = _open_core_log(log_file)
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
        _set_port_owner(tunnel_id)
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

            pid = _get_verified_tunnel_pid(sub_id, "frp")
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
        await self.remove(tunnel_id)
        await asyncio.sleep(0.2)
            
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

        # The dedicated mws/mwss listener+dialer of GOST 3.0.0-rc10 is broken: every
        # stream fails with "websocket: bad MASK" (mwss: "tls: received record with
        # version 0"), so MWS/MWSS tunnels never carried traffic. ws/wss with
        # mux=true on both ends is the same multiplexed WebSocket and works.
        ws_mux = gost_type in ("mws", "mwss")
        if ws_mux:
            gost_type = "wss" if gost_type == "mwss" else "ws"

        raw_proto = (spec.get("type") or spec.get("tunnel_type") or "tcp").lower()
        if raw_proto == "grpc":
            tunnel_proto = "tcp"
            gost_type = "grpc"
        elif raw_proto in ["tcpmux", "mux"]:
            tunnel_proto = "tcp"
            spec["multiplex"] = True
        elif raw_proto in ["tcp", "udp", "tcp+udp"]:
            tunnel_proto = raw_proto
        else:
            tunnel_proto = "tcp"

        is_udp_mode = tunnel_proto in ["udp", "tcp+udp"]
        # The pinned GOST x revisions only implement xtaci/smux; normalize the
        # legacy "yamux" label so no unsupported claim reaches the config.
        mux_type = str(spec.get("mux_type") or "smux").strip().lower()
        if mux_type in ("yamux", "smux"):
            mux_type = "smux"
        is_reverse_mode = bool(is_reverse)
        is_gaming_mode = bool(spec.get("gaming_mode"))
        # Avoid Nested Double-SMUX in reverse mode: the relay handler/dialer already
        # creates a BIND SMUX session. Do not wrap underlying transport in an extra
        # layer of mtcp/mtls multiplexing unless explicitly requested via mux or tcpmux.
        is_explicit_mux = (
            bool(spec.get("multiplex"))
            or bool(spec.get("enable_mux"))
            or raw_proto in ["tcpmux", "mux"]
            or (spec.get("transport_type") or "").lower() in ["tcpmux", "mux"]
            or (spec.get("transport") or "").lower() in ["tcpmux", "mux"]
        )
        if is_reverse_mode:
            enable_mux = is_explicit_mux and gost_type not in ["mws", "mwss", "mtcp", "mtls", "grpc"]
        else:
            enable_mux = (is_gaming_mode or is_explicit_mux or is_udp_mode) and gost_type not in ["mws", "mwss", "mtcp", "mtls", "grpc"]
        # Low-latency tuning for UDP/gaming tunnels. Everything here only changes how
        # THIS side sends or buffers, so it is wire compatible with a peer running an
        # older agent (safe during rolling updates).
        low_latency = is_gaming_mode or spec.get("latency_profile") == "gaming-v1" or is_udp_mode

        raw_interval = spec.get("keepalive_interval") or 15
        try:
            ka_sec = int(str(raw_interval).rstrip("s"))
            if ka_sec <= 0:
                ka_sec = 15
        except (ValueError, TypeError):
            ka_sec = 15

        ka_interval_str = f"{ka_sec}s"
        ka_timeout_sec = max(30, 3 * ka_sec)
        ka_timeout_str = f"{ka_timeout_sec}s"
        reverse_ttl_sec = max(30, 3 * ka_sec)
        reverse_ttl_str = f"{reverse_ttl_sec}s"

        # Standard GOST v3 multiplexing metadata (mux.version: 2, xtaci/smux)
        # Keep mux.maxFrameSize at 32768 (32KB) for normal tunnels, tcp+udp dual forward,
        # and anti-packet-loss presets. Only set 4KB frame size when competitive gaming_mode is explicitly enabled.
        mux_tuning = {
            "mux.version": 2,
            "mux.keepaliveDisabled": False,
            "mux.keepaliveInterval": ka_interval_str,
            "mux.keepaliveTimeout": ka_timeout_str,
            "mux.maxFrameSize": 4096 if is_gaming_mode else 32768,
            "mux.maxReceiveBuffer": 33554432,
            "mux.maxStreamBuffer": 8388608,
        }

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
        
        # Rate limit (traffic limiter). GOST v3 limits are "<scope> <in> <out>" with
        # "$" = whole service; the old bare "<n>B" entry had no rate, so GOST ignored
        # it and limited tunnels ran at full speed. The limit is enforced where
        # the tunnel's traffic is metered reliably: on the Iran-side client services
        # in direct mode, and on the relay server service in reverse mode (the
        # rudp services of the foreign client do not apply limiters).
        limiter_name = None
        try:
            mbps_val = float(spec.get("rate_limit_mbps") or 0)
        except (ValueError, TypeError):
            mbps_val = 0
        if mbps_val > 0:
            rate_bytes = int(mbps_val * 125000)  # Mbps to bytes/sec
            limiter_name = f"limiter-{tunnel_id}"
            config["limiters"] = [{"name": limiter_name, "limits": [f"$ {rate_bytes}B {rate_bytes}B"]}]

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
                    listener_metadata.update(mux_tuning)
                if gost_type == "kcp":
                    listener_metadata["nodelay"] = True
                    listener_metadata["interval"] = "20ms"
                    if low_latency:
                        listener_metadata["mode"] = "fast3"
                    listener_metadata["resend"] = 2
                    listener_metadata["nc"] = 1
                    kcp_key = spec.get("kcp_key") or auth_token or "smite-kcp-secure"
                    listener_metadata["crypt"] = spec.get("kcp_crypt") or "aes"
                    listener_metadata["key"] = kcp_key
            else:
                keepalive_interval = ka_interval_str
                listener_metadata = {
                    "keepAlive": True,
                    "keepAliveInterval": ka_interval_str,
                    "keepAliveTimeout": ka_timeout_str,
                    "idleTimeout": "300s",
                    "nodelay": True,
                    "bufferSize": 262144,
                }
                if spec.get("ws_path"):
                    listener_metadata["path"] = spec.get("ws_path")
                if ws_mux:
                    listener_metadata["mux"] = True
                    listener_metadata["nodelay"] = True
                    listener_metadata.update(mux_tuning)
                if is_reverse:
                    listener_metadata["bind"] = True
                if enable_mux:
                    listener_metadata["mux.type"] = mux_type
                    listener_metadata["mux"] = True
                    listener_metadata["nodelay"] = True
                    listener_metadata.update(mux_tuning)
                
            # SSH as a data channel for the relay protocol needs the "ssh" listener
            # (client side uses the "ssh" dialer + relay connector). The "sshd"
            # listener only speaks SSH port-forwarding and rejected every relay
            # stream, so GOST SSH-transport tunnels never carried traffic.
            server_listener_type = "ssh" if gost_type in ("ssh", "sshd") else gost_type
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
                    _ensure_rsa_private_key(ssh_key_path)
                if ssh_key_path.exists():
                    listener_metadata["hostKey"] = str(ssh_key_path)
                    listener_metadata["privateKeyFile"] = str(ssh_key_path)
                if auth_token:
                    listener["auth"] = {
                        "username": "smite",
                        "password": auth_token
                    }

            if listener_metadata:
                listener["metadata"] = listener_metadata
            
            grpc_with_tls = gost_type == "grpc" and (
                security_type in ["tls", "utls"]
                or bool(spec.get("tls_cert"))
                or bool(spec.get("tls_cert_pem"))
                or bool(spec.get("custom_sni"))
            )
            requires_tls = (
                security_type in ["tls", "utls"]
                or gost_type in ["wss", "mwss", "tls", "mtls", "quic"]
                or grpc_with_tls
            )

            if requires_tls and gost_type not in ["tcp", "udp", "rtcp", "rudp", "kcp", "ssh", "sshd"]:
                cert_path = self.config_dir / f"cert_{tunnel_id}.pem"
                key_path = self.config_dir / f"key_{tunnel_id}.pem"
                
                # Panel-distributed cert wins; legacy tls_cert/tls_key fields
                # remain supported for older panels.
                cert_pem = spec.get("tls_cert_pem") or spec.get("tls_cert")
                key_pem = spec.get("tls_key_pem") or spec.get("tls_key")
                if cert_pem and key_pem:
                    cert_path.write_text(str(cert_pem).strip())
                    key_path.write_text(str(key_pem).strip())
                elif not cert_path.exists() or not key_path.exists():
                    sni_domain = spec.get("custom_sni") or spec.get("stealth_domain") or spec.get("sni") or "www.cloudflare.com"
                    _ensure_self_signed_cert_and_key(cert_path, key_path, sni_domain)
                
                if cert_path.exists() and key_path.exists():
                    listener["tls"] = {
                        "certFile": str(cert_path),
                        "keyFile": str(key_path)
                    }
                else:
                    logger.warning(
                        f"GOST TLS listener for tunnel {tunnel_id} missing certificate files; proceeding without TLS listener config"
                    )
            elif gost_type == "grpc" and not grpc_with_tls:
                if "metadata" not in listener:
                    listener["metadata"] = {}
                listener["metadata"]["insecure"] = True
                
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
                handler_metadata.update(mux_tuning)
                
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
            if limiter_name and is_reverse:
                service["limiter"] = limiter_name

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
                dialer_metadata["host"] = str(spec.get("custom_host")).strip()
            elif spec.get("stealth_domain") and not spec.get("cdn_mode"):
                dialer_metadata["host"] = str(spec.get("stealth_domain")).strip()
                
            dialer_tls = {}
            # custom_sni takes priority over stealth_domain
            sni_candidate = spec.get("custom_sni") or spec.get("stealth_domain") or spec.get("sni")
            tls_ca_pem = spec.get("tls_ca_cert_pem") or spec.get("tls_ca")
            # Only these carriers authenticate the tunnel server itself. UDP/KCP/SSH
            # have no TLS listener, so a tunnel CA would be attached to a transport
            # that never performs the handshake; their TLS dialer (if any) passes
            # through to the target service instead.
            server_terminates_tls = (
                gost_type in ["wss", "mwss", "tls", "mtls", "quic"]
                or (gost_type in ["ws", "mws"] and security_type in ["tls", "utls"])
                or (
                    gost_type == "grpc"
                    and (security_type in ["tls", "utls"] or bool(sni_candidate or tls_ca_pem))
                )
            )
            if server_terminates_tls:
                if security_type == "utls":
                    # The pinned core has no uTLS dialer; never pretend otherwise.
                    logger.warning(
                        f"GOST tunnel {tunnel_id}: uTLS is unsupported by the installed core; "
                        "falling back to standard TLS"
                    )
                if tls_ca_pem:
                    ca_path = self.config_dir / f"gost_{tunnel_id}_ca.pem"
                    with open(ca_path, "w") as cf:
                        cf.write(tls_ca_pem)
                    try:
                        os.chmod(ca_path, 0o600)
                    except Exception:
                        pass
                    dialer_tls["caFile"] = str(ca_path)
                    dialer_tls["secure"] = True
                    dialer_tls["serverName"] = str(sni_candidate).strip() if sni_candidate else server_ip
                else:
                    # Legacy peers did not distribute a CA; keep the tunnel
                    # working but surface that the listener is unauthenticated.
                    dialer_tls["secure"] = False
                    if sni_candidate:
                        dialer_tls["serverName"] = str(sni_candidate).strip()
                    elif server_ip:
                        dialer_tls["serverName"] = server_ip
            elif security_type in ["tls", "utls"]:
                # Pass-through TLS: the peer is the target service, not the
                # tunnel server. Preserve the historical behaviour exactly.
                dialer_tls["secure"] = False
                if sni_candidate:
                    dialer_tls["serverName"] = str(sni_candidate).strip()
                else:
                    dialer_tls["serverName"] = "www.google.com"

            # Configure gRPC authority (grpc.host) matching custom_host or fallback to serverName
            if gost_type == "grpc":
                if "host" in dialer_metadata:
                    dialer_metadata["grpc.host"] = dialer_metadata["host"]
                else:
                    grpc_auth = dialer_tls.get("serverName") or "www.google.com"
                    dialer_metadata["host"] = grpc_auth
                    dialer_metadata["grpc.host"] = grpc_auth
                
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
                    if low_latency:
                        dialer_metadata["mode"] = "fast3"
                    dialer_metadata["resend"] = 2
                    dialer_metadata["nc"] = 1
                    kcp_key = spec.get("kcp_key") or auth_token or "smite-kcp-secure"
                    dialer_metadata["crypt"] = spec.get("kcp_crypt") or "aes"
                    dialer_metadata["key"] = kcp_key
                if enable_mux and gost_type not in ["udp", "rudp"]:
                    dialer_metadata["mux.type"] = mux_type
                    dialer_metadata["mux"] = True
                    dialer_metadata["nodelay"] = True
                    dialer_metadata.update(mux_tuning)
            else:
                keepalive_interval = ka_interval_str
                dialer_metadata["keepAlive"] = True
                dialer_metadata["keepAliveInterval"] = ka_interval_str
                dialer_metadata["keepAliveTimeout"] = ka_timeout_str
                dialer_metadata["timeout"] = "20s"
                dialer_metadata["idleTimeout"] = "300s"
                dialer_metadata["nodelay"] = True
                dialer_metadata["bufferSize"] = 262144
                if ws_mux:
                    dialer_metadata["mux"] = True
                    dialer_metadata["nodelay"] = True
                    dialer_metadata.update(mux_tuning)
                if enable_mux:
                    dialer_metadata["mux.type"] = mux_type
                    dialer_metadata["mux"] = True
                    dialer_metadata["nodelay"] = True
                    dialer_metadata.update(mux_tuning)
            
            if dialer_metadata:
                dialer["metadata"] = dialer_metadata
            
            grpc_with_tls = gost_type == "grpc" and (
                security_type in ["tls", "utls"]
                or bool(spec.get("tls_cert"))
                or bool(spec.get("tls_cert_pem"))
                or bool(spec.get("custom_sni"))
            )
            requires_tls = (
                security_type in ["tls", "utls"]
                or gost_type in ["wss", "mwss", "tls", "mtls", "quic"]
                or grpc_with_tls
            )

            if requires_tls and gost_type not in ["udp", "kcp", "ssh", "sshd"]:
                if dialer_tls:
                    dialer["tls"] = dialer_tls
                else:
                    dialer["tls"] = {"secure": False}
            elif gost_type == "grpc" and not grpc_with_tls:
                dialer_metadata["insecure"] = True
                
            # Generate node objects for primary and failover IPs
            hop_nodes = []
            
            connector_metadata = {
                "bufferSize": 262144
            }
            if enable_mux:
                connector_metadata["mux.type"] = mux_type
                connector_metadata["mux"] = True
                connector_metadata["nodelay"] = True
                connector_metadata["keepAlive"] = True
                connector_metadata.update(mux_tuning)
            
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
            
            # Create Local Listeners
            default_target_address = spec.get("target_host") or spec.get("remote_ip") or '127.0.0.1'
            fn_ip = spec.get("foreign_node_ip")
            if default_target_address == spec.get("server_ip") or (fn_ip and default_target_address == fn_ip):
                default_target_address = '127.0.0.1'

            # Dynamic UDP buffer scaling based on number of ports
            num_ports = len(ports)
            if num_ports <= 5:
                udp_read_buf_size = 4194304  # 4MB for 1-5 ports
                udp_read_queue_size = 2048
            elif num_ports <= 20:
                udp_read_buf_size = 1048576  # 1MB for 6-20 ports
                udp_read_queue_size = 1024
            else:
                udp_read_buf_size = 262144   # 256KB for >20 ports
                udp_read_queue_size = 512

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

                if target_address == spec.get("server_ip") or (fn_ip and target_address == fn_ip):
                    target_address = '127.0.0.1'
                    
                target_addr_formatted = f"[{target_address}]:{target_port_num}" if ":" in target_address and not target_address.startswith("[") else f"{target_address}:{target_port_num}"
                listen_addr = f":{port_num}" if is_reverse else (f"[::]:{port_num}" if use_ipv6 else f"0.0.0.0:{port_num}")
                
                if tunnel_proto in ["tcp", "tcp+udp"]:
                    if is_reverse:
                        listener_tcp = {
                            "type": "rtcp",
                            "chain": f"chain-{tunnel_id}",
                            "metadata": {
                                "keepAlive": True,
                                "ttl": reverse_ttl_str,
                                "keepalive.idle": ka_interval_str,
                                "keepalive.interval": ka_interval_str,
                                "keepalive.timeout": ka_timeout_str,
                                "bufferSize": 262144
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
                    if limiter_name and not is_reverse:
                        service_tcp["limiter"] = limiter_name
                    
                    config["services"].append(service_tcp)
                
                if tunnel_proto in ["udp", "tcp+udp"]:
                    # UDP session handling (all local to this side):
                    # - ttl: a client channel is only released after 120s of silence
                    #   (was 10s/30s), so idle moments in a game no longer reset the
                    #   session (new relay handshake + new source port = lag spike).
                    # - readBufferSize scaled dynamically based on port count to prevent memory exhaustion
                    # - readQueueSize: absorbs bursts instead of dropping packets.
                    udp_handler_metadata = {
                        "ttl": UDP_SESSION_TTL,
                        "readTimeout": UDP_SESSION_TTL,
                        "bufferSize": 262144,
                        "retryDelay": "1s"
                    }
                    if is_reverse:
                        listener_udp = {
                            "type": "rudp",
                            "chain": f"chain-{tunnel_id}",
                            "metadata": {
                                "keepAlive": True,
                                "ttl": reverse_ttl_str,
                                "keepalive.idle": ka_interval_str,
                                "keepalive.interval": ka_interval_str,
                                "keepalive.timeout": ka_timeout_str,
                                "readTimeout": UDP_SESSION_TTL,
                                "bufferSize": 262144,
                                "readBufferSize": udp_read_buf_size,
                                "readQueueSize": udp_read_queue_size,
                            }
                        }
                        handler_udp = {
                            "type": "udp",
                            "retries": 3,
                            "metadata": udp_handler_metadata
                        }
                    else:
                        # keepalive=true is essential: the GOST udp listener otherwise closes a
                        # client channel after the FIRST response, so every following packet
                        # opened a new relay connection (huge jitter, broken game sessions).
                        listener_udp = {
                            "type": "udp",
                            "metadata": {
                                "keepalive": True,
                                "keepAlive": True,
                                "ttl": UDP_SESSION_TTL,
                                "readTimeout": UDP_SESSION_TTL,
                                "bufferSize": 262144,
                                "readBufferSize": udp_read_buf_size,
                                "readQueueSize": udp_read_queue_size,
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
                    if limiter_name and not is_reverse:
                        service_udp["limiter"] = limiter_name
                    
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
        log_f = _open_core_log(log_file)
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
        _set_port_owner(tunnel_id)
        pid = _get_verified_tunnel_pid(tunnel_id, self.name)
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
        """Get status of GOST tunnel process, configuration, and port health"""
        config_file = self.config_dir / f"{tunnel_id}.json"
        is_running = False
        
        if tunnel_id in self.processes:
            proc = self.processes[tunnel_id]
            is_running = proc.returncode is None
        
        if not is_running:
            is_running = _is_tunnel_pid_alive(tunnel_id, "gost")
        
        config_exists = config_file.exists()
        port_healthy = None
        if config_exists and is_running:
            try:
                cfg_data = json.loads(config_file.read_text(encoding="utf-8", errors="ignore"))
                server_checked = False
                client_checked = False
                client_ports_ok = True
                for svc in cfg_data.get("services", []):
                    svc_name = svc.get("name", "")
                    if svc_name == f"gost-server-{tunnel_id}":
                        addr = svc.get("addr", "")
                        if ":" in addr:
                            p_str = addr.rsplit(":", 1)[1]
                            if p_str.isdigit():
                                port_num = int(p_str)
                                proto = "udp" if svc.get("listener", {}).get("type") in ["kcp", "quic", "udp", "rudp"] else "tcp"
                                port_healthy = is_port_listening_locally(port_num, proto)
                                server_checked = True
                                break
                    elif svc_name.startswith(("tcp-in-", "udp-in-")):
                        lis_type = svc.get("listener", {}).get("type", "")
                        if lis_type in ["tcp", "udp"]:
                            addr = svc.get("addr", "")
                            if ":" in addr:
                                p_str = addr.rsplit(":", 1)[1]
                                if p_str.isdigit():
                                    client_checked = True
                                    if not is_port_listening_locally(int(p_str), lis_type):
                                        client_ports_ok = False
                if not server_checked and client_checked:
                    port_healthy = client_ports_ok
            except Exception:
                pass

        return {
            "active": is_running,
            "type": "gost",
            "config_exists": config_exists,
            "process_running": is_running,
            "port_healthy": port_healthy if port_healthy is not None else True,
        }


CORE_SPEC_TRANSPORTS = {
    "gost": {"tcp", "ws", "wss", "mws", "mwss", "quic", "grpc", "kcp", "ssh", "tls", "mtcp", "mtls", "tcpmux", "mux", "wssmux"},
    "rathole": {"tcp", "noise", "ws", "wss", "tls"},
    "backhaul": {"tcp", "tcpmux", "ws", "wsmux", "wss", "wssmux", "mux"},
    "chisel": {"ws", "wss", "tls"},
    "frp": {"tcp", "kcp", "quic", "websocket", "wss", "tls", "tcpmux"},
}

# Values that used to be accepted (or silently coerced) and must keep working
# for tunnels that are already stored on this node during a rolling upgrade.
CORE_SPEC_TRANSPORT_ALIASES = {
    "gost": {"websocket": "ws", "https": "wss", "tls": "wss", "mux": "mws", "http2": "grpc"},
    "rathole": {"websocket": "ws", "https": "wss", "tls": "wss"},
    # "udp" here is the legacy payload-as-transport spelling, handled by the adapter.
    "backhaul": {"websocket": "ws", "https": "wss", "tls": "wss", "mux": "tcpmux", "udp": "tcp", "tcp+udp": "tcp"},
    "chisel": {"websocket": "ws", "https": "wss", "tls": "wss"},
    "frp": {"ws": "websocket", "https": "wss", "tls": "wss"},
}

CORE_SPEC_TYPES = {
    "gost": {"tcp", "udp", "tcp+udp", "grpc", "tcpmux", "ws", "wss", "mwss", "mws", "quic", "tls", "kcp", "ssh", "all", "reverse", "relay", "tunnel"},
    "rathole": {"tcp", "udp", "tcp+udp", "noise", "all"},
    "backhaul": {"tcp", "udp", "tcp+udp", "tcpmux", "mux", "all", "ws", "wsmux", "wss", "wssmux"},
    "chisel": {"tcp", "udp", "tcp+udp", "all", "socks5"},
    "frp": {"tcp", "udp", "tcp+udp", "http", "https", "stcp", "sudp", "xtcp", "tcpmux", "all"},
}


def validate_core_spec(core: str, spec: Dict[str, Any]) -> None:
    """Reject a spec the core cannot honour before any running process is touched.

    Only the carrier/type vocabulary is checked here; adapters still validate
    ports, keys and targets. The goal is that an unknown value can no longer be
    silently downgraded to plain TCP or another default.
    """
    core_key = (core or "").strip().lower()
    if core_key not in CORE_SPEC_TRANSPORTS:
        raise ValueError(f"Unknown tunnel core: {core}")
    if not isinstance(spec, dict) or not spec:
        raise ValueError(f"{core_key}: empty tunnel spec")

    raw_transport = str(spec.get("transport") or spec.get("transport_type") or "").strip().lower()
    if raw_transport:
        normalized = CORE_SPEC_TRANSPORT_ALIASES.get(core_key, {}).get(raw_transport, raw_transport)
        if normalized not in CORE_SPEC_TRANSPORTS[core_key]:
            raise ValueError(
                f"{core_key}: unsupported transport '{raw_transport}'. "
                f"Allowed: {sorted(CORE_SPEC_TRANSPORTS[core_key])}"
            )

    raw_type = str(spec.get("tunnel_type") or spec.get("type") or "").strip().lower()
    if raw_type and raw_type not in CORE_SPEC_TYPES[core_key]:
        raise ValueError(
            f"{core_key}: unsupported tunnel type '{raw_type}'. Allowed: {sorted(CORE_SPEC_TYPES[core_key])}"
        )

    if core_key == "backhaul" and raw_type in ("udp", "tcp+udp"):
        normalized = CORE_SPEC_TRANSPORT_ALIASES["backhaul"].get(raw_transport, raw_transport)
        if normalized in {"ws", "wsmux", "wss", "wssmux"}:
            raise ValueError(
                f"backhaul: UDP over {raw_transport} is not implemented by the core; use tcp or tcpmux"
            )


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
        self._pending_workers = _PENDING_CORE_WORKERS
        self._shutting_down = False
        # Reconciliation safety: an orphan is only purged after it was reported
        # missing by the panel in consecutive registrations (not on a single glitch).
        self._orphan_strikes: Dict[str, int] = {}
        # Set by _load_tunnels; the process sweep never kills anything unless the
        # persisted configuration was read successfully.
        self._configs_trusted = False
        self._stray_strikes: Dict[int, int] = {}
        logger.info(f"Tunnel persistence file: {self.tunnels_file}")

        # Every tunnel configured on this node is protected from cross-tunnel port freeing
        global _protected_tunnels_provider
        _protected_tunnels_provider = lambda: set(self.tunnel_configs.keys()) | set(self.active_tunnels.keys())

    # Keys that never affect the generated core configuration. They are stripped
    # before comparing/persisting specs so they can't trigger needless restarts.
    VOLATILE_SPEC_KEYS = {"force_restart", "latency_ms", "_pending_reapply"}

    @classmethod
    def _normalize_spec(cls, spec: Any) -> Dict[str, Any]:
        if not isinstance(spec, dict):
            return {}
        return {k: v for k, v in spec.items() if k not in cls.VOLATILE_SPEC_KEYS}
    
    def _get_tunnel_lock(self, tunnel_id: str) -> asyncio.Lock:
        if tunnel_id not in self._tunnel_locks:
            self._tunnel_locks[tunnel_id] = asyncio.Lock()
        return self._tunnel_locks[tunnel_id]

    def get_adapter(self, tunnel_core: str) -> Optional[CoreAdapter]:
        """Get adapter for tunnel core"""
        return self.adapters.get(tunnel_core)
    
    @property
    def _tunnels_backup_file(self) -> Path:
        return self.tunnels_file.with_name(self.tunnels_file.name + ".bak")

    @staticmethod
    def _read_tunnels_json(path: Path) -> Optional[Dict[str, Dict[str, Any]]]:
        import json
        if not path.exists() or path.stat().st_size == 0:
            return None
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return None
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("tunnels file must contain a JSON object")
        return data

    def _load_tunnels(self):
        """Load persisted tunnel configurations (falls back to the last good backup if the file is corrupt)"""
        self.tunnel_configs = {}
        self._configs_trusted = False
        if not self.tunnels_file.exists() and not self._tunnels_backup_file.exists():
            logger.info(f"No tunnel configurations file found at {self.tunnels_file} (this is normal for new nodes)")
            self._configs_trusted = True
            return

        for candidate in (self.tunnels_file, self._tunnels_backup_file):
            try:
                data = self._read_tunnels_json(candidate)
            except Exception as e:
                logger.error(f"Failed to load tunnel configurations from {candidate}: {e}")
                continue
            if data is None:
                # Missing/zero-length file: _save_tunnels always writes at least "{}",
                # so this indicates an interrupted write -> try the backup.
                if candidate == self.tunnels_file and candidate.exists():
                    logger.warning(f"Tunnel config file {candidate} is empty")
                continue
            self.tunnel_configs = data
            self._configs_trusted = True
            if candidate != self.tunnels_file:
                logger.warning(f"Recovered {len(data)} tunnel configurations from backup {candidate}")
            break

        logger.info(f"Loaded {len(self.tunnel_configs)} persisted tunnel configurations from {self.tunnels_file}")
        for tunnel_id, config in self.tunnel_configs.items():
            core = config.get("core", "unknown") if isinstance(config, dict) else "invalid"
            mode = (config.get("spec") or {}).get("mode", "N/A") if isinstance(config, dict) else "N/A"
            logger.info(f"  - Tunnel {tunnel_id}: core={core}, mode={mode}")

    def _save_tunnels(self):
        """Save tunnel configurations to disk (atomic write + fsync + last-good backup)"""
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
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                try:
                    os.chmod(temp_file, 0o600)
                except Exception:
                    pass
                # Keep the previous good copy so a corrupt/truncated write can never lose all tunnels
                try:
                    if self.tunnels_file.exists() and self.tunnels_file.stat().st_size > 0:
                        self._read_tunnels_json(self.tunnels_file)
                        shutil.copy2(self.tunnels_file, self._tunnels_backup_file)
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
        counters = {"restored": 0, "adopted": 0, "failed": 0}
        # Bounded parallelism: after a container restart every tunnel is down, so
        # restoring them one by one would keep the last tunnels offline for minutes.
        semaphore = asyncio.Semaphore(6)

        async def _restore_one(tunnel_id: str, config: Dict[str, Any]) -> None:
            async with semaphore:
                try:
                    if not isinstance(config, dict):
                        logger.warning(f"Tunnel {tunnel_id}: Invalid persisted entry, skipping")
                        counters["failed"] += 1
                        return
                    tunnel_core = config.get("core")
                    spec = config.get("spec", {})

                    if not tunnel_core:
                        logger.warning(f"Tunnel {tunnel_id}: Missing core, skipping")
                        counters["failed"] += 1
                        return

                    if not spec:
                        logger.warning(f"Tunnel {tunnel_id}: Empty spec, skipping")
                        counters["failed"] += 1
                        return

                    adapter = self.get_adapter(tunnel_core)
                    if not adapter:
                        logger.warning(f"Tunnel {tunnel_id}: Unknown core {tunnel_core}, skipping")
                        counters["failed"] += 1
                        return

                    async with self._get_tunnel_lock(tunnel_id):
                        # Non-destructive adoption: keep a healthy running process untouched
                        tunnel_status = adapter.status(tunnel_id)
                        if tunnel_status.get("process_running", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                            self.active_tunnels[tunnel_id] = adapter
                            counters["adopted"] += 1
                            logger.info(f"Tunnel {tunnel_id} core ({tunnel_core}) is ALREADY running healthy (PID {_get_tunnel_pid(tunnel_id)}). Preserved connection without restart.")
                            return

                        mode = spec.get('mode', 'N/A')
                        logger.info(f"Restoring tunnel {tunnel_id}: core={tunnel_core}, mode={mode}, spec_keys={list(spec.keys())}")

                        if tunnel_core in ["rathole", "backhaul", "chisel", "frp", "gost"] and mode == 'N/A':
                            logger.warning(f"Tunnel {tunnel_id}: Reverse tunnel missing mode field, defaulting to client")
                            spec['mode'] = 'client'

                        _set_port_owner(tunnel_id)
                        try:
                            await adapter.apply(tunnel_id, spec)
                            self.active_tunnels[tunnel_id] = adapter
                            counters["restored"] += 1
                            logger.info(f"Successfully restored tunnel {tunnel_id} (core={tunnel_core}, mode={spec.get('mode', 'N/A')})")
                        except Exception as apply_error:
                            logger.error(f"Failed to apply tunnel {tunnel_id} during restoration: {apply_error}", exc_info=True)
                            counters["failed"] += 1
                except Exception as e:
                    logger.error(f"Failed to restore tunnel {tunnel_id}: {e}", exc_info=True)
                    counters["failed"] += 1

        await asyncio.gather(
            *[_restore_one(tid, cfg) for tid, cfg in list(self.tunnel_configs.items())],
            return_exceptions=True,
        )

        logger.info(
            f"Tunnel restoration completed: {counters['adopted']} adopted (no restart), "
            f"{counters['restored']} restarted, {counters['failed']} failed"
        )
        self.start_watchdog()

    @staticmethod
    def _extract_spec_ports(spec: Any) -> Set[int]:
        """Extract all service and control ports defined in a tunnel spec"""
        ports: Set[int] = set()
        if isinstance(spec, str):
            try:
                import json
                spec = json.loads(spec)
            except Exception:
                return ports
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
                    else:
                        _, local_port, _ = parse_address_port(lhs)
                        if local_port and 0 < local_port <= 65535:
                            ports.add(local_port)
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
        restart_failures: Dict[str, int] = {}
        last_log_sweep: float = 0.0
        cycle = 0
        
        while True:
            try:
                await asyncio.sleep(15)
                now = time.time()
                cycle += 1

                # Periodic log truncation check (every 5 minutes / 300s) to prevent VPS disk exhaustion
                if now - last_log_sweep >= 300:
                    last_log_sweep = now
                    try:
                        await _run_core_worker(self._rotate_oversized_logs)
                    except Exception as e_sweep:
                        logger.debug(f"Watchdog: Error during log sweep: {e_sweep}")

                # Reap exited adopted core processes (children of this PID after an
                # in-place agent reload) so they never linger as zombies.
                self._reap_adopted_children()

                # Kill stray core processes (duplicates of a tunnel, or processes of a
                # tunnel that is no longer configured here) once a minute
                if cycle % 4 == 0:
                    try:
                        await self._sweep_stray_processes()
                    except Exception as e_sw:
                        logger.debug(f"Watchdog: stray process sweep failed: {e_sw}")

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
                            # The spec port sets include non-listening ports (remote
                            # server ports, forward targets), so an overlap is often not
                            # a real conflict. Recovery can no longer kill the other
                            # tunnel (free_ports protects configured tunnels), so try
                            # anyway: a real bind conflict just fails and backs off.
                            logger.warning(
                                f"Watchdog: tunnel {tunnel_id} ({tunnel_core}) shares port "
                                f"{conflicting_active_tunnel[1]} with active tunnel {conflicting_active_tunnel[0]} "
                                f"in its spec; attempting recovery without touching the other tunnel."
                            )
                        
                        # 2. Proceed with safe recovery. The jitter prevents every
                        # node from restarting the same tunnel in the same second.
                        cur_delay = backoff_delay.get(tunnel_id, 15)
                        logger.warning(f"Watchdog: tunnel {tunnel_id} ({tunnel_core}) is inactive/dead! Auto-recovering...")
                        await asyncio.sleep(random.uniform(0.0, 2.0))
                        try:
                            async with self._get_tunnel_lock(tunnel_id):
                                # The tunnel may have been removed (or re-applied with a new
                                # spec) while we were waiting for the lock: use the current
                                # config, and never resurrect a removed tunnel.
                                current = self.tunnel_configs.get(tunnel_id)
                                if not current or current.get("core") != tunnel_core:
                                    continue
                                spec = current.get("spec") or {}
                                status = adapter.status(tunnel_id)
                                if not (status.get("process_running", False) or status.get("active", False)):
                                    # Cleanly remove old process/sockets and reapply
                                    _set_port_owner(tunnel_id)
                                    await adapter.remove(tunnel_id)
                                    await asyncio.sleep(0.2)
                                    await adapter.apply(tunnel_id, spec)
                                    self.active_tunnels[tunnel_id] = adapter
                            backoff_delay.pop(tunnel_id, None)
                            next_retry_at.pop(tunnel_id, None)
                            restart_failures.pop(tunnel_id, None)
                            logger.info(f"Watchdog: successfully revived and restored tunnel {tunnel_id} ({tunnel_core})")
                        except Exception as e:
                            failures = restart_failures.get(tunnel_id, 0) + 1
                            restart_failures[tunnel_id] = failures
                            logger.error(
                                f"Watchdog: failed to auto-recover tunnel {tunnel_id} "
                                f"(attempt {failures}): {e}"
                            )
                            next_delay = min(cur_delay * 2, 120)
                            if failures >= 10:
                                # Sustained restart budget: keep trying, but stop a tight loop.
                                next_delay = 300
                                logger.critical(
                                    f"Watchdog: tunnel {tunnel_id} ({tunnel_core}) failed recovery "
                                    f"{failures} times in a row; backing off to {next_delay}s. "
                                    "This needs operator attention."
                                )
                            backoff_delay[tunnel_id] = next_delay
                            next_retry_at[tunnel_id] = now + next_delay * random.uniform(0.9, 1.1)
                    else:
                        backoff_delay.pop(tunnel_id, None)
                        next_retry_at.pop(tunnel_id, None)
                        restart_failures.pop(tunnel_id, None)
            except asyncio.CancelledError:
                logger.info("AdapterManager watchdog loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in AdapterManager watchdog loop: {e}", exc_info=True)

    async def _sweep_stray_processes(self) -> List[int]:
        """Kill core processes this agent started that no tunnel owns anymore.

        - process of a tunnel that is not configured on this node (deleted tunnel
          whose process survived) -> stray
        - extra process of a configured tunnel (not its recorded/tracked PID, e.g. a
          duplicate left by an interrupted apply) -> stray

        Safety: tunnels whose lock is held (being applied/removed/restored) are
        skipped, nothing happens unless tunnels.json was loaded successfully, and a
        process must be seen as stray on two consecutive sweeps before it is killed.
        """
        if not self._configs_trusted:
            return []
        tagged = _find_tagged_core_processes()
        if not tagged:
            self._stray_strikes.clear()
            return []
        recorded = _all_recorded_pids()
        tracked: Dict[str, Set[int]] = {}
        for adapter in self.adapters.values():
            for key, proc in list(getattr(adapter, "processes", {}).items()):
                pid = getattr(proc, "pid", None)
                if pid:
                    tracked.setdefault(key, set()).add(pid)

        stray_now: Set[int] = set()
        for pid, tag in tagged.items():
            if not tag:
                continue
            lock = self._tunnel_locks.get(tag)
            if lock is not None and lock.locked():
                continue
            if tag not in self.tunnel_configs:
                stray_now.add(pid)
                continue
            allowed = {recorded.get(tag), recorded.get(f"{tag}_provider")}
            allowed |= tracked.get(tag, set()) | tracked.get(f"{tag}_provider", set())
            if pid not in allowed:
                stray_now.add(pid)

        for pid in list(self._stray_strikes):
            if pid not in stray_now:
                self._stray_strikes.pop(pid, None)
        to_kill = []
        for pid in stray_now:
            self._stray_strikes[pid] = self._stray_strikes.get(pid, 0) + 1
            if self._stray_strikes[pid] >= 2:
                to_kill.append(pid)
        if to_kill:
            logger.warning(f"Watchdog: killing stray core processes {sorted(to_kill)} (tunnels: {sorted({tagged[p] for p in to_kill})})")
            await _run_core_worker(_kill_pids, to_kill)
            for pid in to_kill:
                self._stray_strikes.pop(pid, None)
        return sorted(to_kill)

    LOG_ROTATE_THRESHOLD = 10 * 1024 * 1024
    LOG_ROTATE_KEEP = 256 * 1024

    def _rotate_oversized_logs(self) -> None:
        """Shrink oversized agent and core logs in place, keeping the newest 256KB.

        Core logs live under /etc/smite-node/<core>/ and are written by long-running
        processes; they are opened in O_APPEND mode so in-place truncation works.
        """
        roots = [self.config_dir, Path("/etc/smite-node")]
        seen: Set[Path] = set()
        for root in roots:
            try:
                if not root.exists():
                    continue
                candidates = list(root.glob("*.log")) + list(root.glob("*/*.log"))
            except Exception:
                continue
            for log_p in candidates:
                try:
                    if log_p in seen or not log_p.is_file():
                        continue
                    seen.add(log_p)
                    size = log_p.stat().st_size
                    if size <= self.LOG_ROTATE_THRESHOLD:
                        continue
                    with open(log_p, "r+b") as lf:
                        lf.seek(max(0, size - self.LOG_ROTATE_KEEP))
                        tail = lf.read()
                        lf.seek(0)
                        lf.write(f"[LOG ROTATED BY SMITE WATCHDOG AT {time.ctime()}]\n".encode("utf-8") + tail)
                        lf.truncate()
                        lf.flush()
                    logger.info(f"Watchdog: Rotated oversized log file {log_p} to last 256KB")
                except Exception as e_rot:
                    logger.debug(f"Watchdog: Could not rotate log file {log_p}: {e_rot}")

    def _reap_adopted_children(self) -> None:
        if os.name != 'posix':
            return
        tracked: Set[int] = set()
        for adapter in self.adapters.values():
            for proc in list(getattr(adapter, "processes", {}).values()):
                pid = getattr(proc, "pid", None)
                if pid:
                    tracked.add(pid)
        for pid in set(_all_recorded_pids().values()) - tracked:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            except Exception:
                pass

    def start_watchdog(self):
        """Start watchdog background task"""
        if not hasattr(self, '_watchdog_task') or self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
            logger.info("AdapterManager self-healing watchdog task started")

    def stop_watchdog(self):
        """Stop watchdog background task"""
        if hasattr(self, '_watchdog_task') and self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
    
    def _is_tunnel_known_locally(self, tunnel_id: str) -> bool:
        """True if anything of this tunnel exists on this node (config, process, PID, files or tagged process)."""
        if tunnel_id in self.tunnel_configs or tunnel_id in self.active_tunnels:
            return True
        if _get_tunnel_pid(tunnel_id) or _get_tunnel_pid(f"{tunnel_id}_provider"):
            return True
        for adapter in self.adapters.values():
            if tunnel_id in getattr(adapter, "processes", {}) or f"{tunnel_id}_provider" in getattr(adapter, "processes", {}):
                return True
            cfg_dir = getattr(adapter, "config_dir", None)
            try:
                if cfg_dir is not None and Path(cfg_dir).exists() and any(Path(cfg_dir).glob(f"*{tunnel_id}*")):
                    return True
            except Exception:
                pass
        return bool(_find_tagged_core_processes(tunnel_id))

    @staticmethod
    def _ports_from_disk_configs(tunnel_id: str) -> Set[int]:
        found: Set[int] = set()
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
                                    found.add(p_num)
                            for m in re.finditer(r'["\'](\d{1,5})/(?:tcp|udp)["\']', content, re.IGNORECASE):
                                p_num = int(m.group(1))
                                if 0 < p_num <= 65535:
                                    found.add(p_num)
                        except Exception:
                            pass
            except Exception:
                pass
        return found

    async def _remove_tunnel_unlocked(
        self,
        tunnel_id: str,
        purge: bool = False,
        ports: Optional[Iterable[Any]] = None,
        control_port: Optional[int] = None,
        core: Optional[str] = None
    ) -> Dict[str, Any]:
        """Remove a tunnel completely: config, every process (by any identity) and its ports.

        Order matters:
        1. The tunnel is de-configured and persisted FIRST, so neither the watchdog
           nor a restart can resurrect it while (or after) it is being killed.
        2. Processes are stopped through every identity we have: the tracked asyncio
           handle, the verified PID file, command-line patterns and the
           SMITE_TUNNEL_ID environment tag (finds orphans that lost their PID file).
        3. The listening ports are verified to be released; a process of this
           tunnel that survived is killed again and reported back to the panel.
        """
        _set_port_owner(tunnel_id)
        result: Dict[str, Any] = {"found": False, "killed_pids": [], "stuck_ports": []}
        if not self._is_tunnel_known_locally(tunnel_id):
            # Nothing of this tunnel exists here (e.g. fleet-wide ghost purge or a
            # retried removal). Never kill anything by port number on its behalf:
            # the same port numbers are usually used by other tunnels on this node.
            logger.info(f"Remove request for tunnel {tunnel_id}: not present on this node, nothing to do")
            return result

        result["found"] = True
        logger.info(f"Removing tunnel {tunnel_id} (purge={purge}, core={core})")
        saved_config = self.tunnel_configs.get(tunnel_id, {}) or {}
        t_core_name = core or saved_config.get("core")

        # 1. Gather every port this tunnel may hold
        target_ports: Set[int] = set()
        for p in list(ports or []) + ([control_port] if control_port else []):
            try:
                p_int = int(p)
                if 0 < p_int <= 65535:
                    target_ports.add(p_int)
            except (ValueError, TypeError):
                pass
        if saved_config.get("spec"):
            target_ports.update(_extract_ports_from_spec_dict(saved_config.get("spec")))
        target_ports.update(self._ports_from_disk_configs(tunnel_id))

        # 2. De-configure first and persist (no resurrection by watchdog/restore)
        if tunnel_id in self.tunnel_configs:
            del self.tunnel_configs[tunnel_id]
            self._save_tunnels()
        self._orphan_strikes.pop(tunnel_id, None)

        # 3. Stop through the primary adapter, then every other adapter that still
        #    has something of this tunnel (covers core switches)
        primary_adapter = self.active_tunnels.pop(tunnel_id, None)
        if not primary_adapter and t_core_name:
            primary_adapter = self.get_adapter(t_core_name)
        if primary_adapter:
            try:
                await primary_adapter.remove(tunnel_id, purge=purge)
            except TypeError:
                await primary_adapter.remove(tunnel_id)
            except Exception as e:
                logger.warning(f"Error in primary adapter remove for {tunnel_id}: {e}")

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

        # 4. Orphans: PID files, command-line patterns and the environment tag
        _set_port_owner(tunnel_id)
        patterns = [tunnel_id, f"{tunnel_id}.toml", f"{tunnel_id}.json", f"{tunnel_id}.yaml"]
        rec_pid = _get_verified_tunnel_pid(tunnel_id, t_core_name)
        await safe_stop_subprocess(patterns=patterns, pid=rec_pid)
        _remove_tunnel_pid(tunnel_id)

        prov_pid = _get_verified_tunnel_pid(f"{tunnel_id}_provider", "frp")
        if prov_pid:
            await safe_stop_subprocess(pid=prov_pid)
        _remove_tunnel_pid(f"{tunnel_id}_provider")

        tagged = _find_tagged_core_processes(tunnel_id)
        if tagged:
            logger.warning(f"Killing {len(tagged)} orphan process(es) of tunnel {tunnel_id} found by tag: {sorted(tagged)}")
            await _run_core_worker(_kill_pids, list(tagged))
            result["killed_pids"].extend(sorted(tagged))

        # 5. Free the ports and verify they really are released
        if target_ports:
            logger.info(f"Freeing and verifying ports {sorted(list(target_ports))} for tunnel {tunnel_id}")
            await free_ports(target_ports)
            stuck: Set[int] = set()
            for attempt in range(6):
                await asyncio.sleep(0.25 if attempt else 0.1)
                protected_ids = _get_protected_tunnel_ids()
                recorded_pids = _all_recorded_pids()
                stuck = set()
                for p in target_ports:
                    for hp in _find_pids_by_port_procfs(p, listen_only=True):
                        if hp in (os.getpid(), os.getppid(), 1):
                            continue
                        if _may_kill_port_holder(hp, tunnel_id, protected_ids, recorded_pids):
                            stuck.add(p)
                            logger.warning(f"Verification: killing stale core process {hp} still holding port {p}")
                            try:
                                os.kill(hp, _SIGKILL)
                                result["killed_pids"].append(hp)
                            except Exception:
                                pass
                if not stuck:
                    break
            result["stuck_ports"] = sorted(stuck)
            if stuck:
                logger.error(f"Tunnel {tunnel_id}: ports {sorted(stuck)} are still held after removal")

        logger.info(f"Tunnel {tunnel_id} removal complete. Ports checked: {sorted(list(target_ports))}")
        return result

    async def _purge_conflicting_orphans(self, tunnel_id: str, spec: Dict[str, Any], authoritative_ids: Iterable[str]) -> None:
        """Remove tunnels the panel no longer knows that use the ports of `spec`.

        Typical case: a tunnel was deleted while this node was unreachable and a new
        tunnel is created on the same port right away. Without this the leftover
        (still configured here, so protected) would keep the port until the
        periodic reconciliation, and the new tunnel would fail to start.
        """
        auth_set = {str(t) for t in authoritative_ids if t}
        auth_set.add(tunnel_id)
        wanted = self._extract_spec_ports(spec)
        if not wanted:
            return
        for other_id in sorted(set(self.tunnel_configs.keys()) - auth_set):
            # A tunnel with an operation in flight (e.g. another tunnel being created
            # concurrently, so it is missing from this request's list) is left alone.
            other_lock = self._tunnel_locks.get(other_id)
            if other_lock is not None and other_lock.locked():
                continue
            other_spec = (self.tunnel_configs.get(other_id) or {}).get("spec") or {}
            overlap = self._extract_spec_ports(other_spec) & wanted
            if not overlap:
                continue  # unrelated leftovers are handled by reconciliation/sweep
            logger.warning(
                f"Removing orphan tunnel {other_id} (unknown to the panel) before applying {tunnel_id}: "
                f"it holds ports {sorted(overlap)}"
            )
            try:
                async with self._get_tunnel_lock(other_id):
                    if other_id in self.tunnel_configs:
                        await self._remove_tunnel_unlocked(other_id, purge=True)
            except Exception as e:
                logger.error(f"Failed to remove orphan tunnel {other_id}: {e}")

    async def apply_tunnel(
        self,
        tunnel_id: str,
        tunnel_core: str,
        spec: Dict[str, Any],
        authoritative_ids: Optional[Iterable[str]] = None,
    ):
        """Apply tunnel using appropriate adapter - idempotent and zero-downtime if spec is unchanged"""
        clean_spec = self._normalize_spec(spec)
        adapter = self.get_adapter(tunnel_core)
        if not adapter:
            error_msg = f"Unknown tunnel core: {tunnel_core}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        # Reject an unrecognised carrier/type before any process or port is touched.
        validate_core_spec(tunnel_core, clean_spec)
        if authoritative_ids is not None:
            await self._purge_conflicting_orphans(tunnel_id, clean_spec, authoritative_ids)
        async with self._get_tunnel_lock(tunnel_id):
            logger.info(f"Applying tunnel {tunnel_id}: core={tunnel_core}")

            # Check if tunnel switched core type (e.g. rathole -> backhaul)
            existing_config = self.tunnel_configs.get(tunnel_id, {})
            previous_core = existing_config.get("core") or tunnel_core
            previous_spec = self._normalize_spec(existing_config.get("spec")) if existing_config.get("spec") else None
            old_core = existing_config.get("core")

            # Check if already running with exact same configuration (Idempotent Apply).
            # Volatile bookkeeping keys (force_restart, latency_ms, _pending_reapply) are
            # ignored so that they can never cause an unnecessary tunnel restart.
            _set_port_owner(tunnel_id)
            force_restart = bool(spec.get("force_restart", False))
            same_config = (
                existing_config.get("core") == tunnel_core
                and self._normalize_spec(existing_config.get("spec")) == clean_spec
            )
            if same_config and not force_restart:
                t_status = adapter.status(tunnel_id)
                if t_status.get("process_running", False) or _is_tunnel_pid_alive(tunnel_id, tunnel_core):
                    self.active_tunnels[tunnel_id] = adapter
                    if existing_config.get("spec") != clean_spec:
                        self.tunnel_configs[tunnel_id] = {"core": tunnel_core, "spec": dict(clean_spec)}
                        self._save_tunnels()
                    logger.info(f"Tunnel {tunnel_id} ({tunnel_core}) is already active and healthy with identical configuration. Skipping restart to keep traffic 100% uninterrupted.")
                    return

            # Validate the replacement before the previous core/process is removed,
            # so a rejected config never takes down a healthy tunnel.
            preflight = getattr(adapter, "preflight", None)
            if callable(preflight) and hasattr(type(adapter), "preflight"):
                await preflight(tunnel_id, clean_spec)

            if old_core and old_core != tunnel_core:
                logger.info(f"Tunnel {tunnel_id} switched core from {old_core} to {tunnel_core}. Purging old core instance first.")
                old_adapter = self.get_adapter(old_core)
                if old_adapter:
                    try:
                        await old_adapter.remove(tunnel_id, purge=True)
                    except Exception as e:
                        logger.warning(f"Error purging old {old_core} adapter for {tunnel_id}: {e}")
                self.active_tunnels.pop(tunnel_id, None)

            if tunnel_id in self.active_tunnels:
                logger.info(f"Tunnel {tunnel_id} configuration changed, restart forced, or process inactive; applying new configuration")
                old_adapter = self.active_tunnels[tunnel_id]
                try:
                    await old_adapter.remove(tunnel_id)
                except Exception as e:
                    logger.warning(f"Error removing old adapter process for tunnel {tunnel_id}: {e}")
                del self.active_tunnels[tunnel_id]

            adapter_name = getattr(adapter, "name", tunnel_core)
            logger.info(f"Using adapter: {adapter_name}, mode={spec.get('mode', 'N/A')}")
            try:
                await adapter.apply(tunnel_id, clean_spec)
            except Exception as apply_error:
                # The previous configuration (if any) was the last known good state.
                # Put it back so a rejected config never leaves the tunnel dead.
                if previous_spec:
                    logger.error(
                        f"Applying new configuration for tunnel {tunnel_id} failed ({apply_error}); "
                        "restoring the previous working configuration"
                    )
                    restore_adapter = self.get_adapter(previous_core) or adapter
                    try:
                        await restore_adapter.apply(tunnel_id, previous_spec)
                        self.active_tunnels[tunnel_id] = restore_adapter
                        self.tunnel_configs[tunnel_id] = {"core": previous_core, "spec": dict(previous_spec)}
                        self.start_watchdog()
                        self._save_tunnels()
                        logger.warning(
                            f"Previous configuration restored for tunnel {tunnel_id}; it keeps serving while the "
                            "rejected configuration is corrected"
                        )
                    except Exception as restore_error:
                        logger.critical(
                            f"Tunnel {tunnel_id} is down: new configuration failed ({apply_error}) and "
                            f"restoring the previous configuration also failed ({restore_error})"
                        )
                        self.active_tunnels.pop(tunnel_id, None)
                        # Keep tunnel_configs so the watchdog keeps retrying the last known good spec.
                else:
                    logger.error(f"Tunnel {tunnel_id} could not be started: {apply_error}")
                raise
            self.active_tunnels[tunnel_id] = adapter

            self.tunnel_configs[tunnel_id] = {
                "core": tunnel_core,
                "spec": dict(clean_spec)
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
        lock = self._get_tunnel_lock(tunnel_id)
        async with lock:
            result = await self._remove_tunnel_unlocked(
                tunnel_id,
                purge=purge,
                ports=ports,
                control_port=control_port,
                core=core
            )
        # Drop the lock object only when nobody else is queued on it; otherwise a
        # waiter (watchdog, re-apply) and a new caller would hold two different
        # locks for the same tunnel and run concurrently.
        if not lock.locked() and not getattr(lock, "_waiters", None):
            if self._tunnel_locks.get(tunnel_id) is lock:
                self._tunnel_locks.pop(tunnel_id, None)
        return result

    async def reconcile_active_tunnels(self, authoritative_ids: List[str]):
        """Reconcile local tunnels against authoritative list from panel.
        Orphan/ghost tunnels running locally or saved on disk that do not exist
        in authoritative_ids are safely purged.
        Active valid tunnels are left completely untouched (zero downtime).
        """
        auth_set = set(str(tid).strip() for tid in authoritative_ids if tid and str(tid).strip())
        
        # 1. Gather all local tunnel IDs currently tracked in memory / disk json
        local_ids = set(self.tunnel_configs.keys()) | set(self.active_tunnels.keys())
        
        # 2. Orphan core processes started by this agent that lost config and PID file
        for tag in _find_tagged_core_processes().values():
            if tag and SAFE_TUNNEL_ID_RE.match(tag):
                local_ids.add(tag)

        # 3. Check recorded PID files (e.g. detached processes)
        pdir = _get_pid_dir()
        if pdir.exists():
            try:
                for pf in pdir.glob("*.pid"):
                    stem = pf.stem
                    if stem.endswith("_provider"):
                        stem = stem[:-9]
                    if stem and len(stem) >= 8 and not stem.startswith("default"):
                        local_ids.add(stem)
            except Exception:
                pass

        candidates = [tid for tid in local_ids if tid not in auth_set]
        # Forget strikes of tunnels that are authoritative again
        for tid in list(self._orphan_strikes.keys()):
            if tid not in candidates:
                self._orphan_strikes.pop(tid, None)
        if not candidates:
            return

        # Safety net against a glitchy/partial panel answer: a tunnel is only purged
        # after it was missing in consecutive reconciliations (~1 minute apart). An
        # empty authoritative list (panel DB restored/reset?) needs even more evidence
        # because it would otherwise take every tunnel on this node down at once.
        required_strikes = 3 if not auth_set else 2
        orphans = []
        for tid in candidates:
            self._orphan_strikes[tid] = self._orphan_strikes.get(tid, 0) + 1
            if self._orphan_strikes[tid] >= required_strikes:
                orphans.append(tid)
            else:
                logger.warning(
                    f"Reconciliation: tunnel {tid} is not in the panel authoritative list "
                    f"(strike {self._orphan_strikes[tid]}/{required_strikes}); keeping it for now"
                )
        if not orphans:
            return

        logger.warning(f"Reconciliation: detected {len(orphans)} orphan/ghost tunnel(s) not in panel authoritative list: {orphans}. Purging cleanly...")
        for orphan_id in orphans:
            self._orphan_strikes.pop(orphan_id, None)
            try:
                await self.remove_tunnel(orphan_id, purge=True)
                logger.info(f"Reconciliation: successfully purged orphan tunnel {orphan_id}")
            except Exception as e:
                logger.error(f"Reconciliation: failed to purge orphan tunnel {orphan_id}: {e}")
    
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
                            if core in ["backhaul", "gost"] and (
                                spec.get("tunnel_type") == "tcp+udp" or proto == "tcp+udp"
                            ):
                                # Dual forward opens both a TCP and a UDP listener; BOTH must be up.
                                tcp_up = await asyncio.to_thread(is_port_listening_locally, p_num, proto="tcp")
                                udp_up = await asyncio.to_thread(is_port_listening_locally, p_num, proto="udp")
                                if tcp_up:
                                    listening_ports.append({"port": p_num, "type": "service_tcp"})
                                else:
                                    missing_ports.append({"port": p_num, "type": "service_tcp"})
                                if udp_up:
                                    listening_ports.append({"port": p_num, "type": "service_udp"})
                                else:
                                    missing_ports.append({"port": p_num, "type": "service_udp"})
                                continue
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
                        if (core in ["backhaul", "gost"]) and (
                            spec.get("tunnel_type") == "tcp+udp" or proto == "tcp+udp"
                        ):
                            tcp_up = await asyncio.to_thread(is_port_listening_locally, p_num, proto="tcp")
                            udp_up = await asyncio.to_thread(is_port_listening_locally, p_num, proto="udp")
                            if tcp_up:
                                listening_ports.append({"port": p_num, "type": "service_tcp"})
                            else:
                                missing_ports.append({"port": p_num, "type": "service_tcp"})
                            if udp_up:
                                listening_ports.append({"port": p_num, "type": "service_udp"})
                            else:
                                missing_ports.append({"port": p_num, "type": "service_udp"})
                            continue
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
        self._shutting_down = True
        self.stop_watchdog()
        if kill_processes:
            for tunnel_id, adapter in list(self.active_tunnels.items()):
                try:
                    await adapter.remove(tunnel_id)
                except Exception:
                    pass
        self.active_tunnels.clear()

