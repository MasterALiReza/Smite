"""Fail-closed guard that suspends automatic fleet mutations.

A panel restart (image update, database restore, migration) replays many
background mutations: tunnel reapply/self-healing, node tunnel sync, agent token
provisioning, core resets, CA generation and SSL renewal. On a live fleet a
stale database or an untested release must not be allowed to rewrite node state
before an operator has inspected it.

The guard lives in a small JSON file *outside* the SQLite database (default
``./certs/.mutation-guard.json``) so restoring an old database snapshot cannot
rewind the guard itself. It expires loudly instead of auto-releasing: an expired
guard still blocks writes until a human releases it.

Rules:

* missing file           -> not engaged (fresh install / normal operation)
* valid file, engaged    -> engaged
* unreadable/corrupt/schema mismatch -> engaged (fail-closed)
* expired ``expires_at`` -> still engaged; reported as ``expired``
* ``SMITE_DISABLE_MUTATIONS=1`` -> engaged regardless of the file

Release is explicit (admin API or removing/editing the file over the trusted
management path). This module deliberately offers no automatic bypass.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ENGAGE_TRUE_VALUES = {"1", "true", "yes", "on"}


class GuardError(RuntimeError):
    """Raised when the guard file cannot be written or a transition is invalid."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _env_engaged() -> bool:
    return str(os.environ.get("SMITE_DISABLE_MUTATIONS", "")).strip().lower() in ENGAGE_TRUE_VALUES


def guard_path() -> Path:
    path = Path(settings.mutation_guard_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _base_state(source: str) -> Dict[str, Any]:
    return {
        "engaged": False,
        "expired": False,
        "source": source,
        "reason": "",
        "owner": "",
        "engaged_at": None,
        "expires_at": None,
        "released_at": None,
        "released_by": None,
        "path": str(guard_path()),
    }


def _fail_closed(reason: str) -> Dict[str, Any]:
    state = _base_state("corrupt")
    state["engaged"] = True
    state["reason"] = reason
    return state


def state() -> Dict[str, Any]:
    """Read the current guard state. Never raises; unreadable files fail closed."""
    if _env_engaged():
        result = _base_state("environment")
        result["engaged"] = True
        result["reason"] = "SMITE_DISABLE_MUTATIONS is set"
        return result

    path = guard_path()
    if not path.exists():
        return _base_state("missing")
    if path.is_symlink():
        return _fail_closed("guard file is a symlink; refusing to follow it")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        return _fail_closed(f"guard file is unreadable: {error}")
    if not raw.strip():
        return _fail_closed("guard file is empty")

    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return _fail_closed("guard file is not valid JSON")
    if not isinstance(record, dict):
        return _fail_closed("guard file is not a JSON object")
    if record.get("schema") != SCHEMA_VERSION:
        return _fail_closed(f"guard file schema {record.get('schema')!r} is not supported")

    result = _base_state("file")
    result["engaged"] = record.get("engaged") is True
    result["reason"] = str(record.get("reason") or "")[:500]
    result["owner"] = str(record.get("owner") or "")[:200]
    result["engaged_at"] = record.get("engaged_at")
    result["expires_at"] = record.get("expires_at")
    result["released_at"] = record.get("released_at")
    result["released_by"] = record.get("released_by")

    expires_at = _parse_time(result["expires_at"])
    if result["engaged"] and expires_at is not None and expires_at <= _now():
        result["expired"] = True
    return result


def is_engaged() -> bool:
    return bool(state()["engaged"])


def _write_record(record: Dict[str, Any]) -> None:
    path = guard_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise GuardError(f"cannot create guard directory {path.parent}: {error}") from error

    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    tmp_path: Optional[str] = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".mutation-guard-", suffix=".tmp", dir=str(path.parent))
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
        except OSError:
            dir_fd = None
        if dir_fd is not None:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except OSError as error:
        raise GuardError(f"cannot write guard file {path}: {error}") from error
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def engage(reason: str, owner: str = "", expires_in_minutes: Optional[int] = None) -> Dict[str, Any]:
    """Block automatic mutations. Requires an explicit reason for the audit trail."""
    reason = (reason or "").strip()
    if not reason:
        raise GuardError("a reason is required to engage the mutation guard")
    if len(reason) > 500:
        raise GuardError("guard reason is too long (max 500 characters)")
    if expires_in_minutes is not None and expires_in_minutes <= 0:
        raise GuardError("expires_in_minutes must be positive when provided")

    now = _now()
    expires_at = now + timedelta(minutes=expires_in_minutes) if expires_in_minutes else None
    record = {
        "schema": SCHEMA_VERSION,
        "engaged": True,
        "reason": reason,
        "owner": (owner or "")[:200],
        "engaged_at": now.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "released_at": None,
        "released_by": None,
    }
    _write_record(record)
    logger.critical(
        "Mutation guard ENGAGED by %s: %s (expires: %s). Automatic tunnel, token, reset and SSL mutations are suspended.",
        owner or "unknown",
        reason,
        expires_at.isoformat() if expires_at else "never",
    )
    return state()


def release(released_by: str = "") -> Dict[str, Any]:
    """Explicitly allow automatic mutations again."""
    if _env_engaged():
        raise GuardError("SMITE_DISABLE_MUTATIONS is set; unsets it and restart the panel to release")

    current = state()
    if not current["engaged"]:
        return current

    now = _now()
    base = _read_record_for_release()
    record = {
        "schema": SCHEMA_VERSION,
        "engaged": False,
        "reason": str(base.get("reason") or "")[:500],
        "owner": str(base.get("owner") or "")[:200],
        "engaged_at": base.get("engaged_at"),
        "expires_at": base.get("expires_at"),
        "released_at": now.isoformat(),
        "released_by": (released_by or "")[:200],
    }
    _write_record(record)
    logger.warning(
        "Mutation guard RELEASED by %s. Automatic tunnel, token, reset and SSL mutations may resume.",
        released_by or "unknown",
    )
    return state()


def _read_record_for_release() -> Dict[str, Any]:
    """Best-effort original record; a corrupt file still gets replaced on release."""
    path = guard_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def startup_report() -> Dict[str, Any]:
    """Log the guard state once at startup so operators see a stale restore immediately."""
    current = state()
    if current["engaged"]:
        if current["expired"]:
            logger.critical(
                "Mutation guard is engaged and its expiry (%s) has passed; it stays engaged until an operator releases it. "
                "No automatic tunnel/token/reset/SSL mutation will run.",
                current["expires_at"],
            )
        else:
            logger.critical(
                "Mutation guard is engaged (source=%s, reason=%s); automatic tunnel/token/reset/SSL mutations are suspended. "
                "Inspect the restored database and node inventory, then release it explicitly.",
                current["source"],
                current["reason"] or "no reason recorded",
            )
    else:
        logger.info("Mutation guard is not engaged")
    return current


class MutationGuard:
    """Singleton facade. Every call re-reads the file, so separate processes agree."""

    state = staticmethod(state)
    is_engaged = staticmethod(is_engaged)
    engage = staticmethod(engage)
    release = staticmethod(release)
    startup_report = staticmethod(startup_report)


mutation_guard = MutationGuard()
