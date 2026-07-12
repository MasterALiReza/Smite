"""Rathole server management for panel"""
import asyncio
import logging
import shutil
import os
from pathlib import Path
from typing import Dict, Optional

from app.utils import parse_address_port, format_address_port
from app.process_manager import start_async_process, stop_async_process, wait_for_port, read_log_tail

logger = logging.getLogger(__name__)


class RatholeServerManager:
    """Manages Rathole server processes on the panel"""
    
    def __init__(self):
        self.config_dir = Path("/app/data/rathole")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.active_servers: Dict[str, asyncio.subprocess.Process] = {}
        self.server_configs: Dict[str, dict] = {}
        self.log_files: Dict[str, object] = {}
    
    async def start_server(self, tunnel_id: str, remote_addr: str, token: str, proxy_port: int, use_ipv6: bool = False) -> bool:
        """
        Start a Rathole server for a tunnel
        """
        try:
            _, port, _ = parse_address_port(remote_addr)
            if port is None:
                raise ValueError(f"Invalid remote_addr format: {remote_addr} (port required)")
            
            bind_addr = f"0.0.0.0:{port}"
            proxy_bind_addr = f"0.0.0.0:{proxy_port}"
            
            if tunnel_id in self.active_servers:
                logger.warning(f"Rathole server for tunnel {tunnel_id} already exists, stopping it first")
                await self.stop_server(tunnel_id)
                await asyncio.sleep(0.5)
            
            config = f"""[server]
bind_addr = "{bind_addr}"
default_token = "{token}"

[server.services.{tunnel_id}]
bind_addr = "{proxy_bind_addr}"
"""
            
            config_path = self.config_dir / f"{tunnel_id}.toml"
            
            def write_config():
                with open(config_path, "w") as f:
                    f.write(config)
            
            await asyncio.to_thread(write_config)
            
            self.server_configs[tunnel_id] = {
                "remote_addr": remote_addr,
                "token": token,
                "proxy_port": proxy_port,
                "bind_addr": bind_addr,
                "config_path": str(config_path)
            }
            
            log_file = self.config_dir / f"rathole_{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            
            rathole_binary = "/usr/local/bin/rathole"
            if not os.path.exists(rathole_binary):
                rathole_binary = shutil.which("rathole")
                if not rathole_binary:
                    raise RuntimeError("rathole binary not found at /usr/local/bin/rathole or in PATH")
            
            cmd = [rathole_binary, "-s", str(config_path)]
            
            log_f.write(f"Starting rathole server for tunnel {tunnel_id}\n")
            log_f.write(f"Config: bind_addr={bind_addr}, proxy_port={proxy_port}\n")
            log_f.write(f"Config file: {config_path}\n")
            log_f.write(f"Config content:\n{config}\n")
            log_f.flush()
            
            proc = await start_async_process(cmd, str(self.config_dir), log_f)
            
            self.log_files[tunnel_id] = log_f
            self.active_servers[tunnel_id] = proc
            
            await asyncio.sleep(1.0)
            if proc.returncode is not None:
                stderr = await read_log_tail(log_file)
                error_msg = f"rathole server failed to start (exit code: {proc.returncode}): {stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            port_listening = await wait_for_port(port)
            if not port_listening:
                if proc.returncode is not None:
                    stderr = await read_log_tail(log_file)
                    error_msg = f"rathole server process exited (code: {proc.returncode}) before port verification: {stderr}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                else:
                    logger.warning(f"Rathole server port {port} not listening after verification, but process is running. PID: {proc.pid}")
            else:
                logger.info(f"Rathole server port {port} verified as listening")
            
            logger.info(f"Started Rathole server for tunnel {tunnel_id} on {bind_addr}, proxy port: {proxy_port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Rathole server for tunnel {tunnel_id}: {e}")
            if tunnel_id in self.active_servers:
                await self.stop_server(tunnel_id)
            raise
    
    async def stop_server(self, tunnel_id: str):
        """Stop Rathole server for a tunnel"""
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
            
            logger.info(f"Stopped Rathole server for tunnel {tunnel_id}")
        
        if tunnel_id in self.server_configs:
            config_path = Path(self.server_configs[tunnel_id]["config_path"])
            if config_path.exists():
                try:
                    config_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete config file {config_path}: {e}")
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
    
    async def cleanup_all(self):
        """Stop all Rathole servers"""
        tunnel_ids = list(self.active_servers.keys())
        for tunnel_id in tunnel_ids:
            await self.stop_server(tunnel_id)


rathole_server_manager = RatholeServerManager()
