#!/usr/bin/env python3
"""
Smite Node CLI - Smart Multi-Instance Management Tool
"""
import os
import sys
import subprocess
import argparse
import shutil
import json
import hashlib
import base64
import re
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path


def discover_nodes():
    """Discover all Smite Node installations on the host."""
    candidates = []
    
    # Check /opt/smite-node and /opt/smite-node-*
    opt_dir = Path("/opt")
    if opt_dir.exists():
        for d in sorted(opt_dir.iterdir()):
            if d.is_dir() and (d.name == "smite-node" or d.name.startswith("smite-node-")):
                if (d / "docker-compose.yml").exists() or (d / ".env").exists():
                    candidates.append(d)
    
    # Check legacy /usr/local/node
    legacy_dir = Path("/usr/local/node")
    if legacy_dir.exists() and (legacy_dir / "docker-compose.yml").exists():
        if legacy_dir not in candidates:
            candidates.append(legacy_dir)
            
    # Check current directory
    cwd = Path.cwd()
    if (cwd / "docker-compose.yml").exists() and (cwd / "app").exists():
        if cwd not in candidates:
            candidates.append(cwd)

    nodes = []
    for idx, d in enumerate(candidates, start=1):
        compose_file = d / "docker-compose.yml"
        env_file = d / ".env"
        
        # Read .env metadata
        name = f"node-{idx}"
        port = 8888
        role = "unknown"
        panel_addr = "N/A"
        
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("NODE_NAME="):
                        name = line.split("=", 1)[1].strip('"\' ')
                    elif line.startswith("NODE_API_PORT="):
                        try:
                            port = int(line.split("=", 1)[1].strip('"\' '))
                        except ValueError:
                            pass
                    elif line.startswith("NODE_ROLE="):
                        role = line.split("=", 1)[1].strip('"\' ')
                    elif line.startswith("PANEL_ADDRESS="):
                        panel_addr = line.split("=", 1)[1].strip('"\' ')
            except Exception:
                pass
                
        # Extract container name from docker-compose.yml
        cname = "smite-node" if d.name == "smite-node" else d.name
        if compose_file.exists():
            try:
                for line in compose_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("container_name:"):
                        cname = line.split(":", 1)[1].strip('"\' ')
                        break
            except Exception:
                pass
                
        # Check Docker container status
        docker_status = "not found"
        try:
            res = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", cname],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0 and res.stdout.strip():
                docker_status = res.stdout.strip()
        except Exception:
            pass

        # Check API status
        api_status = "unreachable"
        active_tunnels = 0
        try:
            req = urllib.request.Request(f"http://localhost:{port}/api/agent/status")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    api_status = "healthy"
                    active_tunnels = data.get("active_tunnels", 0)
        except Exception:
            pass

        nodes.append({
            "index": idx,
            "id_str": str(idx),
            "dir": d,
            "compose_file": compose_file,
            "env_file": env_file,
            "container_name": cname,
            "name": name,
            "port": port,
            "role": role,
            "panel_address": panel_addr,
            "docker_status": docker_status,
            "api_status": api_status,
            "active_tunnels": active_tunnels,
        })

    return nodes


def select_target_node(nodes, target_arg=None, prompt_action="operate on"):
    """Select a specific node by index or prompt the user interactively."""
    if not nodes:
        print("Error: No Smite Node installations found on this server.")
        print("Install a node using: sudo bash -c \"$(curl -sL https://raw.githubusercontent.com/MasterALiReza/Smite/main/scripts/smite-node.sh)\"")
        sys.exit(1)

    if target_arg:
        target_arg_str = str(target_arg).strip().lower()
        if target_arg_str in ["all", "*"]:
            return nodes
        for node in nodes:
            if node["id_str"] == target_arg_str or node["container_name"].lower() == target_arg_str or node["name"].lower() == target_arg_str:
                return [node]
        print(f"Error: Node '{target_arg}' not found.")
        sys.exit(1)

    if len(nodes) == 1:
        return [nodes[0]]

    print(f"\nMultiple Smite Nodes detected on this server:")
    for n in nodes:
        print(f"  [{n['index']}] {n['container_name']} (Port: {n['port']}, Role: {n['role']}, Status: {n['docker_status']})")
    
    print(f"  [A] All nodes")
    print(f"  [Q] Cancel")
    
    choice = input(f"\nSelect node to {prompt_action} [1-{len(nodes)}/A/Q] (default: 1): ").strip()
    if not choice:
        choice = "1"
    
    if choice.lower() == "q":
        print("Operation cancelled.")
        sys.exit(0)
    elif choice.lower() == "a":
        return nodes
    
    for n in nodes:
        if str(n["index"]) == choice:
            return [n]

    print("Invalid selection.")
    sys.exit(1)


def run_compose_for_node(node, compose_args, capture_output=False):
    """Run a docker compose command inside the node's directory."""
    compose_file = node["compose_file"]
    if not compose_file.exists():
        print(f"Error: {compose_file} not found.")
        sys.exit(1)
        
    compose_dir = node["dir"]
    original_cwd = Path.cwd()
    try:
        os.chdir(compose_dir)
        cmd = ["docker", "compose", "-f", str(compose_file)] + compose_args
        res = subprocess.run(cmd, capture_output=capture_output, text=True, cwd=str(compose_dir))
        if not capture_output and res.returncode != 0:
            sys.exit(res.returncode)
        return res
    finally:
        os.chdir(original_cwd)


def cmd_status(args):
    """Show detailed status for all detected nodes."""
    nodes = discover_nodes()
    if not nodes:
        print("No Smite Node installations found on this server.")
        return

    print("=" * 82)
    print("                      Smite Node Multi-Instance Status                     ")
    print("=" * 82)
    print(f"{'Idx':<4} | {'Container':<14} | {'Port':<6} | {'Role':<8} | {'Docker':<10} | {'API Health':<12} | {'Tunnels':<7}")
    print("-" * 82)
    
    for n in nodes:
        dock = n["docker_status"]
        if dock == "running":
            dock_display = "Running"
        elif dock == "exited":
            dock_display = "Stopped"
        else:
            dock_display = dock[:10]

        api_disp = n["api_status"]
        tunnels_disp = str(n["active_tunnels"]) if n["api_status"] == "healthy" else "-"

        print(f"[{n['index']}]  | {n['container_name']:<14} | {n['port']:<6} | {n['role']:<8} | {dock_display:<10} | {api_disp:<12} | {tunnels_disp:<7}")

    print("=" * 82)
    print(f"Total instances: {len(nodes)}")
    print("Run `smite-node logs [N]` or `smite-node restart [N]` to manage a specific node.")


def cmd_restart(args):
    """Restart node container(s)."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="restart")
    unfinished = [n["container_name"] for n in targets if (n["dir"] / UPDATE_JOURNAL).exists()]
    if unfinished:
        sys.exit(f"Unfinished update journal on {', '.join(unfinished)}; recover it before restarting")
    print("Note: a container restart interrupts the tunnels of the node for a few seconds.")
    print("      To apply new agent code without interruption use: smite-node reload")
    for n in targets:
        print(f"\nRestarting {n['container_name']} (Port {n['port']})...")
        run_compose_for_node(n, ["stop", n["container_name"]])
        run_compose_for_node(n, ["rm", "-f", n["container_name"]])
        res = run_compose_for_node(n, ["up", "-d", "--no-deps", "--no-pull", n["container_name"]], capture_output=True)
        if res.returncode != 0 and "--no-pull" in (res.stderr or ""):
            run_compose_for_node(n, ["up", "-d", "--no-deps", n["container_name"]])
        print(f"✓ {n['container_name']} restarted.")


DEFAULT_REPO = os.environ.get("SMITE_REPO", "MasterALiReza/Smite")
DEFAULT_BRANCH = os.environ.get("SMITE_BRANCH", "main")

# Every Python file of the node agent. They are bind-mounted into the container,
# so updating them on disk + an in-place reload updates the agent without
# restarting the container (tunnel processes keep running).
NODE_CODE_FILES = [
    "main.py",
    "app/__init__.py",
    "app/config.py",
    "app/agent_auth.py",
    "app/hot_reload.py",
    "app/core_adapters.py",
    "app/frp_comm_client.py",
    "app/panel_client.py",
    "app/routers/__init__.py",
    "app/routers/agent.py",
]


LOW_LATENCY_SYSCTL_FILE = Path("/etc/sysctl.d/99-smite-lowlatency.conf")
LOW_LATENCY_SYSCTL = """# Smite low-latency network profile (managed by smite-node; safe to apply at runtime)
# BBR + fq pacing: low queueing delay on lossy long-distance links (persisted across reboots)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
# Keep the unsent part of TCP send buffers small: interactive packets (games, VoIP)
# do not queue behind megabytes of bulk data inside the same tunnel connection
net.ipv4.tcp_notsent_lowat = 16384
# Long-lived tunnel connections keep their congestion window after idle periods
net.ipv4.tcp_slow_start_after_idle = 0
# Recover from path-MTU black holes instead of stalling (common on tunnelled paths)
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_fastopen = 3
# UDP: no drops on short bursts
net.ipv4.udp_rmem_min = 16384
net.ipv4.udp_wmem_min = 16384
net.core.rmem_default = 262144
net.core.wmem_default = 262144
net.core.netdev_budget = 600
"""


def apply_low_latency_profile(verbose=True):
    """Install and apply the kernel network profile. Never interrupts existing connections:
    a new congestion control / qdisc default only applies to new connections."""
    if os.name != "posix" or os.geteuid() != 0:
        if verbose:
            print("⚠️ Low-latency kernel profile needs root on Linux; skipped")
        return False
    try:
        subprocess.run(["modprobe", "tcp_bbr"], capture_output=True, check=False)
        modules_conf = Path("/etc/modules-load.d/smite.conf")
        try:
            existing = modules_conf.read_text() if modules_conf.exists() else ""
            if "tcp_bbr" not in existing:
                modules_conf.parent.mkdir(parents=True, exist_ok=True)
                modules_conf.write_text(existing + ("" if existing.endswith("\n") or not existing else "\n") + "tcp_bbr\n")
        except Exception:
            pass
        LOW_LATENCY_SYSCTL_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOW_LATENCY_SYSCTL_FILE.write_text(LOW_LATENCY_SYSCTL)
        res = subprocess.run(["sysctl", "-e", "-p", str(LOW_LATENCY_SYSCTL_FILE)], capture_output=True, text=True, check=False)
        cc = subprocess.run(["sysctl", "-n", "net.ipv4.tcp_congestion_control"], capture_output=True, text=True, check=False).stdout.strip()
        if verbose:
            print(f"✓ Low-latency network profile applied (congestion control: {cc or 'unknown'})")
            if res.returncode != 0 and res.stderr.strip():
                print(f"  note: {res.stderr.strip().splitlines()[-1]}")
        return True
    except Exception as e:
        if verbose:
            print(f"⚠️ Could not apply low-latency profile: {e}")
        return False


def cmd_tune(args):
    """Apply the low-latency kernel network profile on this host."""
    apply_low_latency_profile(verbose=True)


def _fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "smite-node-cli", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read()


def _resolve_revision(repo, ref):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo):
        raise ValueError("Repository must be owner/name")
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        return ref.lower()
    encoded_ref = urllib.parse.quote(ref, safe="")
    revision = json.loads(_fetch(f"https://api.github.com/repos/{repo}/commits/{encoded_ref}")).get("sha")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Could not resolve an immutable commit")
    return revision


def _download_node_code(repo, revision):
    import ast
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A full commit SHA is required for downloads")
    files = {}
    for rel in NODE_CODE_FILES:
        content = _fetch(f"https://raw.githubusercontent.com/{repo}/{revision}/node/{rel}")
        ast.parse(content, filename=rel)
        files[rel] = content
    return files


def _release_manifest(revision, files):
    return {
        "schema": 1,
        "revision": revision,
        "files": {rel: hashlib.sha256(content).hexdigest() for rel, content in sorted(files.items())},
    }


def _validate_runtime(n, files):
    script = '''import base64, json, os, pathlib, subprocess, sys, tempfile
files = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix="smite-preflight-") as directory:
    root = pathlib.Path(directory)
    for name, content in files.items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            sys.exit(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(content, validate=True))
    env = dict(os.environ, SMITE_RELOAD_VALIDATION="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import main; "
         "assert {'release_identity', 'drained_reexec'} <= set(main.agent.AGENT_CAPABILITIES)", directory],
        cwd=directory, env=env, capture_output=True, timeout=60)
    sys.exit(result.returncode)
'''
    payload = json.dumps({rel: base64.b64encode(content).decode("ascii") for rel, content in files.items()})
    result = subprocess.run(
        ["docker", "exec", "-i", n["container_name"], "python", "-c", script],
        input=payload, capture_output=True, text=True, timeout=75,
    )
    if result.returncode != 0:
        raise RuntimeError("Staged agent import failed in the installed runtime; active files were not changed")


def _agent_status(port, timeout=2.5):
    try:
        req = urllib.request.Request(f"http://localhost:{port}/api/agent/status")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        return None
    return None


def _wait_for_agent(port, previous_started_at=None, timeout=90, expected_release=None):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = _agent_status(port)
        if st and st.get("status") == "ok" and st.get("agent_started_at"):
            restarted = previous_started_at is None or st["agent_started_at"] != previous_started_at
            release_matches = expected_release is None or st.get("release") == expected_release
            if restarted and release_matches:
                return st
        time.sleep(1.5)
    return None


UPDATE_JOURNAL = ".smite-update.json"
RELEASE_MANIFEST = "app/.smite-release.json"


def _code_path(root, rel):
    if rel not in {*NODE_CODE_FILES, RELEASE_MANIFEST}:
        raise ValueError(f"Unexpected agent file: {rel}")
    target = root / rel
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Agent file escapes installation: {rel}")
    return target


def _write_file(target, content, in_place=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    if in_place and target.exists():
        # main.py is a single-file bind mount; replacing its inode is not visible inside Docker.
        with target.open("r+b") as stream:
            stream.write(content)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _write_code_with_backup(app_root, files):
    for rel in files:
        _code_path(app_root, rel)
    journal = app_root / UPDATE_JOURNAL
    with journal.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"phase": "preparing"}))
        stream.flush()
        os.fsync(stream.fileno())
    backup_dir = Path(tempfile.mkdtemp(prefix=".backup-", dir=app_root))
    previous = {}
    try:
        for rel in files:
            current = _code_path(app_root, rel)
            previous[rel] = None
            if current.exists():
                content = current.read_bytes()
                previous[rel] = hashlib.sha256(content).hexdigest()
                _write_file(backup_dir / rel, content)
                shutil.copymode(current, backup_dir / rel)
        _write_file(backup_dir / "rollback.json", json.dumps(previous, sort_keys=True).encode())
        record = {"phase": "writing", "backup": backup_dir.name}
        _write_file(journal, json.dumps(record).encode())
    except Exception:
        journal.unlink()
        raise
    try:
        # Entry point last: a crash mid-write never pairs a new main.py with old modules.
        order = sorted(files, key=lambda rel: (rel == "main.py", rel == RELEASE_MANIFEST))
        for rel in order:
            _write_file(_code_path(app_root, rel), files[rel], in_place=rel == "main.py")
        record["phase"] = "awaiting_verification"
        _write_file(journal, json.dumps(record).encode())
    except BaseException:
        _restore_backup(app_root, backup_dir)
        raise
    return backup_dir


def _restore_backup(app_root, backup_dir):
    previous = json.loads((backup_dir / "rollback.json").read_text(encoding="utf-8"))
    contents = {}
    for rel, digest in previous.items():
        _code_path(app_root, rel)
        if digest is not None:
            content = _code_path(backup_dir, rel).read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise RuntimeError(f"Backup checksum mismatch: {rel}")
            contents[rel] = content
    for rel, digest in previous.items():
        target = _code_path(app_root, rel)
        if digest is None:
            target.unlink(missing_ok=True)
        else:
            _write_file(target, contents[rel], in_place=rel == "main.py")
            shutil.copymode(backup_dir / rel, target)
    (app_root / UPDATE_JOURNAL).unlink(missing_ok=True)


def _confirm(prompt, assume_yes):
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _reload_is_safe(status):
    if not (status and status.get("hot_reload")):
        return False
    return {"safe_reexec", "drained_reexec"}.issubset(status.get("capabilities") or [])


def _restart_and_wait(n, timeout=120, expected_release=None):
    before = _agent_status(n["port"])
    res = subprocess.run(["docker", "restart", n["container_name"]], capture_output=True, text=True)
    if res.returncode != 0:
        print("Container restart failed")
        return None
    return _wait_for_agent(n["port"], previous_started_at=(before or {}).get("agent_started_at"),
                           timeout=timeout, expected_release=expected_release)


def _hot_reload(n, expected_release=None):
    before = _agent_status(n["port"])
    if not before or not before.get("agent_started_at"):
        return False
    res = subprocess.run(["docker", "kill", "--signal=HUP", n["container_name"]], capture_output=True, text=True)
    if res.returncode != 0:
        print("Could not signal the agent")
        return False
    st = _wait_for_agent(n["port"], previous_started_at=before["agent_started_at"],
                         timeout=90, expected_release=expected_release)
    return bool(st)


def _self_update_cli(content):
    try:
        target = Path("/usr/local/bin/smite-node")
        if target.exists():
            _write_file(target, content)
            print("smite-node CLI updated to the same revision")
    except Exception as error:
        print(f"Node release verified, but the local CLI was not updated: {error}")


def cmd_update(args):
    """Update one agent without changing its image, cores, or host tuning."""
    import ast
    targets = select_target_node(discover_nodes(), args.target, prompt_action="update")
    if len(targets) != 1:
        sys.exit("Update one node at a time and verify its tunnels before selecting another")
    if args.image:
        sys.exit("Image/core upgrades require a separate coordinated rollout; agent update does not recreate images")
    n = targets[0]
    app_root = n["dir"]
    if not (app_root / "app").is_dir():
        sys.exit("Node code directory not found; nothing changed")
    if (app_root / UPDATE_JOURNAL).exists():
        sys.exit("An unfinished update exists; inspect the update journal and recovery backup before proceeding")
    status = _agent_status(n["port"])
    if not status or status.get("status") != "ok" or not status.get("agent_started_at"):
        sys.exit("Cannot verify the running agent; nothing changed")
    can_hot_reload = _reload_is_safe(status)
    if not can_hot_reload:
        if not getattr(args, "allow_restart", False):
            sys.exit("Safe reload is unavailable. A planned interruption requires --allow-restart; nothing changed")
        if not _confirm("Restart this node container and interrupt its tunnels?", args.yes):
            sys.exit("Update cancelled; active files were not changed")
    repo = args.repo or DEFAULT_REPO
    ref = getattr(args, "revision", None) or args.branch or DEFAULT_BRANCH
    backup_dir = None
    try:
        revision = _resolve_revision(repo, ref)
        print(f"Preparing agent release {repo}@{revision}")
        files = _download_node_code(repo, revision)
        cli_content = _fetch(f"https://raw.githubusercontent.com/{repo}/{revision}/cli/smite-node.py")
        ast.parse(cli_content, filename="smite-node.py")
        _validate_runtime(n, files)
        current = _agent_status(n["port"])
        if not current or current.get("agent_started_at") != status["agent_started_at"]:
            raise RuntimeError("Agent changed during preflight; nothing changed")
        manifest = json.dumps(_release_manifest(revision, files), sort_keys=True).encode()
        files[RELEASE_MANIFEST] = manifest
        expected_release = {"revision": revision, "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
        backup_dir = _write_code_with_backup(app_root, files)
        print(f"Code backup: {backup_dir}")
        if can_hot_reload:
            verified = _hot_reload(n, expected_release=expected_release)
        else:
            verified = _restart_and_wait(n, expected_release=expected_release)
        if not verified:
            raise RuntimeError("New agent release was not verified; no automatic restart or further update will run")
        after = _agent_status(n["port"])
        if not after or after.get("release") != expected_release or set(after.get("tunnels", [])) != set(status.get("tunnels", [])):
            raise RuntimeError("Agent release or tunnel inventory changed unexpectedly; stop the rollout")
        (app_root / UPDATE_JOURNAL).unlink()
        _self_update_cli(cli_content)
    except Exception as error:
        print(f"Update failed: {error}")
        if backup_dir:
            print(f"Backup retained at {backup_dir}; verify runtime before any recovery. No fallback restart was attempted.")
        sys.exit(1)
    print("Agent release and tunnel IDs verified; image, dependencies and host tuning unchanged.")
    print("Verify existing streams, new connections and peer traffic before updating another node.")


def cmd_recover_update(args):
    targets = select_target_node(discover_nodes(), args.target, prompt_action="recover code on")
    if len(targets) != 1:
        sys.exit("Recover one node at a time")
    n = targets[0]
    result = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", n["container_name"]],
                            capture_output=True, text=True)
    if result.returncode != 0 or result.stdout.strip() not in {"exited", "created"}:
        sys.exit("Code recovery requires a confirmed stopped container; no service was stopped")
    try:
        record = json.loads((n["dir"] / UPDATE_JOURNAL).read_text(encoding="utf-8"))
        name = record.get("backup", "")
        if not re.fullmatch(r"[.]backup-[A-Za-z0-9_-]+", name):
            raise ValueError("No complete recovery backup recorded; inspect manually")
        backup = n["dir"] / name
        if backup.is_symlink():
            raise ValueError("Recovery backup must not be a symlink")
        _restore_backup(n["dir"], backup)
    except Exception as error:
        sys.exit(f"Code recovery failed: {error}")
    print("Previous code restored, including removal of newly added files. Container remains stopped.")


def cmd_reload(args):
    """Reload agent code in place without touching tunnels."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="reload")
    for n in targets:
        if (n["dir"] / UPDATE_JOURNAL).exists():
            sys.exit(f"{n['container_name']}: an unfinished update journal exists; finish or recover it before reloading")
        st = _agent_status(n["port"])
        if not _reload_is_safe(st):
            sys.exit(f"{n['container_name']}: running agent lacks safe drain/re-exec; plan an explicit upgrade interruption")
        print(f"Reloading {n['container_name']}...")
        if not _hot_reload(n):
            sys.exit("Agent reload was not verified; no fallback start/restart was attempted. Check logs before proceeding")
        print("Agent reload verified; verify tunnel traffic separately")


def cmd_reset_token(args):
    """Forget the panel-provisioned agent API token of a node so the panel can re-pair it."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="reset the agent token of")
    for n in targets:
        res = subprocess.run(
            ["docker", "exec", n["container_name"], "sh", "-c",
             "rm -f /var/lib/smite-node/agent_token /var/lib/smite-node/agent_auth_enforce"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            print(f"✗ {n['container_name']}: {res.stderr.strip()}")
            continue
        st = _agent_status(n["port"])
        if st and st.get("hot_reload"):
            _hot_reload(n)
        print(f"✓ {n['container_name']}: token and enforce flag removed; the panel will provision a new one within ~2 minutes")


def cmd_logs(args):
    """Stream or view container logs."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="view logs for")
    
    if len(targets) > 1:
        print("Streaming logs for all containers...")
        # Follow all
        follow = ["--follow"] if args.follow else []
        for n in targets:
            print(f"--- Logs for {n['container_name']} ---")
            run_compose_for_node(n, ["logs"] + follow + [n["container_name"]])
    else:
        n = targets[0]
        follow = ["--follow"] if args.follow else []
        run_compose_for_node(n, ["logs"] + follow + [n["container_name"]])


def cmd_edit(args):
    """Edit docker-compose.yml of target node."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="edit docker-compose.yml for")
    n = targets[0]
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(n["compose_file"])])


def cmd_edit_env(args):
    """Edit .env of target node."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="edit .env for")
    n = targets[0]
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(n["env_file"])])


def cmd_uninstall(args):
    """Safely uninstall a node instance with backup."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="UNINSTALL")
    
    print("=" * 60)
    print("⚠️  WARNING: You are about to uninstall the following instance(s):")
    for n in targets:
        print(f"  - {n['container_name']} ({n['dir']})")
    print("=" * 60)
    
    confirm = input("Type 'yes' to confirm uninstall: ").strip()
    if confirm.lower() != "yes":
        print("Uninstall cancelled.")
        return

    for n in targets:
        print(f"\nUninstalling {n['container_name']}...")
        
        # 1. Stop and remove containers
        try:
            run_compose_for_node(n, ["down", "-v"], capture_output=True)
            subprocess.run(["docker", "stop", n["container_name"]], capture_output=True, check=False)
            subprocess.run(["docker", "rm", "-f", n["container_name"]], capture_output=True, check=False)
            print("  ✓ Container removed")
        except Exception as e:
            print(f"  ⚠️ Container remove error: {e}")

        # 2. Remove volume
        vol_name = f"{n['container_name']}-data" if n['container_name'] != "smite-node" else "smite-node-data"
        try:
            subprocess.run(["docker", "volume", "rm", "-f", vol_name], capture_output=True, check=False)
            print(f"  ✓ Volume {vol_name} removed")
        except Exception:
            pass

        # 3. Remove directory
        if n["dir"].exists():
            try:
                shutil.rmtree(n["dir"])
                print(f"  ✓ Directory {n['dir']} removed")
            except Exception as e:
                print(f"  ⚠️ Directory remove error: {e}")

    remaining = [n for n in discover_nodes() if n not in targets]
    if not remaining:
        # If no nodes remain, clean up CLI script
        cli_path = Path("/usr/local/bin/smite-node")
        if cli_path.exists():
            try:
                cli_path.unlink()
                print("  ✓ Removed /usr/local/bin/smite-node (no nodes remaining)")
            except Exception:
                pass

    print("\n✅ Uninstall completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Smite Node CLI - Multi-Instance Management")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Status
    subparsers.add_parser("status", help="Show status of all node instances")
    
    # Restart
    restart_p = subparsers.add_parser("restart", help="Restart node instance")
    restart_p.add_argument("target", nargs="?", default=None, help="Target node index or name (e.g. 1, 2, all)")
    
    # Update
    update_p = subparsers.add_parser("update", help="Update one node agent; retain image, cores and host tuning")
    update_p.add_argument("target", nargs="?", default=None, help="Target node index or name")
    release_source = update_p.add_mutually_exclusive_group()
    release_source.add_argument("--branch", default=None, help=f"Resolve a branch once to a commit (default: {DEFAULT_BRANCH})")
    release_source.add_argument("--revision", default=None, help="Immutable release commit SHA")
    update_p.add_argument("--repo", default=None, help=f"GitHub repo owner/name (default: {DEFAULT_REPO}, env SMITE_REPO)")
    update_p.add_argument("--image", action="store_true", help="Rejected: image/core upgrades must be performed separately")
    update_p.add_argument("--allow-restart", action="store_true", help="Authorize a container restart only when safe reload is unavailable")
    update_p.add_argument("-y", "--yes", action="store_true", help="Confirm an explicitly authorized restart")
    update_p.add_argument("--no-tune", action="store_true", help="Host tuning is always excluded from agent updates")

    recover_p = subparsers.add_parser("recover-update", help="Restore an interrupted code update on an already stopped container")
    recover_p.add_argument("target", nargs="?", default=None, help="Target node index or name")

    # Tune
    subparsers.add_parser("tune", help="Apply the low-latency kernel network profile (BBR, fq, UDP buffers)")

    # Reload
    reload_p = subparsers.add_parser("reload", help="Reload agent code in place (tunnels keep running)")
    reload_p.add_argument("target", nargs="?", default=None, help="Target node index or name (e.g. 1, 2, all)")

    # Reset token
    reset_p = subparsers.add_parser("reset-token", help="Remove the panel-provisioned agent API token (re-pair with panel)")
    reset_p.add_argument("target", nargs="?", default=None, help="Target node index or name")
    
    # Logs
    logs_p = subparsers.add_parser("logs", help="View logs")
    logs_p.add_argument("target", nargs="?", default=None, help="Target node index or name (e.g. 1, 2)")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Follow log stream")
    
    # Edit
    edit_p = subparsers.add_parser("edit", help="Edit docker-compose.yml")
    edit_p.add_argument("target", nargs="?", default=None, help="Target node index or name")
    
    # Edit .env
    edit_env_p = subparsers.add_parser("edit-env", help="Edit .env file")
    edit_env_p.add_argument("target", nargs="?", default=None, help="Target node index or name")
    
    # Uninstall
    uninst_p = subparsers.add_parser("uninstall", help="Uninstall node instance")
    uninst_p.add_argument("target", nargs="?", default=None, help="Target node index or name")

    args = parser.parse_args()
    
    if not args.command:
        # Default to status if no command provided
        cmd_status(args)
        return

    commands = {
        "status": cmd_status,
        "restart": cmd_restart,
        "update": cmd_update,
        "recover-update": cmd_recover_update,
        "reload": cmd_reload,
        "reset-token": cmd_reset_token,
        "tune": cmd_tune,
        "logs": cmd_logs,
        "edit": cmd_edit,
        "edit-env": cmd_edit_env,
        "uninstall": cmd_uninstall,
    }
    
    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
