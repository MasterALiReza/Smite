"""Settings API endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime, timezone
from app.database import get_db, AsyncSessionLocal
from app.models import Settings, Admin
from app.routers.auth import get_current_user
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Placeholder returned instead of real secrets. A real token can never equal this.
MASKED_SECRET = "••••••••"
# Row that stores the optimistic-concurrency revision, not a user-facing section.
SETTINGS_META_KEY = "_meta"
MAX_INTERVAL = 525600  # one year expressed in minutes


class FrpSettings(BaseModel):
    enabled: bool = False
    port: int = Field(7000, ge=1, le=65535)
    token: Optional[str] = Field(None, max_length=512)

    @field_validator("token")
    @classmethod
    def _validate_token(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, "", MASKED_SECRET):
            return value
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("FRP token must not contain control characters")
        if not value.strip():
            raise ValueError("FRP token must not be blank")
        return value


class TelegramSettings(BaseModel):
    enabled: bool = False
    bot_token: Optional[str] = Field(None, max_length=256)
    admin_ids: List[str] = Field(default_factory=list)
    backup_enabled: bool = False
    backup_interval: int = Field(60, ge=1, le=MAX_INTERVAL)
    backup_interval_unit: Literal["minutes", "hours"] = "minutes"
    backup_passphrase: Optional[str] = Field(None, max_length=256)

    @field_validator("admin_ids")
    @classmethod
    def _validate_admin_ids(cls, value: List[str]) -> List[str]:
        cleaned: List[str] = []
        for raw in value:
            text = str(raw).strip()
            if not text:
                raise ValueError("Telegram admin IDs must not be empty")
            if not text.isdigit() or int(text) <= 0:
                raise ValueError(
                    f"Telegram admin ID '{text}' must be a positive numeric user ID"
                )
            if text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("bot_token")
    @classmethod
    def _validate_bot_token(cls, value: Optional[str]) -> Optional[str]:
        if value in (None, "", MASKED_SECRET):
            return value
        if any(char.isspace() for char in value):
            raise ValueError("Telegram bot token must not contain whitespace")
        if ":" not in value:
            raise ValueError("Telegram bot token must look like '<bot_id>:<secret>'")
        return value


class TunnelSettings(BaseModel):
    auto_reapply_enabled: bool = False
    auto_reapply_interval: int = Field(60, ge=1, le=MAX_INTERVAL)
    auto_reapply_interval_unit: Literal["minutes", "hours"] = "minutes"


class SettingsUpdate(BaseModel):
    # Optional so older frontends keep working; when present it must match the
    # revision returned by GET, otherwise another session changed the settings.
    revision: Optional[int] = Field(None, ge=0)
    frp: Optional[FrpSettings] = None
    telegram: Optional[TelegramSettings] = None
    tunnel: Optional[TunnelSettings] = None


def _resolve_secret(incoming: Optional[str], existing: Optional[str]) -> Optional[str]:
    """Resolve a secret field from an update payload.

    - Masked placeholder or absent (None) -> preserve existing value
    - Empty string -> explicit clear
    - Anything else -> new value
    """
    if incoming is None or incoming == MASKED_SECRET:
        return existing
    if incoming == "":
        return None
    return incoming


def _dump_model(m: Optional[BaseModel]) -> dict:
    """Serialize model cleanly across Pydantic v1 and v2"""
    if m is None:
        return {}
    if hasattr(m, "model_dump"):
        return m.model_dump(exclude_none=True)
    return m.dict(exclude_none=True)


def _read_revision(meta: Any) -> int:
    if isinstance(meta, dict):
        try:
            return max(0, int(meta.get("revision", 0)))
        except (TypeError, ValueError):
            return 0
    return 0


def _callable_attr(obj: Any, name: str):
    attr = getattr(obj, name, None)
    return attr if callable(attr) else None


def _runtime_status() -> Dict[str, Any]:
    """Describe what the panel processes are actually doing right now.

    UI badges must reflect runtime state, not the values stored in the database:
    a saved setting can be enabled while its process failed to start.
    """
    status: Dict[str, Any] = {}

    try:
        from app.frp_comm_manager import frp_comm_manager

        is_running = _callable_attr(frp_comm_manager, "is_running")
        running = bool(is_running()) if is_running else False
        status["frp"] = {
            "running": running,
            "port": int(getattr(frp_comm_manager, "port", 7000) or 7000) if running else None,
        }
    except Exception as exc:  # pragma: no cover - defensive: settings must still load
        logger.debug(f"Could not read FRP runtime status: {exc}")
        status["frp"] = {"running": False, "port": None}

    try:
        from app.telegram_bot import telegram_bot

        runtime = _callable_attr(telegram_bot, "runtime_status")
        if runtime:
            status["telegram"] = runtime()
        else:
            status["telegram"] = {"running": False, "polling": False, "last_error": None}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Could not read Telegram runtime status: {exc}")
        status["telegram"] = {"running": False, "polling": False, "last_error": None}

    try:
        from app.tunnel_reapply_manager import tunnel_reapply_manager

        periodic = getattr(tunnel_reapply_manager, "task", None)
        healer = getattr(tunnel_reapply_manager, "heal_task", None)
        status["tunnel"] = {
            "periodic_running": bool(periodic is not None and not periodic.done()),
            "healer_running": bool(healer is not None and not healer.done()),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Could not read tunnel automation runtime status: {exc}")
        status["tunnel"] = {"periodic_running": False, "healer_running": False}

    return status


@router.get("")
async def get_settings(db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Get all settings (secrets are masked)"""
    result = await db.execute(select(Settings))
    settings_list = result.scalars().all()

    settings_dict = {s.key: s.value for s in settings_list}

    frp_settings = settings_dict.get("frp", {}) or {}
    telegram_settings = settings_dict.get("telegram", {}) or {}
    tunnel_settings = settings_dict.get("tunnel", {})  # Backward compatible: defaults to {} if not exists

    return {
        "revision": _read_revision(settings_dict.get(SETTINGS_META_KEY)),
        "frp": {
            "enabled": frp_settings.get("enabled", False),
            "port": frp_settings.get("port", 7000),
            "token": MASKED_SECRET if frp_settings.get("token") else None,
            "token_set": bool(frp_settings.get("token")),
        },
        "telegram": {
            "enabled": telegram_settings.get("enabled", False),
            "bot_token": MASKED_SECRET if telegram_settings.get("bot_token") else None,
            "bot_token_set": bool(telegram_settings.get("bot_token")),
            "admin_ids": telegram_settings.get("admin_ids", []),
            "backup_enabled": telegram_settings.get("backup_enabled", False),
            "backup_interval": telegram_settings.get("backup_interval", 60),
            "backup_interval_unit": telegram_settings.get("backup_interval_unit", "minutes"),
            "backup_passphrase": MASKED_SECRET if telegram_settings.get("backup_passphrase") else None,
            "backup_passphrase_set": bool(telegram_settings.get("backup_passphrase")),
        },
        "tunnel": {
            "auto_reapply_enabled": tunnel_settings.get("auto_reapply_enabled", False) if tunnel_settings else False,
            "auto_reapply_interval": tunnel_settings.get("auto_reapply_interval", 60) if tunnel_settings else 60,
            "auto_reapply_interval_unit": tunnel_settings.get("auto_reapply_interval_unit", "minutes") if tunnel_settings else "minutes"
        },
        "runtime": _runtime_status(),
    }


@router.put("")
async def update_settings(settings_update: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db), current_user: Admin = Depends(get_current_user)):
    """Update settings (masked/absent secrets are preserved, empty string clears).

    Only the sections present in the payload are touched. All provided sections
    are committed in one transaction, then only effective runtime changes are
    applied. A runtime failure restores both the stored value and the previous
    runtime process so a bad value cannot silently take a working service down.
    """
    from app.frp_comm_manager import frp_comm_manager

    warnings: List[str] = []
    section_results: Dict[str, Dict[str, Any]] = {}
    runtime_failed = False

    frp_old: Dict[str, Any] = {}
    frp_new: Optional[Dict[str, Any]] = None
    telegram_old: Dict[str, Any] = {}
    telegram_new: Optional[Dict[str, Any]] = None
    tunnel_old: Dict[str, Any] = {}
    tunnel_new: Optional[Dict[str, Any]] = None

    async def _load_row(key: str):
        result = await db.execute(select(Settings).where(Settings.key == key))
        return result.scalar_one_or_none()

    def _store_row(row, key: str, value: Dict[str, Any]):
        if row is None:
            row = Settings(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return row

    meta_row = await _load_row(SETTINGS_META_KEY)
    current_revision = _read_revision(meta_row.value if meta_row else None)
    if settings_update.revision is not None and settings_update.revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail="Settings were modified by another session. Reload the page and apply your changes again.",
        )

    frp_row = None
    telegram_row = None
    tunnel_row = None

    if settings_update.frp:
        frp_row = await _load_row("frp")
        frp_old = dict(frp_row.value or {}) if frp_row else {}
        new_value = _dump_model(settings_update.frp)

        resolved_token = _resolve_secret(new_value.get("token"), frp_old.get("token"))
        if resolved_token is None:
            new_value.pop("token", None)
        else:
            new_value["token"] = resolved_token

        frp_new = new_value
        frp_row = _store_row(frp_row, "frp", new_value)

    if settings_update.telegram:
        telegram_row = await _load_row("telegram")
        telegram_old = dict(telegram_row.value or {}) if telegram_row else {}
        new_value = _dump_model(settings_update.telegram)

        resolved_bot_token = _resolve_secret(new_value.get("bot_token"), telegram_old.get("bot_token"))
        if resolved_bot_token is None:
            new_value.pop("bot_token", None)
        else:
            new_value["bot_token"] = resolved_bot_token

        incoming_passphrase = new_value.get("backup_passphrase")
        if incoming_passphrase not in (None, MASKED_SECRET, "") and len(incoming_passphrase.strip()) < 12:
            raise HTTPException(
                status_code=400,
                detail="Backup passphrase must be at least 12 characters (or leave it empty for a sanitized export)."
            )
        resolved_passphrase = _resolve_secret(incoming_passphrase, telegram_old.get("backup_passphrase"))
        if resolved_passphrase is None:
            new_value.pop("backup_passphrase", None)
        else:
            new_value["backup_passphrase"] = resolved_passphrase

        telegram_new = new_value
        telegram_row = _store_row(telegram_row, "telegram", new_value)

    if settings_update.tunnel:
        tunnel_row = await _load_row("tunnel")
        tunnel_old = dict(tunnel_row.value or {}) if tunnel_row else {}
        new_value = _dump_model(settings_update.tunnel)
        tunnel_new = new_value
        tunnel_row = _store_row(tunnel_row, "tunnel", new_value)

    revision = current_revision
    if frp_new is not None or telegram_new is not None or tunnel_new is not None:
        revision = current_revision + 1
        _store_row(meta_row, SETTINGS_META_KEY, {"revision": revision})
        await db.commit()

    if frp_new is not None:
        old_enabled = bool(frp_old.get("enabled"))
        new_enabled = bool(frp_new.get("enabled"))
        old_port = int(frp_old.get("port", 7000) or 7000)
        new_port = int(frp_new.get("port", 7000) or 7000)
        old_token = frp_old.get("token")
        new_token = frp_new.get("token")
        runtime_changed = new_port != old_port or new_token != old_token
        is_running = _callable_attr(frp_comm_manager, "is_running")
        live_running = bool(is_running()) if is_running else old_enabled
        needs_apply = new_enabled and (not old_enabled or runtime_changed or not live_running)

        if new_enabled and not new_token:
            warnings.append(
                "FRP communication is enabled without an auth token; any client that can reach "
                "the FRP port may register tunnels. Set a token to require authentication."
            )

        if needs_apply:
            try:
                if live_running:
                    await frp_comm_manager.stop()
                started = await frp_comm_manager.start(new_port, new_token)
                if not started:
                    raise RuntimeError("frps did not start (binary missing or port unavailable)")
                section_results["frp"] = {"runtime": "applied"}
                logger.info(f"FRP communication server started on port {new_port}")
            except Exception as e:
                runtime_failed = True
                logger.error(f"Failed to start FRP communication server: {e}", exc_info=True)
                section_results["frp"] = {"runtime": "failed"}
                warnings.append(f"FRP communication server failed to start: {e}. Previous settings were restored.")
                frp_row = _store_row(frp_row, "frp", frp_old)
                await db.commit()
                try:
                    stop = _callable_attr(frp_comm_manager, "stop")
                    if stop:
                        await stop()
                    if old_enabled:
                        restored = await frp_comm_manager.start(old_port, old_token)
                        if not restored:
                            warnings.append("FRP communication could not be restored to its previous configuration.")
                except Exception as restore_error:
                    logger.error(f"Failed to restore FRP communication server: {restore_error}", exc_info=True)
                    warnings.append(f"FRP communication could not be restored: {restore_error}")
        elif not new_enabled and old_enabled:
            try:
                await frp_comm_manager.stop()
                section_results["frp"] = {"runtime": "stopped"}
                logger.info("FRP communication server stopped")
            except Exception as e:
                runtime_failed = True
                logger.error(f"Failed to stop FRP communication server: {e}", exc_info=True)
                section_results["frp"] = {"runtime": "failed"}
                warnings.append(f"FRP communication server failed to stop: {e}")
        else:
            section_results["frp"] = {"runtime": "unchanged"}

    if telegram_new is not None:
        from app.telegram_bot import telegram_bot

        old_enabled = bool(telegram_old.get("enabled"))
        new_enabled = bool(telegram_new.get("enabled"))
        token_changed = telegram_new.get("bot_token") != telegram_old.get("bot_token")
        backup_changed = any(
            telegram_new.get(key) != telegram_old.get(key)
            for key in ("admin_ids", "backup_enabled", "backup_interval", "backup_interval_unit", "backup_passphrase")
        )
        is_running = _callable_attr(telegram_bot, "is_running")
        live_running = bool(is_running()) if is_running else old_enabled
        needs_start = new_enabled and (not old_enabled or token_changed or not live_running)

        def _telegram_error() -> str:
            last_error = getattr(telegram_bot, "last_error", None)
            return str(last_error) if last_error else "the bot did not start"

        try:
            if needs_start:
                started = await telegram_bot.start()
                if not started:
                    raise RuntimeError(_telegram_error())
                section_results["telegram"] = {"runtime": "applied"}
                logger.info("Telegram bot started")
            elif not new_enabled and old_enabled:
                await telegram_bot.stop()
                section_results["telegram"] = {"runtime": "stopped"}
                logger.info("Telegram bot stopped")
            elif new_enabled and backup_changed:
                await telegram_bot.start_backup_task()
                section_results["telegram"] = {"runtime": "applied"}
                logger.info("Telegram bot backup settings applied")
            else:
                section_results["telegram"] = {"runtime": "unchanged"}
        except Exception as e:
            runtime_failed = True
            logger.error(f"Failed to apply Telegram runtime settings: {e}", exc_info=True)
            section_results["telegram"] = {"runtime": "failed"}
            warnings.append(f"Telegram runtime update failed: {e}. Previous settings were restored.")
            telegram_row = _store_row(telegram_row, "telegram", telegram_old)
            await db.commit()
            try:
                stop = _callable_attr(telegram_bot, "stop")
                if stop:
                    await stop()
                if old_enabled:
                    restored = await telegram_bot.start()
                    if not restored:
                        warnings.append(
                            "Telegram bot could not be restarted with its previous settings; "
                            "it will retry on the next panel start."
                        )
            except Exception as restore_error:
                logger.error(f"Failed to restore Telegram bot: {restore_error}", exc_info=True)
                warnings.append(f"Telegram bot could not be restored: {restore_error}")

    if tunnel_new is not None and tunnel_new != tunnel_old:
        try:
            from app.tunnel_reapply_manager import tunnel_reapply_manager
            await tunnel_reapply_manager.apply_settings()
            section_results["tunnel"] = {"runtime": "applied"}
            logger.info("Tunnel automation settings applied")
        except Exception as e:
            logger.error(f"Failed to apply tunnel automation settings: {e}", exc_info=True)
            section_results["tunnel"] = {"runtime": "failed"}
            warnings.append(f"Tunnel automation settings could not be applied: {e}")
    elif tunnel_new is not None:
        section_results["tunnel"] = {"runtime": "unchanged"}

    response: Dict[str, Any] = {
        "status": "partial" if runtime_failed else "success",
        "revision": revision,
        "sections": section_results,
    }
    if warnings:
        response["warnings"] = warnings
    return response
