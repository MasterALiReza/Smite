"""Node agent API authentication.

The agent API (/api/agent/*) can be protected by a shared secret sent by the
panel in the X-Node-Token header. The secret comes from one of two sources:

1. NODE_API_TOKEN in the node .env (static, manually configured). Takes precedence.
2. A per-node token provisioned automatically by the panel through
   POST /api/agent/auth/bootstrap and persisted in the node data volume, so it
   survives container restarts and re-creation.

When neither is configured the API stays open for backward compatibility with
older panels; the panel provisions a token as soon as it sees the node.
"""
import hmac
import logging
import os
import re
import threading
from pathlib import Path
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(os.environ.get("SMITE_AGENT_TOKEN_FILE", "/var/lib/smite-node/agent_token"))
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{32,128}$")

_lock = threading.Lock()
_file_token: Optional[str] = None
_loaded = False


def _load_file_token() -> Optional[str]:
    try:
        if TOKEN_FILE.exists():
            value = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if value and _TOKEN_RE.match(value):
                return value
            if value:
                logger.warning(f"Ignoring malformed agent token file {TOKEN_FILE}")
    except Exception as e:
        logger.warning(f"Could not read agent token file {TOKEN_FILE}: {e}")
    return None


def get_file_token() -> Optional[str]:
    global _file_token, _loaded
    with _lock:
        if not _loaded:
            _file_token = _load_file_token()
            _loaded = True
        return _file_token


def get_env_token() -> str:
    return (settings.node_api_token or "").strip()


def get_effective_token() -> str:
    """Token the agent currently enforces ('' when the API is open)."""
    return get_env_token() or (get_file_token() or "")


def token_source() -> str:
    if get_env_token():
        return "env"
    if get_file_token():
        return "panel"
    return "none"


def is_valid_token_format(value: str) -> bool:
    return bool(value) and bool(_TOKEN_RE.match(value))


def tokens_match(provided: Optional[str], expected: Optional[str]) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def set_file_token(value: str) -> None:
    """Atomically persist a panel-provisioned token (0600)."""
    global _file_token, _loaded
    if not is_valid_token_format(value):
        raise ValueError("Invalid token format")
    with _lock:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = TOKEN_FILE.with_name(f".{TOKEN_FILE.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                tmp.unlink()
            except Exception:
                pass
            raise
        os.replace(tmp, TOKEN_FILE)
        _file_token = value
        _loaded = True


def clear_file_token() -> None:
    global _file_token, _loaded
    with _lock:
        try:
            if TOKEN_FILE.exists():
                TOKEN_FILE.unlink()
        except Exception as e:
            logger.warning(f"Could not remove agent token file: {e}")
        _file_token = None
        _loaded = True


def is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.") or host == "::ffff:127.0.0.1"
