"""FRP communication client for node-panel communication"""
import asyncio
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Dict, Optional
try:
    from .config import settings
except ImportError:
    from app.config import settings

logger = logging.getLogger(__name__)


class FrpCommClient:
    """Manages FRP client for node-panel communication"""
    
    def __init__(self):
        self.config_dir = Path("/etc/smite-node/frp_comm")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.process: Optional[asyncio.subprocess.Process] = None
        self.config_file = self.config_dir / "frpc_comm.yaml"
        self.log_file = self.config_dir / "frpc_comm.log"
        self.pid_file = self.config_dir / "frpc_comm.pid"
        self.enabled = False
        self.server_addr: Optional[str] = None
        self.server_port: Optional[int] = None
        self.token: Optional[str] = None
        self.local_port = settings.node_api_port
        self.remote_port: Optional[int] = None

    def _cleanup_stale_processes(self):
        """Clean up previous orphan frpc_comm processes and stale pidfiles."""
        old_pid = None
        if self.pid_file.exists():
            try:
                content = self.pid_file.read_text().strip()
                if content.isdigit():
                    old_pid = int(content)
            except Exception:
                pass

        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmd = " ".join(p.info['cmdline'] or [])
                    if (old_pid and p.info['pid'] == old_pid) or ("frpc_comm" in cmd and "frpc" in (p.info['name'] or "")):
                        logger.info(f"Terminating stale frpc_comm process (PID {p.info['pid']})")
                        p.terminate()
                        try:
                            p.wait(timeout=2.0)
                        except Exception:
                            p.kill()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Process cleanup check: {e}")

        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
        except Exception:
            pass
    
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
        
        import shutil
        resolved = shutil.which("frpc")
        if resolved:
            return Path(resolved)
        
        raise FileNotFoundError(
            "frpc binary not found. Expected at FRPC_BINARY, '/usr/local/bin/frpc', or in PATH."
        )
    
    async def start(self, server_addr: str, server_port: int, token: Optional[str] = None, node_id: Optional[str] = None) -> bool:
        """Start FRP client for node-panel communication"""
        if self.process and self.process.returncode is None:
            logger.warning("FRP communication client already running")
            return True

        self._cleanup_stale_processes()
        
        try:
            self.server_addr = server_addr
            self.server_port = server_port
            self.token = token
            self.enabled = True
            
            if node_id:
                import hashlib
                port_hash = int(hashlib.md5(node_id.encode()).hexdigest()[:8], 16)
                self.remote_port = 10000 + (port_hash % 10000)
            else:
                self.remote_port = None
            
            config_content = f"""serverAddr: {server_addr}
serverPort: {server_port}
loginFailExit: false
transport:
  heartbeatInterval: 10
  heartbeatTimeout: 30
  dialServerTimeout: 15
  tcpMux: true
"""
            if token:
                config_content += f"""auth:
  method: token
  token: "{token}"
"""
            
            # FRP proxy names should be alphanumeric and reasonably short
            if node_id:
                clean_id = node_id.replace("-", "")[:12]
                proxy_name = f"node_{clean_id}"
            else:
                if self.remote_port:
                    proxy_name = f"node_{self.remote_port}"
                else:
                    proxy_name = "node_api"
            
            config_content += f"""
proxies:
  - name: {proxy_name}
    type: tcp
    localIP: 127.0.0.1
    localPort: {self.local_port}
"""
            if self.remote_port:
                config_content += f"    remotePort: {self.remote_port}\n"
            
            with open(self.config_file, 'w') as f:
                f.write(config_content)
            
            binary_path = self._resolve_binary_path()
            cmd = [str(binary_path), "-c", str(self.config_file)]
            
            log_f = open(self.log_file, 'w', buffering=1)
            log_f.write(f"Starting FRP communication client\n")
            log_f.write(f"Server: {server_addr}:{server_port}\n")
            log_f.write(f"Local: 127.0.0.1:{self.local_port}\n")
            if self.remote_port:
                log_f.write(f"Remote port: {self.remote_port}\n")
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
                error_msg = f"FRP communication client failed to start: {error_output[-500:] if len(error_output) > 500 else error_output}"
                logger.error(error_msg)
                self.enabled = False
                raise RuntimeError(error_msg)
            
            logger.info(f"[FRP] FRP communication client started (PID: {self.process.pid}, remote_port={self.remote_port})")
            try:
                self.pid_file.write_text(str(self.process.pid))
            except Exception:
                pass
            
            if self.remote_port:
                await asyncio.sleep(1.0)
                if self.log_file.exists():
                    with open(self.log_file, 'r') as f:
                        log_content = f.read()
                        if 'remotePort' in log_content or 'start proxy success' in log_content.lower():
                            logger.info(f"[FRP] FRP client connected successfully, remote port: {self.remote_port}")
                            logger.info(f"[FRP] Node API is now accessible to panel via FRP tunnel at 127.0.0.1:{self.remote_port}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start FRP communication client: {e}")
            self.enabled = False
            raise
    
    async def stop(self):
        """Stop FRP communication client"""
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            except Exception as e:
                logger.warning(f"Error stopping FRP communication client: {e}")
            finally:
                self.process = None
                self.enabled = False
                try:
                    if self.pid_file.exists():
                        self.pid_file.unlink()
                except Exception:
                    pass
                logger.info("[FRP] FRP communication client stopped")
    
    def is_running(self) -> bool:
        """Check if client is running"""
        if not self.process:
            return False
        return self.process.returncode is None
    
    def get_config(self) -> Dict[str, any]:
        """Get current configuration"""
        return {
            "enabled": self.enabled,
            "server_addr": self.server_addr,
            "server_port": self.server_port,
            "token": self.token,
            "local_port": self.local_port,
            "remote_port": self.remote_port,
            "running": self.is_running()
        }


frp_comm_client = FrpCommClient()

