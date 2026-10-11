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
import urllib.request
import urllib.error
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


def _download_node_code(repo, branch):
    """Download and syntax-check every node file. All-or-nothing."""
    import ast
    files = {}
    for rel in NODE_CODE_FILES:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/node/{rel}"
        content = _fetch(url)
        ast.parse(content, filename=rel)
        files[rel] = content
    return files


def _agent_status(port, timeout=2.5):
    try:
        req = urllib.request.Request(f"http://localhost:{port}/api/agent/status")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode())
    except Exception:
        return None
    return None


def _wait_for_agent(port, previous_started_at=None, timeout=90):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _agent_status(port)
        if st and (previous_started_at is None or st.get("agent_started_at") != previous_started_at):
            return st
        time.sleep(1.5)
    return None


def _write_code_atomically(app_root, files):
    """Back up current files then atomically replace them."""
    import time
    backup_dir = app_root / f".backup-{time.strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for rel in files:
        cur = app_root / rel
        if cur.exists():
            dest = backup_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cur, dest)
    for rel, content in files.items():
        target = app_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if "/" not in rel and target.exists():
            # main.py is a single-file bind mount: the container keeps seeing the
            # old inode after a rename, so rewrite the existing file in place.
            with open(target, "r+b") as f:
                f.write(content)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            continue
        tmp = target.with_name(f".{target.name}.new")
        tmp.write_bytes(content)
        os.replace(tmp, target)
    return backup_dir


def _restore_backup(app_root, backup_dir):
    for src in backup_dir.rglob("*"):
        if src.is_file():
            rel = src.relative_to(backup_dir)
            dest = app_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def _confirm(prompt, assume_yes):
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _hot_reload(n):
    """Reload the agent in place (SIGHUP). Returns True when the new agent is up."""
    before = _agent_status(n["port"])
    started_at = (before or {}).get("agent_started_at")
    res = subprocess.run(["docker", "kill", "--signal=HUP", n["container_name"]], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  ⚠️ Could not signal container: {res.stderr.strip()}")
        return False
    st = _wait_for_agent(n["port"], previous_started_at=started_at, timeout=90)
    return bool(st)


def _self_update_cli(repo, branch):
    try:
        import ast
        content = _fetch(f"https://raw.githubusercontent.com/{repo}/{branch}/cli/smite-node.py")
        ast.parse(content)
        target = Path("/usr/local/bin/smite-node")
        if target.exists():
            tmp = target.with_name(".smite-node.new")
            tmp.write_bytes(content)
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
            print("✓ smite-node CLI updated")
    except Exception as e:
        print(f"⚠️ Could not update the CLI itself: {e}")


def cmd_update(args):
    """Update node agent code with zero tunnel downtime (in-place reload).

    Default: sync all agent code files from GitHub, then hot-reload the agent. The
    container and every tunnel process keep running. Nodes still running an old
    agent (without hot reload) need ONE container restart; you are asked first.
    --image additionally pulls the container image (core binaries) and recreates the
    container, which always interrupts tunnels briefly.
    """
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="update")
    repo = args.repo or DEFAULT_REPO
    branch = args.branch or DEFAULT_BRANCH

    print(f"Downloading node agent code from {repo}@{branch} ...")
    try:
        files = _download_node_code(repo, branch)
    except Exception as e:
        print(f"✗ Download/validation failed, nothing was changed: {e}")
        sys.exit(1)
    print(f"✓ {len(files)} files downloaded and syntax-checked")
    if not args.no_tune:
        apply_low_latency_profile(verbose=True)

    for n in targets:
        print(f"\nUpdating {n['container_name']} in {n['dir']}...")
        app_root = n["dir"]
        if not (app_root / "app").exists():
            print("  ✗ Node code directory not found, skipping")
            continue

        status = _agent_status(n["port"])
        can_hot_reload = bool(status and status.get("hot_reload"))

        backup_dir = _write_code_atomically(app_root, files)
        print(f"  ✓ Code updated (backup: {backup_dir})")

        if args.image:
            if not _confirm("  --image recreates the container: tunnels on this node drop for a few seconds. Continue?", args.yes):
                print("  Skipped image update.")
            else:
                run_compose_for_node(n, ["pull"], capture_output=True)
                run_compose_for_node(n, ["up", "-d", "--force-recreate"])
                st = _wait_for_agent(n["port"], timeout=120)
                print(f"  {'✓' if st else '⚠️'} Container recreated{'' if st else ' but agent is not answering yet'}")
                continue

        if can_hot_reload:
            print("  Reloading agent in place (tunnels keep running)...")
            if _hot_reload(n):
                print(f"  ✓ {n['container_name']} updated with zero tunnel downtime")
                continue
            print("  ✗ Agent did not come back after reload. Rolling back code...")
            _restore_backup(app_root, backup_dir)
            subprocess.run(["docker", "kill", "--signal=HUP", n["container_name"]], capture_output=True)
            print("  Rolled back. Check logs: smite-node logs")
            continue

        print("  This node runs an older agent without in-place reload.")
        print("  A ONE-TIME container restart is needed to load the new agent; tunnels on")
        print("  this node are interrupted for a few seconds while they are restored.")
        print("  All later updates are applied without any interruption.")
        if not _confirm("  Restart this node's agent container now?", args.yes):
            print("  Code is updated on disk; it becomes active on the next container restart.")
            continue
        res = subprocess.run(["docker", "restart", n["container_name"]], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  ✗ docker restart failed: {res.stderr.strip()}")
            continue
        st = _wait_for_agent(n["port"], timeout=120)
        if st:
            print(f"  ✓ {n['container_name']} restarted with the new agent ({st.get('active_tunnels', 0)} tunnels active)")
        else:
            print("  ⚠️ Agent is not answering yet; check: smite-node logs")

    _self_update_cli(repo, branch)


def cmd_reload(args):
    """Reload agent code in place without touching tunnels."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="reload")
    for n in targets:
        st = _agent_status(n["port"])
        if not (st and st.get("hot_reload")):
            print(f"✗ {n['container_name']}: agent does not support in-place reload (run `smite-node update` first)")
            continue
        print(f"Reloading {n['container_name']}...")
        print("✓ reloaded" if _hot_reload(n) else "⚠️ agent did not come back in time; check logs")


def cmd_reset_token(args):
    """Forget the panel-provisioned agent API token of a node so the panel can re-pair it."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="reset the agent token of")
    for n in targets:
        res = subprocess.run(
            ["docker", "exec", n["container_name"], "rm", "-f", "/var/lib/smite-node/agent_token"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            print(f"✗ {n['container_name']}: {res.stderr.strip()}")
            continue
        st = _agent_status(n["port"])
        if st and st.get("hot_reload"):
            _hot_reload(n)
        print(f"✓ {n['container_name']}: token removed; the panel will provision a new one within ~2 minutes")


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
    update_p = subparsers.add_parser("update", help="Update node agent code (zero tunnel downtime)")
    update_p.add_argument("target", nargs="?", default=None, help="Target node index or name (e.g. 1, 2, all)")
    update_p.add_argument("--branch", default=None, help=f"Git branch to update from (default: {DEFAULT_BRANCH}, env SMITE_BRANCH)")
    update_p.add_argument("--repo", default=None, help=f"GitHub repo owner/name (default: {DEFAULT_REPO}, env SMITE_REPO)")
    update_p.add_argument("--image", action="store_true", help="Also pull the container image and recreate the container (interrupts tunnels)")
    update_p.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation")
    update_p.add_argument("--no-tune", action="store_true", help="Do not (re)apply the low-latency kernel network profile")

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
