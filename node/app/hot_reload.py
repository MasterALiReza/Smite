"""Validate new agent code, drain tracked operations, and re-exec in place."""
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
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
        manager = getattr(state, "adapter_manager", None) if state else None
        if manager is not None:
            deadline = time.monotonic() + 15
            while True:
                if getattr(manager, "_shutting_down", False):
                    logger.error("Hot reload aborted: agent shutdown is already in progress")
                    return
                locks = list(getattr(manager, "_tunnel_locks", {}).values())
                io_lock = getattr(manager, "_io_lock", None)
                if io_lock is not None:
                    locks.append(io_lock)
                workers = getattr(manager, "_pending_workers", ())
                if not any(lock.locked() for lock in locks) and not any(not task.done() for task in workers):
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.error("Hot reload aborted: adapter operations still busy after 15 seconds")
                    return
                await asyncio.sleep(min(0.25, remaining))

        # No awaits after the drain check: event-loop mutations must not resume before exec.
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
