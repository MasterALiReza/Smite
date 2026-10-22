"""Zero-downtime in-place agent reload.

`docker kill --signal=HUP <node-container>` (used by `smite-node update`) makes the
agent re-exec itself with the code currently on disk. The PID stays the same, so
the container keeps running and every tunnel core process keeps forwarding
traffic; the new agent adopts them through their PID files.

Before re-exec the new code is imported in a child interpreter. If that fails the
reload is aborted and the running agent (old code) keeps serving.
"""
import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import Any, List

logger = logging.getLogger(__name__)

_reload_in_progress = False


def _build_exec_argv() -> List[str]:
    # sys.orig_argv is the interpreter's real command line ("python -m uvicorn ..."
    # or "python /usr/local/bin/uvicorn ..."), so re-exec reproduces it exactly.
    # sys.argv is not safe for this: after "python -m uvicorn" its argv[0] is
    # ".../site-packages/uvicorn/__main__.py", and running that file as a script
    # puts the uvicorn package dir first on sys.path, where uvicorn/logging.py
    # shadows the stdlib logging module and the new agent crashes on startup
    # (which, as container PID 1, stopped the container and all its tunnels).
    orig = list(getattr(sys, "orig_argv", None) or [])
    if len(orig) > 1:
        return [sys.executable, *orig[1:]]
    argv0 = sys.argv[0] if sys.argv else ""
    base = os.path.basename(argv0)
    if base == "__main__.py":
        return [sys.executable, "-m", os.path.basename(os.path.dirname(argv0)), *sys.argv[1:]]
    if base in ("uvicorn", "uvicorn.exe") or base.endswith("uvicorn"):
        return [sys.executable, "-m", "uvicorn", *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def _validate_new_code() -> bool:
    """Import the on-disk code in a separate interpreter; never reload into broken code."""
    try:
        res = subprocess.run(
            [sys.executable, "-c", "import main"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "SMITE_RELOAD_VALIDATION": "1"},
        )
    except Exception as e:
        logger.error(f"Hot reload aborted: validation could not run: {e}")
        return False
    if res.returncode != 0:
        logger.error(f"Hot reload aborted: new code failed to import:\n{(res.stderr or res.stdout)[-2000:]}")
        return False
    return True


async def _reload(app: Any) -> None:
    global _reload_in_progress
    if _reload_in_progress:
        return
    _reload_in_progress = True
    try:
        logger.warning("Hot reload requested (SIGHUP): validating new agent code...")
        ok = await asyncio.to_thread(_validate_new_code)
        if not ok:
            return

        state = getattr(app, "state", None)
        # Stop background loops; tunnel core processes are intentionally left running.
        task = getattr(state, "registration_task", None) if state else None
        if task:
            task.cancel()
        manager = getattr(state, "adapter_manager", None) if state else None
        if manager is not None:
            try:
                manager.stop_watchdog()
            except Exception:
                pass
            # Let in-flight apply/remove operations finish so no tunnel is left half-applied
            for _ in range(60):
                locks = list(getattr(manager, "_tunnel_locks", {}).values())
                if not any(lock.locked() for lock in locks):
                    break
                await asyncio.sleep(0.25)
        h2_client = getattr(state, "h2_client", None) if state else None
        if h2_client is not None:
            try:
                await asyncio.wait_for(h2_client.stop(), timeout=8)
            except Exception as e:
                logger.debug(f"Panel client stop during reload: {e}")

        argv = _build_exec_argv()
        logger.warning(f"Hot reload: re-executing agent in place ({' '.join(argv)}); tunnels keep running")
        for h in logging.getLogger().handlers:
            try:
                h.flush()
            except Exception:
                pass
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(argv[0], argv)
    except Exception as e:
        logger.error(f"Hot reload failed, continuing with current code: {e}", exc_info=True)
    finally:
        _reload_in_progress = False


def install_reload_handler(app: Any) -> bool:
    """Register SIGHUP -> in-place reload. Returns False where unsupported."""
    if os.name != "posix" or os.environ.get("SMITE_RELOAD_VALIDATION"):
        return False
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGHUP, lambda: asyncio.ensure_future(_reload(app)))
        logger.info("Hot reload handler installed (send SIGHUP to reload agent code without touching tunnels)")
        return True
    except Exception as e:
        logger.warning(f"Could not install hot reload handler: {e}")
        return False
