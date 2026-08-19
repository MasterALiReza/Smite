"""Gost-based forwarding service for stable TCP/UDP/WS/gRPC tunnels"""
import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Optional

from app.utils import parse_address_port, format_address_port
from app.process_manager import start_async_process, stop_async_process, wait_for_port, read_log_tail

logger = logging.getLogger(__name__)


class GostForwarder:
    """Manages TCP/UDP/WS/gRPC forwarding using gost"""
    
    def __init__(self):
        self.config_dir = Path("/app/data/gost")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.active_forwards: Dict[str, asyncio.subprocess.Process] = {}
        self.forward_configs: Dict[str, dict] = {}
        self.log_files: Dict[str, object] = {}
    
    async def start_forward(self, tunnel_id: str, local_port: int, forward_to: str, tunnel_type: str = "tcp", path: str = None, use_ipv6: bool = False) -> bool:
        """
        Start forwarding using gost - forwards directly to target (no node)
        """
        try:
            if tunnel_id in self.active_forwards:
                logger.warning(f"Forward for tunnel {tunnel_id} already exists, stopping it first")
                await self.stop_forward(tunnel_id)
                await asyncio.sleep(0.5)
            
            forward_host, forward_port, forward_is_ipv6 = parse_address_port(forward_to)
            if forward_port is None:
                forward_port = 8080
            
            target_addr = format_address_port(forward_host, forward_port)
            
            if use_ipv6:
                listen_addr = f"[::]:{local_port}"
            else:
                listen_addr = f"0.0.0.0:{local_port}"
            
            import json
            services = []
            if tunnel_type == "tcp+udp":
                for proto in ["tcp", "udp"]:
                    services.append({
                        "name": f"forward-{tunnel_id}-{proto}",
                        "addr": listen_addr,
                        "handler": {"type": proto, "metadata": {"keepAlive": True}},
                        "listener": {"type": proto, "metadata": {"keepAlive": True, "keepAliveInterval": "25s"}},
                        "forwarder": {
                            "nodes": [
                                {"name": f"target-{tunnel_id}-{proto}", "addr": target_addr}
                            ]
                        }
                    })
            else:
                listener_type = tunnel_type if tunnel_type in ["tcp", "udp", "ws", "grpc", "tcpmux"] else "tcp"
                handler_type = "udp" if tunnel_type == "udp" else "tcp"
                
                listener_metadata = {"keepAlive": True, "keepAliveInterval": "25s"}
                if path and tunnel_type == "ws":
                    listener_metadata["path"] = path

                listener_obj = {"type": listener_type, "metadata": listener_metadata}

                services.append({
                    "name": f"forward-{tunnel_id}",
                    "addr": listen_addr,
                    "handler": {"type": handler_type, "metadata": {"keepAlive": True}},
                    "listener": listener_obj,
                    "forwarder": {
                        "nodes": [
                            {"name": f"target-{tunnel_id}", "addr": target_addr}
                        ]
                    }
                })
            
            config = {
                "services": services
            }
            
            config_file = self.config_dir / f"gost_{tunnel_id}.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
            try:
                os.chmod(config_file, 0o600)
            except Exception:
                pass
            
            gost_binary = "/usr/local/bin/gost"
            if not os.path.exists(gost_binary):
                gost_binary = shutil.which("gost")
                if not gost_binary:
                    raise RuntimeError("gost binary not found at /usr/local/bin/gost or in PATH")
            elif not os.access(gost_binary, os.X_OK):
                raise RuntimeError(f"gost binary at {gost_binary} is not executable")
            
            cmd = [gost_binary, "-C", str(config_file)]
            logger.info(f"Starting gost v3: {' '.join(cmd)}")
            
            log_file = self.config_dir / f"gost_{tunnel_id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_f = open(log_file, 'w', buffering=1)
            log_f.write(f"Starting gost with command: {' '.join(cmd)}\n")
            log_f.write(f"Tunnel ID: {tunnel_id}\n")
            log_f.write(f"Local port: {local_port}, Forward to: {forward_to}\n")
            log_f.flush()
            
            proc = await start_async_process(cmd, str(self.config_dir), log_f)
            
            log_f.write(f"Process started with PID: {proc.pid}\n")
            log_f.flush()
            
            self.log_files[tunnel_id] = log_f
            self.active_forwards[tunnel_id] = proc
            logger.info(f"Started gost process for tunnel {tunnel_id}, PID={proc.pid}")
            
            await asyncio.sleep(1.5)
            if proc.returncode is not None:
                stderr = await read_log_tail(log_file)
                error_msg = f"gost failed to start (exit code: {proc.returncode}): {stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            if tunnel_type != "udp":
                await asyncio.sleep(0.5)
                if proc.returncode is not None:
                    stderr = await read_log_tail(log_file)
                    error_msg = f"gost process died after startup (exit code: {proc.returncode}): {stderr}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                
                if tunnel_type != "ws":
                    port_listening = await wait_for_port(local_port)
                    if proc.returncode is not None:
                        stderr = await read_log_tail(log_file)
                        error_msg = f"gost process died during port check (exit code: {proc.returncode}): {stderr}"
                        logger.error(error_msg)
                        raise RuntimeError(error_msg)
                    elif not port_listening:
                        logger.warning(f"Port {local_port} not listening after gost start, but process is running. PID: {proc.pid}")
                else:
                    logger.info(f"WS tunnel on port {local_port}: skipping port verification (WebSocket requires handshake)")
            else:
                await asyncio.sleep(0.5)
                if proc.returncode is not None:
                    stderr = await read_log_tail(log_file)
                    error_msg = f"gost UDP process died after startup (exit code: {proc.returncode}): {stderr}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
            
            self.forward_configs[tunnel_id] = {
                "local_port": local_port,
                "forward_to": forward_to,
                "tunnel_type": tunnel_type
            }
            
            logger.info(f"Started gost forwarding for tunnel {tunnel_id}: {tunnel_type}://:{local_port} -> {forward_to}, PID={proc.pid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start gost forwarding for tunnel {tunnel_id}: {e}")
            raise
    
    async def stop_forward(self, tunnel_id: str):
        """Stop forwarding for a tunnel"""
        if tunnel_id in self.active_forwards:
            proc = self.active_forwards[tunnel_id]
            await stop_async_process(proc)
            del self.active_forwards[tunnel_id]
            logger.info(f"Stopped gost forwarding for tunnel {tunnel_id}")
            
        if tunnel_id in self.log_files:
            try:
                self.log_files[tunnel_id].close()
            except Exception:
                pass
            del self.log_files[tunnel_id]
        
        config_file = self.config_dir / f"gost_{tunnel_id}.json"
        if config_file.exists():
            try:
                config_file.unlink()
            except Exception:
                pass

        log_file = self.config_dir / f"gost_{tunnel_id}.log"
        if log_file.exists():
            try:
                log_file.unlink()
            except Exception:
                pass

        if tunnel_id in self.forward_configs:
            del self.forward_configs[tunnel_id]
    
    async def is_forwarding(self, tunnel_id: str) -> bool:
        """Check if forwarding is active for a tunnel"""
        if tunnel_id not in self.active_forwards:
            return False
        proc = self.active_forwards[tunnel_id]
        is_alive = proc.returncode is None
        if not is_alive and tunnel_id in self.forward_configs:
            logger.warning(f"Gost process for tunnel {tunnel_id} died, attempting restart...")
            try:
                config = self.forward_configs[tunnel_id]
                await self.start_forward(
                    tunnel_id=tunnel_id,
                    local_port=config["local_port"],
                    forward_to=config["forward_to"],
                    tunnel_type=config["tunnel_type"]
                )
                return True
            except Exception as e:
                logger.error(f"Failed to restart gost for tunnel {tunnel_id}: {e}")
                return False
        return is_alive
    
    def get_forwarding_tunnels(self) -> list:
        """Get list of tunnel IDs with active forwarding"""
        active = []
        for tunnel_id, proc in list(self.active_forwards.items()):
            if proc.returncode is None:
                active.append(tunnel_id)
            else:
                del self.active_forwards[tunnel_id]
                if tunnel_id in self.forward_configs:
                    del self.forward_configs[tunnel_id]
                if tunnel_id in self.log_files:
                    try:
                        self.log_files[tunnel_id].close()
                    except Exception:
                        pass
                    del self.log_files[tunnel_id]
        return active
    
    async def _health_monitor_loop(self):
        """Background health monitor checking active forwards and auto-restarting dead processes every 30s"""
        logger.info("GostForwarder health monitor loop started (interval: 30s)")
        while True:
            try:
                await asyncio.sleep(30)
                for tunnel_id in list(self.forward_configs.keys()):
                    try:
                        await self.is_forwarding(tunnel_id)
                    except Exception as e:
                        logger.error(f"Error in GostForwarder health check for tunnel {tunnel_id}: {e}")
            except asyncio.CancelledError:
                logger.info("GostForwarder health monitor loop cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in GostForwarder health monitor loop: {e}", exc_info=True)

    def start_monitor(self):
        """Start the background health monitor task"""
        if not hasattr(self, '_monitor_task') or self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._health_monitor_loop())
            logger.info("GostForwarder health monitor task created")

    def stop_monitor(self):
        """Stop the background health monitor task"""
        if hasattr(self, '_monitor_task') and self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()

    async def cleanup_all(self):
        """Stop all forwarding and health monitor"""
        self.stop_monitor()
        tunnel_ids = list(self.active_forwards.keys())
        for tunnel_id in tunnel_ids:
            await self.stop_forward(tunnel_id)


gost_forwarder = GostForwarder()
