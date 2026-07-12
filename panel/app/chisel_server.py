"""Chisel server management for panel"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

from app.utils import parse_address_port, format_address_port
from app.process_manager import start_async_process, stop_async_process, wait_for_port, read_log_tail

logger = logging.getLogger(__name__)


class ChiselServerManager:
    """Manages Chisel server processes on the panel"""
    
    def __init__(self):
        self.config_dir = Path("/app/data/chisel")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.active_servers: Dict[str, asyncio.subprocess.Process] = {}
        self.server_configs: Dict[str, dict] = {}
        self.log_files: Dict[str, object] = {}
    
    async def start_server(self, tunnel_id: str, server_port: int, auth: Optional[str] = None, fingerprint: Optional[str] = None, use_ipv6: bool = False) -> bool:
        """
        Start a Chisel server for a tunnel
        """
        try:
            if tunnel_id in self.active_servers:
                logger.warning(f"Chisel server for tunnel {tunnel_id} already exists, stopping it first")
                await self.stop_server(tunnel_id)
                await asyncio.sleep(0.5)
            
            host = "0.0.0.0"
            
            cmd = [
                "/usr/local/bin/chisel",
                "server",
                "--host", host,
                "--port", str(server_port),
                "--reverse"
            ]
            
            if auth:
                cmd.extend(["--auth", auth])
            
            if fingerprint:
                cmd.extend(["--fingerprint", fingerprint])
            
            chisel_binary = "/usr/local/bin/chisel"
            import os
            import shutil
            if not os.path.exists(chisel_binary):
                chisel_binary = shutil.which("chisel")
                if not chisel_binary:
                    raise RuntimeError("chisel binary not found at /usr/local/bin/chisel or in PATH")
            
            cmd[0] = chisel_binary
            
            self.server_configs[tunnel_id] = {
                "server_port": server_port,
                "auth": auth,
                "fingerprint": fingerprint,
                "use_ipv6": use_ipv6
            }
            
            log_file = self.config_dir / f"chisel_{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            log_f.write(f"Starting chisel server for tunnel {tunnel_id}\n")
            log_f.write(f"Config: server_port={server_port}, auth={auth is not None}, fingerprint={fingerprint is not None}\n")
            log_f.write(f"Command: {' '.join(cmd)}\n")
            log_f.flush()
            
            proc = await start_async_process(cmd, str(self.config_dir), log_f)
            
            self.log_files[tunnel_id] = log_f
            self.active_servers[tunnel_id] = proc
            
            await asyncio.sleep(1.0)
            if proc.returncode is not None:
                stderr = await read_log_tail(log_file)
                error_msg = f"chisel server failed to start (exit code: {proc.returncode}): {stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            port_listening = await wait_for_port(server_port)
            if not port_listening:
                if proc.returncode is not None:
                    stderr = await read_log_tail(log_file)
                    error_msg = f"Chisel server process exited (code: {proc.returncode}) before port verification: {stderr}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                else:
                    logger.warning(f"Chisel server port {server_port} not listening after verification, but process is running. PID: {proc.pid}")
            else:
                logger.info(f"Chisel server port {server_port} verified as listening")
            
            logger.info(f"Started Chisel server for tunnel {tunnel_id} on port {server_port} (PID: {proc.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Chisel server for tunnel {tunnel_id}: {e}")
            if tunnel_id in self.active_servers:
                await self.stop_server(tunnel_id)
            raise
    
    async def stop_server(self, tunnel_id: str):
        """Stop Chisel server for a tunnel"""
        if tunnel_id in self.active_servers:
            proc = self.active_servers[tunnel_id]
            await stop_async_process(proc)
            del self.active_servers[tunnel_id]
            
            if tunnel_id in self.log_files:
                try:
                    self.log_files[tunnel_id].close()
                except Exception:
                    pass
                del self.log_files[tunnel_id]
            
            logger.info(f"Stopped Chisel server for tunnel {tunnel_id}")
        
        if tunnel_id in self.server_configs:
            del self.server_configs[tunnel_id]
    
    async def is_running(self, tunnel_id: str) -> bool:
        """Check if server is running for a tunnel"""
        if tunnel_id not in self.active_servers:
            return False
        proc = self.active_servers[tunnel_id]
        return proc.returncode is None
    
    def get_active_servers(self) -> list:
        """Get list of tunnel IDs with active servers"""
        active = []
        for tunnel_id, proc in list(self.active_servers.items()):
            if proc.returncode is None:
                active.append(tunnel_id)
            else:
                del self.active_servers[tunnel_id]
                if tunnel_id in self.server_configs:
                    del self.server_configs[tunnel_id]
                if tunnel_id in self.log_files:
                    try:
                        self.log_files[tunnel_id].close()
                    except Exception:
                        pass
                    del self.log_files[tunnel_id]
        return active


chisel_server_manager = ChiselServerManager()
