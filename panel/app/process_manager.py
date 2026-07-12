"""Asynchronous Process Management for Tunnels"""
import asyncio
import logging
import os
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

async def start_async_process(cmd: list, cwd: str, log_f) -> asyncio.subprocess.Process:
    """Start a process asynchronously in a new session group (if posix)."""
    kwargs = {
        "stdout": log_f,
        "stderr": asyncio.subprocess.STDOUT,
        "cwd": cwd,
    }
    if os.name == 'posix':
        kwargs["start_new_session"] = True
        
    proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
    return proc

async def stop_async_process(proc: asyncio.subprocess.Process, timeout: float = 5.0) -> None:
    """Stop a process asynchronously and safely."""
    if proc.returncode is not None:
        return
        
    if os.name == 'posix':
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"Failed to killpg process {proc.pid}: {e}")
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
    else:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        if os.name == 'posix':
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        
        await proc.wait()

async def wait_for_port(port: int, max_retries: int = 6, delay: float = 0.5) -> bool:
    """Non-blocking wait to check if a local port is listening on either IPv4 or IPv6."""
    hosts = ['127.0.0.1', '::1']
    for _ in range(max_retries):
        await asyncio.sleep(delay)
        for host in hosts:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), 
                    timeout=0.5
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                continue
    return False

async def read_log_tail(log_file: Path, max_chars: int = 500) -> str:
    """Asynchronously read the tail of a log file."""
    if not log_file.exists():
        return "Log file not found"
        
    def _read_tail():
        try:
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            if len(content) > max_chars:
                return content[-max_chars:]
            return content
        except Exception as e:
            return f"Error reading log: {e}"
            
    return await asyncio.to_thread(_read_tail)
