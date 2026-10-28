"""Telegram bot for panel management"""
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Node, Tunnel, Settings
import httpx

logger = logging.getLogger(__name__)

BACKUP_MAGIC = b"SMITE-BACKUP-1\n"
BACKUP_KDF_ITERATIONS = 390_000
_ENV_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
_SENSITIVE_KEY_PARTS = ("secret", "password", "passwd", "credential", "private", "pkcs12")


def _is_sensitive_key(key: str) -> bool:
    """Heuristic for config keys whose values must never leave the server unencrypted"""
    k = str(key).strip().lower()
    if not k:
        return False
    if k.endswith("_mode") or k.endswith("_set"):
        return False
    if k in {"token", "auth", "key"}:
        return True
    if any(part in k for part in _SENSITIVE_KEY_PARTS):
        return True
    if "token" in k or "key_pem" in k:
        return True
    return k.endswith(("_key", "_token", "_secret", "_password", "_passwd", "_credential"))


def _redact_json(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _is_sensitive_key(key) and item not in (None, ""):
                result[key] = "REDACTED_IN_SANITIZED_BACKUP"
            else:
                result[key] = _redact_json(item)
        return result
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def _sanitize_env_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        match = _ENV_KEY_RE.match(line)
        if match and _is_sensitive_key(match.group(1)):
            lines.append(f"{match.group(1)}=REDACTED_IN_SANITIZED_BACKUP")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def _snapshot_sqlite_db(source: Path, destination: Path) -> None:
    """Consistent online SQLite snapshot without copying WAL/SHM sidecars"""
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(destination))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _sanitize_backup_database(db_path: Path) -> None:
    """Remove secrets from a snapshot, then VACUUM so old pages are not recoverable"""
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()
        tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        targets = (("settings", "value"), ("nodes", "metadata"), ("tunnels", "spec"))
        for table, column in targets:
            if table not in tables:
                continue
            rows = cursor.execute(f"SELECT id, {column} FROM {table}").fetchall()
            for row_id, raw in rows:
                if raw in (None, ""):
                    continue
                try:
                    parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                except (TypeError, ValueError):
                    continue
                sanitized = _redact_json(parsed)
                if sanitized != parsed:
                    cursor.execute(
                        f"UPDATE {table} SET {column}=? WHERE id=?",
                        (json.dumps(sanitized), row_id),
                    )
        if "admins" in tables:
            cursor.execute("UPDATE admins SET password_hash='REDACTED_IN_SANITIZED_BACKUP'")
        conn.commit()
        cursor.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
        cursor.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass


def _strip_private_material(root: Path) -> None:
    """Best-effort removal of keys/certificate bundles that contain private material"""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.suffix.lower() in {".key", ".p12", ".pfx"}:
                path.unlink()
                continue
            if path.stat().st_size <= 2 * 1024 * 1024 and b"PRIVATE KEY" in path.read_bytes():
                path.unlink()
        except OSError:
            continue


def _derive_backup_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=BACKUP_KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_backup_archive(zip_path: Path, passphrase: str) -> Path:
    """Wrap the ZIP in an authenticated, passphrase-derived encrypted container"""
    from cryptography.fernet import Fernet

    salt = os.urandom(16)
    token = Fernet(_derive_backup_key(passphrase, salt)).encrypt(zip_path.read_bytes())
    encrypted_path = zip_path.with_name(zip_path.name + ".enc")
    encrypted_path.write_bytes(BACKUP_MAGIC + salt + token)
    try:
        os.chmod(str(encrypted_path), 0o600)
    except OSError:
        pass
    zip_path.unlink()
    return encrypted_path


def decrypt_backup_archive(encrypted_path: Path, passphrase: str, destination: Path) -> None:
    from cryptography.fernet import Fernet, InvalidToken

    data = encrypted_path.read_bytes()
    if not data.startswith(BACKUP_MAGIC):
        raise ValueError("Not a Smite encrypted backup archive.")
    salt = data[len(BACKUP_MAGIC):len(BACKUP_MAGIC) + 16]
    token = data[len(BACKUP_MAGIC) + 16:]
    try:
        plaintext = Fernet(_derive_backup_key(passphrase, salt)).decrypt(token)
    except InvalidToken as exc:
        raise ValueError("Invalid backup passphrase or corrupted archive.") from exc
    destination.write_bytes(plaintext)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton  # type: ignore
    from telegram.ext import (  # type: ignore
        Application, CommandHandler, CallbackQueryHandler, ContextTypes,
        ConversationHandler, MessageHandler, filters
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    Update = None  # type: ignore
    InlineKeyboardButton = None  # type: ignore
    InlineKeyboardMarkup = None  # type: ignore
    ReplyKeyboardMarkup = None  # type: ignore
    KeyboardButton = None  # type: ignore
    Application = None  # type: ignore
    CommandHandler = None  # type: ignore
    CallbackQueryHandler = None  # type: ignore
    ContextTypes = None  # type: ignore
    ConversationHandler = None  # type: ignore
    MessageHandler = None  # type: ignore
    filters = None  # type: ignore
    logger.warning("python-telegram-bot not installed. Telegram bot will not work.")


# No conversation states needed


class TelegramBot:
    """Telegram bot for managing panel"""
    
    def __init__(self):
        self.application: Optional[Application] = None
        self.enabled = False
        self.bot_token: Optional[str] = None
        self.admin_ids: List[str] = []
        self.backup_task: Optional[asyncio.Task] = None
        self.backup_enabled = False
        self.backup_interval = 60
        self.backup_interval_unit = "minutes"
        self.backup_passphrase: Optional[str] = None
        self.last_error: Optional[str] = None
        self._backup_lock: Optional[asyncio.Lock] = None
        self.user_states: Dict[int, Dict[str, Any]] = {}
        api_url = os.getenv("PANEL_API_URL")
        if not api_url:
            api_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        self.api_base_url = api_url
    
    async def load_settings(self):
        """Load settings from database"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Settings).where(Settings.key == "telegram"))
            setting = result.scalar_one_or_none()
            if setting and setting.value:
                self.enabled = setting.value.get("enabled", False)
                self.bot_token = setting.value.get("bot_token")
                self.admin_ids = setting.value.get("admin_ids", [])
                self.backup_enabled = setting.value.get("backup_enabled", False)
                self.backup_interval = setting.value.get("backup_interval", 60)
                self.backup_interval_unit = setting.value.get("backup_interval_unit", "minutes")
                self.backup_passphrase = setting.value.get("backup_passphrase")
            else:
                self.enabled = False
                self.bot_token = None
                self.admin_ids = []
                self.backup_enabled = False
                self.backup_interval = 60
                self.backup_interval_unit = "minutes"
                self.backup_passphrase = None
    
    def t(self, user_id: int, key: str, **kwargs) -> str:
        """Get text (simplified - no translations)"""
        translations = {
            "welcome": "👋 Welcome to Smite Panel Bot!\n\nSelect an action:",
            "access_denied": "❌ Access denied. You are not an admin.",
            "node_stats": "📊 Node Stats",
            "tunnel_stats": "📊 Tunnel Stats",
            "logs": "📋 Logs",
            "backup": "📦 Backup",
            "no_nodes": "📭 No nodes found.",
            "no_tunnels": "📭 No tunnels found.",
            "error": "❌ Error: {error}",
        }
        text = translations.get(key, key)
        return text.format(**kwargs) if kwargs else text
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return str(user_id) in self.admin_ids

    def is_running(self) -> bool:
        """True while a polling application is alive."""
        return self.application is not None

    def _redact(self, message: str) -> str:
        if self.bot_token and message:
            return message.replace(self.bot_token, "***")
        return message

    def runtime_status(self) -> Dict[str, Any]:
        """Runtime view for the settings UI; never includes the token."""
        updater = getattr(self.application, "updater", None) if self.application else None
        return {
            "running": self.application is not None,
            "polling": bool(getattr(updater, "running", False)),
            "desired": bool(self.enabled),
            "last_error": self.last_error,
        }
    
    async def start(self):
        """Start Telegram bot"""
        if not TELEGRAM_AVAILABLE:
            self.last_error = "python-telegram-bot is not installed"
            logger.error("python-telegram-bot not available. Cannot start bot.")
            return False
        
        await self.load_settings()
        
        if not self.enabled:
            logger.info("Telegram bot not enabled")
            return False
        if not self.bot_token:
            self.last_error = "Telegram is enabled but no bot token is configured"
            logger.info("Telegram bot not enabled or token not set")
            return False
        
        # Stop existing instance if running
        await self.stop()
        self.last_error = None
        
        try:
            self.application = Application.builder().token(self.bot_token).build()
            
            self.application.add_handler(CommandHandler("start", self.cmd_start))
            self.application.add_handler(CommandHandler("help", self.cmd_help))
            self.application.add_handler(CommandHandler("nodes", self.cmd_nodes))
            self.application.add_handler(CommandHandler("tunnels", self.cmd_tunnels))
            self.application.add_handler(CommandHandler("status", self.cmd_status))
            self.application.add_handler(CommandHandler("backup", self.cmd_backup))
            self.application.add_handler(CommandHandler("logs", self.cmd_logs))
            self.application.add_handler(CallbackQueryHandler(self.handle_callback, pattern="^(back_to_menu|node_stats|tunnel_stats|logs|cmd_nodes|cmd_tunnels|cmd_backup|cmd_status)$"))
            
            # Handle persistent keyboard buttons - must be after conversation handlers
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.handle_text_message
            ))
            
            await self.start_backup_task()
            
            # Initialize and start the application (PTB v20+ async lifecycle)
            await self.application.initialize()
            await self.application.start()
            
            # Start polling using updater (PTB v20+ way for existing event loop)
            if hasattr(self.application, 'updater') and self.application.updater:
                await self.application.updater.start_polling(drop_pending_updates=True)
                logger.info("Telegram bot polling started successfully")
            else:
                self.last_error = "Telegram updater is unavailable; polling could not start"
                logger.error("Application updater not available. Polling cannot be started.")
                await self.stop()
                return False
            
            logger.info("Telegram bot started successfully (polling mode)")
            
            return True
        except Exception as e:
            self.last_error = self._redact(str(e))
            logger.error(f"Failed to start Telegram bot: {e}", exc_info=True)
            await self.stop()
            return False
    
    async def stop(self):
        """Stop Telegram bot (idempotent - safe to call multiple times)"""
        await self.stop_backup_task()
        
        if not self.application:
            return  # Already stopped
        
        try:
            # Stop updater first (if it exists and is running)
            if hasattr(self.application, 'updater') and self.application.updater:
                try:
                    # Check if updater is running before stopping
                    if hasattr(self.application.updater, 'running') and self.application.updater.running:
                        await self.application.updater.stop()
                        logger.info("Telegram bot updater stopped")
                except Exception as e:
                    logger.warning(f"Error stopping updater: {e}")
            
            # Stop and shutdown application
            await self.application.stop()
            await self.application.shutdown()
        except Exception as e:
            logger.warning(f"Error stopping Telegram bot: {e}")
        finally:
            self.application = None
            logger.info("Telegram bot stopped")
    
    async def start_backup_task(self):
        """Start automatic backup task"""
        await self.stop_backup_task()
        await self.load_settings()
        
        if self.backup_enabled and self.admin_ids:
            self.backup_task = asyncio.create_task(self._backup_loop())
            logger.info(f"Automatic backup task started: interval={self.backup_interval} {self.backup_interval_unit}")
    
    async def stop_backup_task(self):
        """Stop automatic backup task"""
        if self.backup_task:
            self.backup_task.cancel()
            try:
                await self.backup_task
            except asyncio.CancelledError:
                pass
            self.backup_task = None
            logger.info("Automatic backup task stopped")
    
    async def _backup_loop(self):
        """Background task for automatic backups"""
        try:
            while True:
                await self.load_settings()
                
                if not self.backup_enabled or not self.admin_ids:
                    await asyncio.sleep(60)
                    continue
                
                if self.backup_interval_unit == "hours":
                    sleep_seconds = self.backup_interval * 3600
                else:
                    sleep_seconds = self.backup_interval * 60
                
                await asyncio.sleep(sleep_seconds)
                
                if not self.backup_enabled:
                    continue
                
                try:
                    recipients = self._backup_recipient_ids()
                    if not recipients:
                        logger.warning("Automatic backup skipped: no private numeric Telegram admin IDs configured")
                        continue
                    backup_path = await self.create_backup()
                    if not backup_path or not self.application or not self.application.bot:
                        continue
                    filename = Path(backup_path).name
                    mode_label = (
                        "Encrypted full backup"
                        if backup_path.endswith(".enc")
                        else "Sanitized export (secrets removed)"
                    )
                    try:
                        for admin_id in recipients:
                            try:
                                with open(backup_path, "rb") as f:
                                    await self.application.bot.send_document(
                                        chat_id=admin_id,
                                        document=f,
                                        filename=filename,
                                        caption=f"🔄 {mode_label} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                    )
                            except Exception as e:
                                logger.error(f"Failed to send backup to admin {admin_id}: {e}")
                        logger.info("Automatic backup sent successfully")
                    finally:
                        if os.path.exists(backup_path):
                            os.remove(backup_path)
                except Exception as e:
                    logger.error(f"Error in automatic backup: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Backup loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Backup loop error: {e}", exc_info=True)

    async def send_admin_alert(self, text: str):
        """Send urgent or notification message to all configured Telegram admin IDs"""
        await self.load_settings()
        if not self.application or not self.application.bot or not self.admin_ids:
            return
        for admin_id_str in self.admin_ids:
            try:
                admin_id = int(admin_id_str)
                await self.application.bot.send_message(chat_id=admin_id, text=text)
            except Exception as e:
                logger.warning(f"Failed to send Telegram alert to admin {admin_id_str}: {e}")
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        try:
            user_id = update.effective_user.id
            reply_markup = self._get_keyboard(user_id)
            
            if not self.is_admin(user_id):
                await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
                return
            
            await update.message.reply_text(self.t(user_id, "welcome"), reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in cmd_start: {e}", exc_info=True)
            try:
                user_id = update.effective_user.id
                reply_markup = self._get_keyboard(user_id)
                await update.message.reply_text("❌ Error: Please try again.", reply_markup=reply_markup)
            except:
                pass
    
    def _get_keyboard(self, user_id: int) -> ReplyKeyboardMarkup:
        """Get persistent keyboard markup"""
        keyboard = [
            [
                KeyboardButton(self.t(user_id, 'node_stats')),
                KeyboardButton(self.t(user_id, 'tunnel_stats'))
            ],
            [
                KeyboardButton(self.t(user_id, 'logs')),
                KeyboardButton(self.t(user_id, 'backup'))
            ],
        ]
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    async def show_main_menu(self, message_or_query):
        """Show main menu with persistent keyboard buttons"""
        try:
            # Get user_id and message object
            if hasattr(message_or_query, 'from_user'):
                user_id = message_or_query.from_user.id
                message = message_or_query
            elif hasattr(message_or_query, 'message'):
                user_id = message_or_query.message.from_user.id
                message = message_or_query.message
            else:
                user_id = message_or_query.chat.id if hasattr(message_or_query, 'chat') else 0
                message = message_or_query
            
            reply_markup = self._get_keyboard(user_id)
            text = self.t(user_id, "welcome")
            
            if hasattr(message, 'reply_text'):
                await message.reply_text(text, reply_markup=reply_markup)
            elif hasattr(message_or_query, 'edit_message_text'):
                await message_or_query.edit_message_text(text, reply_markup=reply_markup)
            elif hasattr(message_or_query, 'message'):
                await message_or_query.message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error showing main menu: {e}", exc_info=True)
            try:
                user_id = message_or_query.from_user.id if hasattr(message_or_query, 'from_user') else 0
                reply_markup = self._get_keyboard(user_id)
                if hasattr(message_or_query, 'reply_text'):
                    await message_or_query.reply_text(self.t(user_id, "welcome"), reply_markup=reply_markup)
            except:
                pass
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        help_text = """📋 Available Commands:

/start - Show main menu
/nodes - List all nodes
/tunnels - List all tunnels
/status - Show panel status
/logs - Show recent logs
/backup - Create and send backup

Use buttons in messages to interact with nodes and tunnels."""
        
        await update.message.reply_text(help_text, reply_markup=reply_markup)
    
    async def cmd_nodes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /nodes command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        await self.cmd_nodes_callback(update.message)
    
    async def cmd_tunnels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /tunnels command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        await self.cmd_tunnels_callback(update.message)
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        await self.cmd_status_callback(update.message)
    
    async def cmd_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /backup command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        await update.message.reply_text("📦 Creating backup...", reply_markup=reply_markup)
        
        backup_path = None
        try:
            backup_path = await self.create_backup()
            if not backup_path:
                await update.message.reply_text("❌ Failed to create backup", reply_markup=reply_markup)
                return
            filename = Path(backup_path).name
            mode_label = (
                "Encrypted full backup"
                if backup_path.endswith(".enc")
                else "Sanitized export (secrets removed)"
            )
            # Always deliver to the admin's private chat, even if the command was issued in a group
            with open(backup_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename=filename,
                    caption=f"✅ {mode_label} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            await update.message.reply_text("✅ Backup sent to your private chat.", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error creating backup: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Error creating backup: {str(e)}", reply_markup=reply_markup)
        finally:
            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)
    
    async def cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /logs command"""
        user_id = update.effective_user.id
        reply_markup = self._get_keyboard(user_id)
        
        if not self.is_admin(user_id):
            await update.message.reply_text(self.t(user_id, "access_denied"), reply_markup=reply_markup)
            return
        
        try:
            from app.routers.logs import get_recent_logs
            logs = get_recent_logs(20)
            if logs:
                text = "📋 Recent Logs:\n\n"
                for log in logs[-10:]:
                    text += f"`{log.get('level', 'INFO')}` {log.get('message', '')[:100]}\n\n"
                await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            else:
                await update.message.reply_text("No logs available.", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error fetching logs: {e}", exc_info=True)
            await update.message.reply_text(f"Error: {str(e)}", reply_markup=reply_markup)
    
    def _resolve_backup_paths(self):
        from app.config import settings

        db_path = Path(settings.db_path)
        if not db_path.is_absolute():
            db_path = Path(os.getcwd()) / db_path

        data_dir = db_path.parent
        if not data_dir.exists():
            data_dir = Path("/opt/smite/panel/data")
        if not data_dir.exists():
            panel_root = Path(os.getcwd())
            if not (panel_root / "data").exists():
                for possible_root in [Path("/opt/smite"), Path(__file__).parent.parent.parent]:
                    if (possible_root / "data").exists():
                        panel_root = possible_root
                        break
            data_dir = panel_root / "data"

        panel_root = data_dir.parent if data_dir.exists() else Path("/opt/smite/panel")
        if not (panel_root / "certs").exists():
            panel_root = Path(os.getcwd())
            if not (panel_root / "certs").exists():
                for possible_root in [Path("/opt/smite"), Path(__file__).parent.parent.parent]:
                    if (possible_root / "certs").exists():
                        panel_root = possible_root
                        break

        return db_path, data_dir, panel_root

    def _backup_recipient_ids(self) -> List[int]:
        """Only positive numeric IDs are private Telegram chats; groups/channels are excluded"""
        recipients: List[int] = []
        for raw in self.admin_ids:
            try:
                chat_id = int(str(raw).strip())
            except (TypeError, ValueError):
                logger.warning(f"Skipping invalid Telegram backup recipient: {raw}")
                continue
            if chat_id <= 0:
                logger.warning(f"Skipping non-private Telegram backup recipient: {raw}")
                continue
            if chat_id not in recipients:
                recipients.append(chat_id)
        return recipients

    def _backup_passphrase_value(self) -> Optional[str]:
        passphrase = (self.backup_passphrase or "").strip()
        return passphrase if len(passphrase) >= 12 else None

    def _get_backup_lock(self) -> asyncio.Lock:
        if self._backup_lock is None:
            self._backup_lock = asyncio.Lock()
        return self._backup_lock

    async def create_backup(self) -> Optional[str]:
        """Serialize concurrent backups so snapshots never race each other."""
        async with self._get_backup_lock():
            return await self._create_backup_locked()

    async def _create_backup_locked(self) -> Optional[str]:
        """Create a backup archive.

        With a passphrase, this is a full encrypted snapshot (secrets included).
        Without one, it is a sanitized export: tokens, private keys, password
        hashes and environment secrets are removed before zipping.
        """
        await self.load_settings()
        passphrase = self._backup_passphrase_value()
        encrypted = passphrase is not None
        staging = Path(tempfile.mkdtemp(prefix="smite_backup_"))
        archive_path: Optional[Path] = None
        zip_path: Optional[Path] = None
        try:
            from app.config import settings

            db_path, data_dir, panel_root = self._resolve_backup_paths()

            if data_dir.exists():
                staged_data = staging / "data"
                staged_data.mkdir(parents=True, exist_ok=True)
                # SECRET_KEY material must never leave the server in an export.
                skip = {".secret_key"}
                if db_path.exists():
                    staged_db = staged_data / db_path.name
                    await asyncio.to_thread(_snapshot_sqlite_db, db_path, staged_db)
                    skip.update({db_path.name, db_path.name + "-wal", db_path.name + "-shm"})
                for item in data_dir.iterdir():
                    if item.name in skip:
                        continue
                    target = staged_data / item.name
                    try:
                        if item.is_dir():
                            shutil.copytree(item, target, dirs_exist_ok=True)
                        elif item.is_file():
                            shutil.copy2(item, target)
                    except OSError as copy_err:
                        logger.warning(f"Skipping unreadable data item {item}: {copy_err}")
                if not encrypted:
                    for snapshot in staged_data.glob("*.db"):
                        await asyncio.to_thread(_sanitize_backup_database, snapshot)
                logger.info(f"Backed up data from: {data_dir}")

            certs_dir = panel_root / "certs"
            if certs_dir.exists():
                shutil.copytree(certs_dir, staging / "certs", dirs_exist_ok=True)

            node_cert_path = Path(settings.node_cert_path)
            if not node_cert_path.is_absolute():
                node_cert_path = panel_root / node_cert_path
            if node_cert_path.exists():
                (staging / "node_certs").mkdir(exist_ok=True)
                shutil.copy2(node_cert_path, staging / "node_certs" / "ca.crt")

            server_cert_path = Path(settings.node_server_cert_path)
            if not server_cert_path.is_absolute():
                server_cert_path = panel_root / server_cert_path
            if server_cert_path.exists():
                (staging / "server_certs").mkdir(exist_ok=True)
                shutil.copy2(server_cert_path, staging / "server_certs" / "ca-server.crt")

            config_dir = Path("/app/config")
            env_locations = [
                config_dir / ".env",
                Path("/opt/smite/.env"),
                panel_root / ".env",
                Path(os.getcwd()) / ".env",
            ]
            env_file = next((path for path in env_locations if path.exists()), None)
            if env_file:
                try:
                    env_text = env_file.read_text(encoding="utf-8", errors="replace")
                    (staging / "env").write_text(
                        env_text if encrypted else _sanitize_env_text(env_text),
                        encoding="utf-8",
                    )
                    logger.info(f"Backed up {'full' if encrypted else 'sanitized'} .env from: {env_file}")
                except Exception as env_err:
                    logger.warning(f"Could not sanitize .env for backup: {env_err}")

            compose_locations = [
                config_dir / "docker-compose.yml",
                Path("/opt/smite/docker-compose.yml"),
                panel_root / "docker-compose.yml",
                Path(os.getcwd()) / "docker-compose.yml",
            ]
            compose_file = next((path for path in compose_locations if path.exists()), None)
            if compose_file:
                shutil.copy2(compose_file, staging / "docker-compose.yml")
                logger.info(f"Backed up docker-compose.yml from: {compose_file}")

            if settings.https_enabled and settings.panel_domain:
                nginx_dir = panel_root / "nginx"
                if nginx_dir.exists():
                    shutil.copytree(nginx_dir, staging / "nginx", dirs_exist_ok=True)

                letsencrypt_dir = Path("/etc/letsencrypt")
                if letsencrypt_dir.exists():
                    domain_dir = letsencrypt_dir / "live" / settings.panel_domain
                    if domain_dir.exists():
                        target = staging / "letsencrypt" / "live" / settings.panel_domain
                        target.mkdir(parents=True, exist_ok=True)
                        cert_files = ["fullchain.pem", "chain.pem", "cert.pem"]
                        if encrypted:
                            cert_files.append("privkey.pem")
                        for cert_file in cert_files:
                            cert_path = domain_dir / cert_file
                            if cert_path.exists():
                                shutil.copy2(cert_path, target / cert_file)

            if not encrypted:
                _strip_private_material(staging)

            manifest = "\n".join([
                "Smite panel backup",
                f"created_utc={datetime.now(timezone.utc).isoformat()}",
                f"mode={'encrypted-full' if encrypted else 'sanitized'}",
                "secrets=" + (
                    "included; archive is passphrase-encrypted (Fernet, PBKDF2-SHA256)"
                    if encrypted else "removed"
                ),
                "",
            ])
            (staging / "MANIFEST.txt").write_text(manifest, encoding="utf-8")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_name = f"smite_backup_{timestamp}_{uuid.uuid4().hex[:8]}"
            zip_path = Path(tempfile.gettempdir()) / f"{base_name}.zip"

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(staging):
                    for file in files:
                        file_path = Path(root) / file
                        zipf.write(file_path, file_path.relative_to(staging))

            if encrypted:
                archive_path = await asyncio.to_thread(encrypt_backup_archive, zip_path, passphrase)
            else:
                archive_path = zip_path

            return str(archive_path)
        except Exception as e:
            logger.error(f"Error creating backup: {e}", exc_info=True)
            for candidate in (archive_path, zip_path):
                if candidate and candidate.exists():
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
            return None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages from persistent keyboard"""
        try:
            if not self.is_admin(update.effective_user.id):
                return
            
            # Skip if user is in a conversation (let conversation handlers handle it)
            user_id = update.effective_user.id
            if user_id in self.user_states:
                return
            
            text = update.message.text
            if not text:
                return
            
            # Check if it's a keyboard button
            if self.t(user_id, "node_stats") in text:
                await self.cmd_nodes_callback(update.message)
            elif self.t(user_id, "tunnel_stats") in text:
                await self.cmd_tunnels_callback(update.message)
            elif self.t(user_id, "logs") in text:
                await self.cmd_logs(update, context)
            elif self.t(user_id, "backup") in text:
                await self.cmd_backup(update, context)
        except Exception as e:
            logger.error(f"Error handling text message: {e}", exc_info=True)
            try:
                user_id = update.effective_user.id
                reply_markup = self._get_keyboard(user_id)
                await update.message.reply_text("❌ Error processing request", reply_markup=reply_markup)
            except:
                pass
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries"""
        try:
            query = update.callback_query
            await query.answer()
            
            if not query.message:
                return
            
            if not self.is_admin(query.from_user.id):
                await query.edit_message_text(self.t(query.from_user.id, "access_denied"))
                return
        except Exception as e:
            logger.error(f"Error in handle_callback: {e}", exc_info=True)
            return
        
        data = query.data
        
        if data == "back_to_menu":
            if query.message:
                text = self.t(query.from_user.id, "welcome")
                await query.edit_message_text(text)
        elif data == "node_stats":
            await self.cmd_nodes_callback(query)
        elif data == "tunnel_stats":
            await self.cmd_tunnels_callback(query)
        elif data == "logs":
            await self.cmd_logs_callback(query)
        elif data == "cmd_nodes":
            await self.cmd_nodes_callback(query)
        elif data == "cmd_tunnels":
            await self.cmd_tunnels_callback(query)
        elif data == "cmd_backup":
            await self.cmd_backup_callback(query)
        elif data == "cmd_status":
            await self.cmd_status_callback(query)
    
    async def cmd_nodes_callback(self, message_or_query):
        """Handle nodes command from callback"""
        try:
            # Get user_id and message object
            if hasattr(message_or_query, 'from_user'):
                user_id = message_or_query.from_user.id
                message = message_or_query
            elif hasattr(message_or_query, 'message') and hasattr(message_or_query.message, 'from_user'):
                user_id = message_or_query.message.from_user.id
                message = message_or_query.message
            else:
                user_id = message_or_query.chat.id if hasattr(message_or_query, 'chat') else 0
                message = message_or_query
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Node))
                nodes = result.scalars().all()
                
                reply_markup = self._get_keyboard(user_id)
                
                if not nodes:
                    text = self.t(user_id, "no_nodes")
                    if hasattr(message, 'edit_message_text') and message:
                        await message.edit_message_text(text)
                    elif hasattr(message, 'reply_text'):
                        await message.reply_text(text, reply_markup=reply_markup)
                    return
                
                text = f"📊 {self.t(user_id, 'node_stats')}:\n\n"
                active = sum(1 for n in nodes if n.status == "active")
                text += f"Total: {len(nodes)}\n"
                text += f"Active: {active}\n\n"
                
                for node in nodes:
                    status = "🟢" if node.status == "active" else "🔴"
                    role = node.node_metadata.get("role", "unknown") if node.node_metadata else "unknown"
                    text += f"{status} {node.name} ({role})\n"
                    text += f"   ID: {node.id[:8]}...\n\n"
                
                if hasattr(message, 'edit_message_text') and message:
                    await message.edit_message_text(text)
                elif hasattr(message, 'reply_text'):
                    await message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in cmd_nodes_callback: {e}", exc_info=True)
            try:
                user_id = message_or_query.from_user.id if hasattr(message_or_query, 'from_user') else 0
                reply_markup = self._get_keyboard(user_id)
                if hasattr(message_or_query, 'reply_text'):
                    await message_or_query.reply_text("❌ Error loading nodes", reply_markup=reply_markup)
                elif hasattr(message_or_query, 'edit_message_text') and message_or_query:
                    await message_or_query.edit_message_text("❌ Error loading nodes")
                elif hasattr(message_or_query, 'message'):
                    await message_or_query.message.reply_text("❌ Error loading nodes", reply_markup=reply_markup)
            except:
                pass
    
    async def cmd_tunnels_callback(self, message_or_query):
        """Handle tunnels command from callback"""
        try:
            if hasattr(message_or_query, 'from_user'):
                user_id = message_or_query.from_user.id
            elif hasattr(message_or_query, 'message') and hasattr(message_or_query.message, 'from_user'):
                user_id = message_or_query.message.from_user.id
            else:
                user_id = message_or_query.chat.id if hasattr(message_or_query, 'chat') else 0
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Tunnel))
                tunnels = result.scalars().all()
                
                reply_markup = self._get_keyboard(user_id)
                
                if not tunnels:
                    text = self.t(user_id, "no_tunnels")
                    if hasattr(message_or_query, 'edit_message_text') and message_or_query:
                        await message_or_query.edit_message_text(text)
                    elif hasattr(message_or_query, 'reply_text'):
                        await message_or_query.reply_text(text, reply_markup=reply_markup)
                    else:
                        await message_or_query.message.reply_text(text, reply_markup=reply_markup)
                    return
                
                text = f"📊 {self.t(user_id, 'tunnel_stats')}:\n\n"
                active = sum(1 for t in tunnels if t.status == "active")
                text += f"Total: {len(tunnels)}\n"
                text += f"Active: {active}\n"
                text += f"Error: {len(tunnels) - active}\n\n"
                
                for tunnel in tunnels[:10]:
                    status = "🟢" if tunnel.status == "active" else "🔴"
                    text += f"{status} {tunnel.name} ({tunnel.core})\n"
                
                if len(tunnels) > 10:
                    text += f"\n... and {len(tunnels) - 10} more"
                
                if hasattr(message_or_query, 'edit_message_text') and message_or_query:
                    await message_or_query.edit_message_text(text)
                elif hasattr(message_or_query, 'reply_text'):
                    reply_markup = self._get_keyboard(user_id)
                    await message_or_query.reply_text(text, reply_markup=reply_markup)
                else:
                    reply_markup = self._get_keyboard(user_id)
                    await message_or_query.message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in cmd_tunnels_callback: {e}", exc_info=True)
            try:
                user_id = message_or_query.from_user.id if hasattr(message_or_query, 'from_user') else 0
                reply_markup = self._get_keyboard(user_id)
                if hasattr(message_or_query, 'reply_text'):
                    await message_or_query.reply_text("❌ Error loading tunnels", reply_markup=reply_markup)
                elif hasattr(message_or_query, 'edit_message_text') and message_or_query:
                    await message_or_query.edit_message_text("❌ Error loading tunnels")
                elif hasattr(message_or_query, 'message'):
                    await message_or_query.message.reply_text("❌ Error loading tunnels", reply_markup=reply_markup)
            except:
                pass
    
    async def cmd_status_callback(self, message_or_query):
        """Handle status command from callback"""
        try:
            if hasattr(message_or_query, 'from_user'):
                user_id = message_or_query.from_user.id
            elif hasattr(message_or_query, 'message') and hasattr(message_or_query.message, 'from_user'):
                user_id = message_or_query.message.from_user.id
            else:
                user_id = message_or_query.chat.id if hasattr(message_or_query, 'chat') else 0
            
            async with AsyncSessionLocal() as session:
                nodes_result = await session.execute(select(Node))
                nodes = nodes_result.scalars().all()
                
                tunnels_result = await session.execute(select(Tunnel))
                tunnels = tunnels_result.scalars().all()
                
                active_nodes = sum(1 for n in nodes if n.status == "active")
                active_tunnels = sum(1 for t in tunnels if t.status == "active")
                
                text = f"""📊 Panel Status:

🖥️ Nodes: {active_nodes}/{len(nodes)} active
🔗 Tunnels: {active_tunnels}/{len(tunnels)} active
"""
                
                if hasattr(message_or_query, 'edit_message_text') and message_or_query:
                    await message_or_query.edit_message_text(text)
                elif hasattr(message_or_query, 'reply_text'):
                    reply_markup = self._get_keyboard(user_id)
                    await message_or_query.reply_text(text, reply_markup=reply_markup)
                else:
                    reply_markup = self._get_keyboard(user_id)
                    await message_or_query.message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error in cmd_status_callback: {e}", exc_info=True)
            try:
                user_id = message_or_query.from_user.id if hasattr(message_or_query, 'from_user') else 0
                reply_markup = self._get_keyboard(user_id)
                if hasattr(message_or_query, 'reply_text'):
                    await message_or_query.reply_text("❌ Error loading status", reply_markup=reply_markup)
                elif hasattr(message_or_query, 'edit_message_text') and message_or_query:
                    await message_or_query.edit_message_text("❌ Error loading status")
                elif hasattr(message_or_query, 'message'):
                    await message_or_query.message.reply_text("❌ Error loading status", reply_markup=reply_markup)
            except:
                pass
    
    async def cmd_backup_callback(self, query):
        """Handle backup command from callback"""
        user_id = query.from_user.id
        if not query.message:
            return
        
        await query.edit_message_text("📦 Creating backup...")
        
        backup_path = None
        try:
            backup_path = await self.create_backup()
            if not backup_path:
                await query.edit_message_text("❌ Failed to create backup")
                return
            filename = Path(backup_path).name
            mode_label = (
                "Encrypted full backup"
                if backup_path.endswith(".enc")
                else "Sanitized export (secrets removed)"
            )
            with open(backup_path, "rb") as f:
                await query.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename=filename,
                    caption=f"✅ {mode_label} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
            await query.edit_message_text("✅ Backup sent to your private chat.")
        except Exception as e:
            logger.error(f"Error creating backup: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Error creating backup: {str(e)}")
        finally:
            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)
    
    async def cmd_logs_callback(self, query):
        """Handle logs command from callback"""
        try:
            from app.routers.logs import get_recent_logs
            logs = get_recent_logs(20)
            if logs:
                text = "📋 Recent Logs:\n\n"
                for log in logs[-10:]:
                    text += f"`{log.get('level', 'INFO')}` {log.get('message', '')[:100]}\n\n"
                await query.edit_message_text(text, parse_mode="Markdown")
            else:
                await query.edit_message_text("No logs available.")
        except Exception as e:
            logger.error(f"Error fetching logs: {e}", exc_info=True)
            await query.edit_message_text(f"Error: {str(e)}")


telegram_bot = TelegramBot()
