"""FRP communication manager for panel-node communication"""
import os
import subprocess
import time
import logging
import shutil
from pathlib import Path
from typing import Dict, Optional
import asyncio

logger = logging.getLogger(__name__)


class FrpCommManager:
    """Manages FRP server for panel-node communication"""
    
    def __init__(self):
        self.config_dir = Path("/app/data/frp_comm")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.process: Optional[asyncio.subprocess.Process] = None
        self.config_file = self.config_dir / "frps_comm.yaml"
        self.log_file = self.config_dir / "frps_comm.log"
        self.enabled = False
        self.port = 7000
        self.token: Optional[str] = None
    
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
        
        resolved = shutil.which("frps")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "frps binary not found. Expected at FRPS_BINARY, '/usr/local/bin/frps', or in PATH."
        )
    
    async def start(self, port: int, token: Optional[str] = None) -> bool:
        """Start FRP server for panel-node communication"""
        if self.process and self.process.returncode is None:
            if self.port == port and self.token == token:
                logger.debug("FRP communication server already running with the requested configuration")
                return True
            logger.info("FRP communication server configuration changed; restarting")
            await self.stop()
        
        try:
            self.port = port
            self.token = token
            
            # Node agent APIs are exposed through this server on remote ports
            # 10000-19999 (see node frp_comm_client). They are only meant for the
            # panel itself, so bind them to loopback and refuse any other port:
            # previously every node API was reachable on the panel's public IP.
            config_content = f"""bindPort: {int(port)}
kcpBindPort: 0
quicBindPort: 0
proxyBindAddr: "127.0.0.1"
allowPorts:
  - start: 10000
    end: 19999
"""
            if token:
                safe_token = str(token).replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"').replace(chr(13), '').replace(chr(10), '')
                config_content += f"""auth:
  method: token
  token: "{safe_token}"
"""
            
            with open(self.config_file, 'w') as f:
                f.write(config_content)
            
            try:
                binary_path = self._resolve_binary_path()
            except FileNotFoundError as e:
                logger.warning(f"FRP binary not found: {e}. FRP communication will not be available.")
                self.enabled = False
                return False
            
            cmd = [str(binary_path), "-c", str(self.config_file)]
            
            log_f = open(self.log_file, 'w', buffering=1)
            log_f.write(f"Starting FRP communication server on port {port}\n")
            log_f.write(f"Config: bind_port={port}, token={'set' if token else 'none'}\n")
            log_f.write(f"Command: {' '.join(cmd)}\n")
            log_f.flush()
            
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_f,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.config_dir),
                start_new_session=True
            )
            
            await asyncio.sleep(1.0)
            if self.process.returncode is not None:
                if self.log_file.exists():
                    with open(self.log_file, 'r') as f:
                        error_output = f.read()
                else:
                    error_output = "Log file not found"
                error_msg = f"FRP communication server failed to start: {error_output[-500:] if len(error_output) > 500 else error_output}"
                logger.error(error_msg)
                self.enabled = False
                return False
            
            self.enabled = True
            logger.info(f"[FRP] FRP communication server started on port {port} (PID: {self.process.pid})")
            logger.info(f"[FRP] Panel is now ready to accept FRP connections from nodes")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start FRP communication server: {e}")
            self.enabled = False
            return False
    
    async def stop(self):
        """Stop FRP communication server"""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.warning(f"Error stopping FRP communication server: {e}")
            finally:
                self.process = None
                self.enabled = False
                logger.info("[FRP] FRP communication server stopped")
    
    def is_running(self) -> bool:
        """Check if server is running"""
        if not self.process:
            return False
        return self.process.returncode is None
    
    def get_config(self) -> Dict[str, any]:
        """Get current configuration"""
        return {
            "enabled": self.enabled,
            "port": self.port,
            "token": self.token,
            "running": self.is_running()
        }


frp_comm_manager = FrpCommManager()


