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
            
            if tunnel_type == "tcp":
                cmd = ["/usr/local/bin/gost", f"-L=tcp://{listen_addr}/{target_addr}"]
            elif tunnel_type == "udp":
                cmd = ["/usr/local/bin/gost", f"-L=udp://{listen_addr}/{target_addr}"]
            elif tunnel_type == "ws":
                import socket
                try:
                    if use_ipv6:
                        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                        s.connect(("2001:4860:4860::8888", 80))
                        bind_ip = s.getsockname()[0]
                    else:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.connect(("8.8.8.8", 80))
                        bind_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    bind_ip = "[::]" if use_ipv6 else "0.0.0.0"
                cmd = ["/usr/local/bin/gost", f"-L=ws://{bind_ip}:{local_port}/tcp://{target_addr}"]
            elif tunnel_type == "grpc":
                cmd = ["/usr/local/bin/gost", f"-L=grpc://{listen_addr}/{target_addr}"]
            elif tunnel_type == "tcpmux":
                cmd = ["/usr/local/bin/gost", f"-L=tcpmux://{listen_addr}/{target_addr}"]
            else:
                raise ValueError(f"Unsupported tunnel type: {tunnel_type}")
            
            gost_binary = "/usr/local/bin/gost"
            if not os.path.exists(gost_binary):
                gost_binary = shutil.which("gost")
                if not gost_binary:
                    raise RuntimeError("gost binary not found at /usr/local/bin/gost or in PATH")
            elif not os.access(gost_binary, os.X_OK):
                raise RuntimeError(f"gost binary at {gost_binary} is not executable")
            
            cmd[0] = gost_binary
            logger.info(f"Starting gost: {' '.join(cmd)}")
            
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
    
    async def cleanup_all(self):
        """Stop all forwarding"""
        tunnel_ids = list(self.active_forwards.keys())
        for tunnel_id in tunnel_ids:
            await self.stop_forward(tunnel_id)


gost_forwarder = GostForwarder()
