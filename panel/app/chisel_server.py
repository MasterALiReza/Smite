"""Chisel server management for panel"""
import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

from app.utils import parse_address_port, format_address_port, sanitize_cmd_for_log, is_safe_backend_url
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
    
    async def start_server(
        self,
        tunnel_id: str,
        server_port: int,
        auth: Optional[str] = None,
        fingerprint: Optional[str] = None,
        use_ipv6: bool = False,
        tls_cert_pem: Optional[str] = None,
        tls_key_pem: Optional[str] = None,
        backend_url: Optional[str] = None,
        socks5: bool = False,
        keepalive: Optional[str] = None
    ) -> bool:
        """
        Start a Chisel server for a tunnel with WSS, decoy backend camouflage, and persistent SSH host key
        """
        try:
            if tunnel_id in self.active_servers:
                logger.warning(f"Chisel server for tunnel {tunnel_id} already exists, stopping it first")
                await self.stop_server(tunnel_id)
                await asyncio.sleep(0.5)
            
            host = "::" if use_ipv6 else "0.0.0.0"
            
            chisel_binary = "/usr/local/bin/chisel"
            if not os.path.exists(chisel_binary):
                chisel_binary = shutil.which("chisel")
                if not chisel_binary:
                    raise RuntimeError("chisel binary not found at /usr/local/bin/chisel or in PATH")
            
            cmd = [
                chisel_binary,
                "server",
                "--host", host,
                "--port", str(server_port),
                "--reverse"
            ]
            
            if auth:
                cmd.extend(["--auth", auth])
            
            # Persistent SSH host keyfile so clients can reliably pin --fingerprint without MITM risks
            keyfile_path = self.config_dir / f"{tunnel_id}_ssh.key"
            if not keyfile_path.exists():
                try:
                    keygen_proc = await asyncio.create_subprocess_exec(
                        chisel_binary, "server", "--keygen", str(keyfile_path),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    await keygen_proc.wait()
                    os.chmod(keyfile_path, 0o600)
                except Exception as e:
                    logger.warning(f"Could not auto-generate persistent SSH key for chisel server {tunnel_id}: {e}")
            if keyfile_path.exists():
                cmd.extend(["--keyfile", str(keyfile_path)])

            # WSS / TLS Certificate configuration
            cert_path = self.config_dir / f"{tunnel_id}_cert.pem"
            key_path = self.config_dir / f"{tunnel_id}_key.pem"
            if tls_cert_pem and tls_key_pem:
                with open(cert_path, "w") as cf:
                    cf.write(tls_cert_pem)
                with open(key_path, "w") as kf:
                    kf.write(tls_key_pem)
                try:
                    os.chmod(key_path, 0o600)
                except Exception:
                    pass
            elif not cert_path.exists() and not key_path.exists() and (tls_cert_pem or tls_key_pem):
                try:
                    ssl_proc = await asyncio.create_subprocess_exec(
                        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key_path), "-out", str(cert_path), "-days", "3650",
                        "-subj", "/CN=chisel-tunnel",
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    await ssl_proc.wait()
                    os.chmod(key_path, 0o600)
                except Exception as e:
                    logger.warning(f"Could not auto-generate self-signed cert for chisel server: {e}")

            if cert_path.exists() and key_path.exists():
                cmd.extend(["--tls-cert", str(cert_path), "--tls-key", str(key_path)])

            # Active Probing Defense / Camouflage (SSRF Safe)
            if backend_url and is_safe_backend_url(backend_url):
                cmd.extend(["--backend", str(backend_url).strip()])

            # SOCKS5 dynamic proxy support
            if socks5:
                cmd.append("--socks5")

            # Stability / Keepalive tuning
            if keepalive:
                cmd.extend(["--keepalive", str(keepalive)])
            
            self.server_configs[tunnel_id] = {
                "server_port": server_port,
                "auth": auth,
                "use_ipv6": use_ipv6,
                "backend_url": backend_url,
                "socks5": socks5,
                "keepalive": keepalive
            }
            
            log_file = self.config_dir / f"chisel_{tunnel_id}.log"
            log_f = open(log_file, 'w', buffering=1)
            try:
                log_f.write(f"Starting chisel server for tunnel {tunnel_id}\n")
                log_f.write(f"Config: server_port={server_port}, auth={auth is not None}, wss={cert_path.exists()}\n")
                log_f.write(f"Command: {sanitize_cmd_for_log(cmd)}\n")
                log_f.flush()
                
                proc = await start_async_process(cmd, str(self.config_dir), log_f)
            except Exception:
                log_f.close()
                raise
            
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
    
    async def stop_server(self, tunnel_id: str, purge: bool = False):
        """Stop Chisel server for a tunnel and cleanup key/cert files (preserves host key unless purge=True)"""
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

        # Cleanup temporary keys & certificates; preserve _ssh.key for fingerprint continuity unless purge=True
        cleanup_suffixes = ["_cert.pem", "_key.pem"]
        if purge:
            cleanup_suffixes.append("_ssh.key")
        for suffix in cleanup_suffixes:
            fpath = self.config_dir / f"{tunnel_id}{suffix}"
            if fpath.exists():
                try:
                    fpath.unlink()
                except Exception:
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


chisel_server_manager = ChiselServerManager()
