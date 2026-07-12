"""FRP server management for panel"""
import os
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Dict, Optional

from app.process_manager import start_async_process, stop_async_process, wait_for_port, read_log_tail

logger = logging.getLogger(__name__)


class FrpServerManager:
    """Manages FRP server (frps) processes on the panel"""
    
    def __init__(self):
        self.config_dir = Path("/app/data/frp")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.active_servers: Dict[str, asyncio.subprocess.Process] = {}
        self.server_configs: Dict[str, dict] = {}
        self.log_files: Dict[str, object] = {}
    
    def _resolve_binary_path(self) -> Path:
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
        
        which_path = shutil.which("frps")
        if which_path:
            return Path(which_path)
        
        raise FileNotFoundError(
            "frps binary not found. Expected at FRPS_BINARY, '/usr/local/bin/frps', or in PATH."
        )
    
    async def start_server(self, tunnel_id: str, bind_port: int, token: Optional[str] = None) -> bool:
        """
        Start an FRP server for a tunnel
        """
        try:
            if tunnel_id in self.active_servers:
                logger.warning(f"FRP server for tunnel {tunnel_id} already exists, stopping it first")
                await self.stop_server(tunnel_id)
                await asyncio.sleep(0.5)
            
            config_file = self.config_dir / f"frps_{tunnel_id}.yaml"
            config_content = f"bindPort: {bind_port}\n"
            if token:
                config_content += f"auth:\n  method: token\n  token: \"{token}\"\n"
                
            def write_config():
                with open(config_file, 'w') as f:
                    f.write(config_content)
            
            await asyncio.to_thread(write_config)
            
            logger.info(f"FRP server config file {config_file} content:\n{config_content}")
            
            binary_path = self._resolve_binary_path()
            cmd = [
                str(binary_path),
                "-c", str(config_file)
            ]
            
            self.server_configs[tunnel_id] = {
                "bind_port": bind_port,
                "token": token,
                "config_file": str(config_file)
            }
            
            log_file = self.config_dir / f"frps_{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            
            log_f.write(f"Starting FRP server for tunnel {tunnel_id}\n")
            log_f.write(f"Config: bind_port={bind_port}, token={'set' if token else 'none'}\n")
            log_f.write(f"Command: {' '.join(cmd)}\n")
            log_f.flush()
            
            proc = await start_async_process(cmd, str(self.config_dir), log_f)
            
            self.log_files[tunnel_id] = log_f
            self.active_servers[tunnel_id] = proc
            
            await asyncio.sleep(1.0)
            if proc.returncode is not None:
                stderr = await read_log_tail(log_file)
                error_msg = f"FRP server failed to start (exit code: {proc.returncode}): {stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            
            port_listening = await wait_for_port(bind_port)
            if not port_listening:
                if proc.returncode is not None:
                    stderr = await read_log_tail(log_file)
                    error_msg = f"FRP server process exited (code: {proc.returncode}) before port verification: {stderr}"
                    logger.error(error_msg)
                    raise RuntimeError(error_msg)
                else:
                    logger.warning(f"FRP server port {bind_port} not listening after verification, but process is running. PID: {proc.pid}")
            else:
                logger.info(f"FRP server port {bind_port} verified as listening")
            
            logger.info(f"Started FRP server for tunnel {tunnel_id} on port {bind_port} (PID: {proc.pid})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start FRP server for tunnel {tunnel_id}: {e}")
            if tunnel_id in self.active_servers:
                await self.stop_server(tunnel_id)
            raise
    
    async def stop_server(self, tunnel_id: str):
        """Stop FRP server for a tunnel"""
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
            
            logger.info(f"Stopped FRP server for tunnel {tunnel_id}")
        
        if tunnel_id in self.server_configs:
            config_file = Path(self.server_configs[tunnel_id].get("config_file", ""))
            if config_file.exists():
                try:
                    config_file.unlink()
                except:
                    pass
            del self.server_configs[tunnel_id]
        
        old_toml_config = self.config_dir / f"frps_{tunnel_id}.toml"
        if old_toml_config.exists():
            try:
                old_toml_config.unlink()
            except:
                pass
    
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
        """Stop all FRP servers"""
        tunnel_ids = list(self.active_servers.keys())
        for tunnel_id in tunnel_ids:
            await self.stop_server(tunnel_id)


frp_server_manager = FrpServerManager()
