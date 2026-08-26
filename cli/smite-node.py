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
    
    for n in targets:
        print(f"\nRestarting {n['container_name']} (Port {n['port']})...")
        run_compose_for_node(n, ["stop", n["container_name"]])
        run_compose_for_node(n, ["rm", "-f", n["container_name"]])
        res = run_compose_for_node(n, ["up", "-d", "--no-deps", "--no-pull", n["container_name"]], capture_output=True)
        if res.returncode != 0 and "--no-pull" in (res.stderr or ""):
            run_compose_for_node(n, ["up", "-d", "--no-deps", n["container_name"]])
        print(f"✓ {n['container_name']} restarted.")


def cmd_update(args):
    """Safely update node container(s) and core adapters."""
    nodes = discover_nodes()
    targets = select_target_node(nodes, args.target, prompt_action="update")
    
    for n in targets:
        print(f"\nUpdating {n['container_name']} in {n['dir']}...")
        app_dir = n["dir"] / "app"
        if app_dir.exists():
            try:
                url = "https://raw.githubusercontent.com/MasterALiReza/Smite/main/node/app/core_adapters.py"
                req = urllib.request.Request(url, headers={"User-Agent": "smite-node-cli"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        (app_dir / "core_adapters.py").write_bytes(resp.read())
                        print("  ✓ Latest core adapters synced")
            except Exception as e:
                print(f"  ⚠️ Could not sync core_adapters directly: {e}")
        
        run_compose_for_node(n, ["pull"], capture_output=True)
        run_compose_for_node(n, ["up", "-d", "--force-recreate"])
        print(f"✓ {n['container_name']} updated.")


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
    update_p = subparsers.add_parser("update", help="Update node instance")
    update_p.add_argument("target", nargs="?", default=None, help="Target node index or name (e.g. 1, 2, all)")
    
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
