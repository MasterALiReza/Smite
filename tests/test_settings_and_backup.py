"""Focused tests for settings runtime changes and backup sanitization/encryption."""

import json
import sqlite3
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.routers.settings as settings_router
from app.routers.settings import (
    FrpSettings,
    SettingsUpdate,
    TelegramSettings,
    TunnelSettings,
    MASKED_SECRET,
)
from app.telegram_bot import (
    TelegramBot,
    BACKUP_MAGIC,
    decrypt_backup_archive,
    encrypt_backup_archive,
    _sanitize_env_text,
)


class _Row:
    def __init__(self, key, value):
        self.key = key
        self.value = value
        self.updated_at = None


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeDB:
    """Minimal AsyncSession stand-in that only understands Settings lookups."""

    def __init__(self, rows=None):
        self.rows = rows or {}
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        params = stmt.compile().params
        key = next((value for name, value in params.items() if name.startswith("key")), None)
        return _Result(self.rows.get(key))

    def add(self, row):
        self.added.append(row)
        self.rows[row.key] = row

    async def commit(self):
        self.committed = True


@pytest.fixture
def runtime_mocks(monkeypatch):
    frp = SimpleNamespace(start=AsyncMock(return_value=True), stop=AsyncMock())
    telegram = SimpleNamespace(
        start=AsyncMock(return_value=True),
        stop=AsyncMock(),
        start_backup_task=AsyncMock(),
    )
    tunnel = SimpleNamespace(apply_settings=AsyncMock())

    monkeypatch.setattr("app.frp_comm_manager.frp_comm_manager", frp)
    monkeypatch.setattr("app.telegram_bot.telegram_bot", telegram)
    monkeypatch.setattr("app.tunnel_reapply_manager.tunnel_reapply_manager", tunnel)
    return frp, telegram, tunnel


@pytest.mark.asyncio
async def test_settings_save_preserves_mask_and_skips_unchanged_runtime(runtime_mocks):
    frp, telegram, tunnel = runtime_mocks
    frp_row = _Row("frp", {"enabled": True, "port": 7000, "token": "old-frp-token"})
    telegram_row = _Row("telegram", {
        "enabled": True,
        "bot_token": "old-bot-token",
        "admin_ids": ["123"],
        "backup_enabled": False,
        "backup_interval": 60,
        "backup_interval_unit": "minutes",
    })
    tunnel_row = _Row("tunnel", {
        "auto_reapply_enabled": False,
        "auto_reapply_interval": 60,
        "auto_reapply_interval_unit": "minutes",
    })
    db = FakeDB({"frp": frp_row, "telegram": telegram_row, "tunnel": tunnel_row})

    payload = SettingsUpdate(
        frp=FrpSettings(enabled=True, port=7000, token=MASKED_SECRET),
        telegram=TelegramSettings(
            enabled=True,
            bot_token=MASKED_SECRET,
            admin_ids=["123"],
            backup_enabled=False,
            backup_interval=60,
            backup_interval_unit="minutes",
        ),
        tunnel=TunnelSettings(
            auto_reapply_enabled=False,
            auto_reapply_interval=60,
            auto_reapply_interval_unit="minutes",
        ),
    )

    response = await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert response["status"] == "success"
    assert db.committed is True
    assert frp_row.value["token"] == "old-frp-token"
    assert telegram_row.value["bot_token"] == "old-bot-token"
    frp.start.assert_not_awaited()
    frp.stop.assert_not_awaited()
    telegram.start.assert_not_awaited()
    telegram.start_backup_task.assert_not_awaited()
    tunnel.apply_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_frp_port_change_restarts_only_frp(runtime_mocks):
    frp, telegram, tunnel = runtime_mocks
    frp_row = _Row("frp", {"enabled": True, "port": 7000, "token": "old-frp-token"})
    db = FakeDB({"frp": frp_row})

    payload = SettingsUpdate(frp=FrpSettings(enabled=True, port=7001, token=MASKED_SECRET))
    await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert frp_row.value["port"] == 7001
    frp.stop.assert_awaited_once()
    frp.start.assert_awaited_once_with(7001, "old-frp-token")
    telegram.start.assert_not_awaited()
    tunnel.apply_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_backup_passphrase_short_is_rejected_before_commit(runtime_mocks):
    _frp, telegram, _tunnel = runtime_mocks
    telegram_row = _Row("telegram", {"enabled": True, "bot_token": "old-bot-token", "admin_ids": ["123"]})
    db = FakeDB({"telegram": telegram_row})

    payload = SettingsUpdate(telegram=TelegramSettings(
        enabled=True,
        bot_token=MASKED_SECRET,
        admin_ids=["123"],
        backup_passphrase="short",
    ))

    with pytest.raises(HTTPException) as exc:
        await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert exc.value.status_code == 400
    assert db.committed is False
    assert telegram_row.value["bot_token"] == "old-bot-token"


@pytest.mark.asyncio
async def test_settings_telegram_backup_change_applies_without_restarting_bot(runtime_mocks):
    _frp, telegram, _tunnel = runtime_mocks
    telegram_row = _Row("telegram", {
        "enabled": True,
        "bot_token": "old-bot-token",
        "admin_ids": ["123"],
        "backup_enabled": False,
        "backup_interval": 60,
        "backup_interval_unit": "minutes",
    })
    db = FakeDB({"telegram": telegram_row})

    payload = SettingsUpdate(telegram=TelegramSettings(
        enabled=True,
        bot_token=MASKED_SECRET,
        admin_ids=["123"],
        backup_enabled=True,
        backup_interval=2,
        backup_interval_unit="hours",
    ))

    await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert telegram_row.value["backup_enabled"] is True
    telegram.start.assert_not_awaited()
    telegram.start_backup_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_settings_tunnel_change_applies_automation(runtime_mocks):
    _frp, _telegram, tunnel = runtime_mocks
    tunnel_row = _Row("tunnel", {
        "auto_reapply_enabled": False,
        "auto_reapply_interval": 60,
        "auto_reapply_interval_unit": "minutes",
    })
    db = FakeDB({"tunnel": tunnel_row})

    payload = SettingsUpdate(tunnel=TunnelSettings(
        auto_reapply_enabled=True,
        auto_reapply_interval=15,
        auto_reapply_interval_unit="minutes",
    ))

    await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert tunnel_row.value["auto_reapply_enabled"] is True
    tunnel.apply_settings.assert_awaited_once()


def _create_source_db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
            CREATE TABLE settings (id INTEGER PRIMARY KEY, key TEXT, value TEXT);
            CREATE TABLE nodes (id INTEGER PRIMARY KEY, metadata TEXT);
            CREATE TABLE tunnels (id INTEGER PRIMARY KEY, spec TEXT);
            CREATE TABLE admins (id INTEGER PRIMARY KEY, password_hash TEXT);
        """)
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("telegram", json.dumps({"enabled": True, "bot_token": "super-secret-bot-token"})),
        )
        conn.execute(
            "INSERT INTO nodes (metadata) VALUES (?)",
            (json.dumps({"agent_token": "node-agent-secret", "region": "eu"}),),
        )
        conn.execute(
            "INSERT INTO tunnels (spec) VALUES (?)",
            (json.dumps({"token": "tunnel-secret", "tls_key_pem": "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----"}),),
        )
        conn.execute("INSERT INTO admins (password_hash) VALUES ('$2b$12$realpasswordhash')")
        conn.commit()
    finally:
        conn.close()


def _configure_backup_paths(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    _create_source_db(data_dir / "smite.db")
    certs_dir = tmp_path / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)
    (certs_dir / "ca.key").write_text("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n", encoding="utf-8")
    (certs_dir / "ca.crt").write_text("PUBLIC CERTIFICATE\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "SECRET_KEY=super-env-secret\nBOT_TOKEN=env-bot-secret\nPANEL_PORT=8000\n",
        encoding="utf-8",
    )

    from app.config import settings as panel_settings

    monkeypatch.setattr(panel_settings, "db_path", str(data_dir / "smite.db"))
    monkeypatch.setattr(panel_settings, "node_cert_path", str(tmp_path / "missing-ca.crt"))
    monkeypatch.setattr(panel_settings, "node_server_cert_path", str(tmp_path / "missing-server.crt"))
    monkeypatch.setattr(panel_settings, "https_enabled", False)
    return data_dir


def _new_backup_bot(monkeypatch, passphrase=None):
    bot = TelegramBot()
    bot.backup_passphrase = passphrase
    monkeypatch.setattr(bot, "load_settings", AsyncMock())
    return bot


def _zip_member_bytes(zip_path: Path, member: str) -> bytes:
    with zipfile.ZipFile(zip_path) as archive:
        return archive.read(member)


@pytest.mark.asyncio
async def test_sanitized_backup_removes_secrets_and_private_keys(tmp_path, monkeypatch):
    _configure_backup_paths(monkeypatch, tmp_path)
    bot = _new_backup_bot(monkeypatch, passphrase=None)

    backup_path = await bot.create_backup()
    assert backup_path is not None
    archive = Path(backup_path)
    try:
        assert archive.suffix == ".zip"
        names = set(zipfile.ZipFile(archive).namelist())
        assert "env" in names
        assert "certs/ca.crt" in names
        assert "certs/ca.key" not in names
        assert "data/smite.db" in names

        env_text = _zip_member_bytes(archive, "env").decode("utf-8")
        assert "super-env-secret" not in env_text
        assert "env-bot-secret" not in env_text
        assert "REDACTED_IN_SANITIZED_BACKUP" in env_text
        assert "PANEL_PORT=8000" in env_text

        staged_db = tmp_path / "staged.db"
        staged_db.write_bytes(_zip_member_bytes(archive, "data/smite.db"))
        conn = sqlite3.connect(str(staged_db))
        try:
            settings_value = conn.execute("SELECT value FROM settings WHERE key='telegram'").fetchone()[0]
            node_meta = conn.execute("SELECT metadata FROM nodes").fetchone()[0]
            tunnel_spec = conn.execute("SELECT spec FROM tunnels").fetchone()[0]
            admin_hash = conn.execute("SELECT password_hash FROM admins").fetchone()[0]
        finally:
            conn.close()
        combined = f"{settings_value}{node_meta}{tunnel_spec}{admin_hash}"
        assert "super-secret-bot-token" not in combined
        assert "node-agent-secret" not in combined
        assert "tunnel-secret" not in combined
        assert "realpasswordhash" not in combined
        assert "REDACTED_IN_SANITIZED_BACKUP" in combined
        assert '"region": "eu"' in node_meta
    finally:
        archive.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_encrypted_backup_roundtrip_and_wrong_passphrase(tmp_path, monkeypatch):
    _configure_backup_paths(monkeypatch, tmp_path)
    passphrase = "correct horse battery staple"
    bot = _new_backup_bot(monkeypatch, passphrase=passphrase)

    backup_path = await bot.create_backup()
    assert backup_path is not None
    encrypted = Path(backup_path)
    restored_zip = tmp_path / "restored.zip"
    wrong_zip = tmp_path / "wrong.zip"
    try:
        assert encrypted.name.endswith(".zip.enc")
        assert encrypted.read_bytes().startswith(BACKUP_MAGIC)

        decrypt_backup_archive(encrypted, passphrase, restored_zip)
        assert "super-secret-bot-token" in _zip_member_bytes(restored_zip, "data/smite.db").decode("utf-8", "ignore")
        env_text = _zip_member_bytes(restored_zip, "env").decode("utf-8")
        assert "SECRET_KEY=super-env-secret" in env_text
        with zipfile.ZipFile(restored_zip) as archive:
            assert "certs/ca.key" in archive.namelist()

        with pytest.raises(ValueError, match="Invalid backup passphrase"):
            decrypt_backup_archive(encrypted, "wrong passphrase value", wrong_zip)
    finally:
        encrypted.unlink(missing_ok=True)
        restored_zip.unlink(missing_ok=True)
        wrong_zip.unlink(missing_ok=True)


def test_sanitize_env_text_preserves_non_secrets():
    text = "PANEL_PORT=8000\nSECRET_KEY=abc\nADMIN_PASSWORD=def\nCORS_ORIGINS=*\n"
    sanitized = _sanitize_env_text(text)
    assert "PANEL_PORT=8000" in sanitized
    assert "CORS_ORIGINS=*" in sanitized
    assert "SECRET_KEY=abc" not in sanitized
    assert "ADMIN_PASSWORD=def" not in sanitized
    assert sanitized.count("REDACTED_IN_SANITIZED_BACKUP") == 2


def test_encrypt_backup_archive_replaces_plain_zip(tmp_path):
    zip_path = tmp_path / "sample.zip"
    zip_path.write_bytes(b"PK\x03\x04not-a-real-zip")
    encrypted = encrypt_backup_archive(zip_path, "a-long-enough-passphrase")
    assert not zip_path.exists()
    assert encrypted.name.endswith(".zip.enc")
    assert encrypted.read_bytes().startswith(BACKUP_MAGIC)


@pytest.mark.asyncio
async def test_settings_revision_conflict_returns_409(runtime_mocks):
    meta_row = _Row("_meta", {"revision": 3})
    db = FakeDB({"_meta": meta_row})

    payload = SettingsUpdate(revision=2, tunnel=TunnelSettings(auto_reapply_enabled=True))

    with pytest.raises(HTTPException) as exc:
        await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert exc.value.status_code == 409
    assert db.committed is False
    assert meta_row.value["revision"] == 3


@pytest.mark.asyncio
async def test_settings_revision_bumps_on_save(runtime_mocks):
    meta_row = _Row("_meta", {"revision": 3})
    tunnel_row = _Row("tunnel", {
        "auto_reapply_enabled": False,
        "auto_reapply_interval": 60,
        "auto_reapply_interval_unit": "minutes",
    })
    db = FakeDB({"_meta": meta_row, "tunnel": tunnel_row})

    payload = SettingsUpdate(
        revision=3,
        tunnel=TunnelSettings(auto_reapply_enabled=True),
    )
    response = await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert response["status"] == "success"
    assert response["revision"] == 4
    assert meta_row.value["revision"] == 4
    assert response["sections"]["tunnel"]["runtime"] == "applied"


@pytest.mark.asyncio
async def test_settings_partial_payload_leaves_other_sections_untouched(runtime_mocks):
    frp, telegram, tunnel = runtime_mocks
    frp_row = _Row("frp", {"enabled": True, "port": 7000, "token": "keep-me"})
    telegram_row = _Row("telegram", {"enabled": True, "bot_token": "keep-me-too", "admin_ids": ["123"]})
    tunnel_row = _Row("tunnel", {
        "auto_reapply_enabled": False,
        "auto_reapply_interval": 60,
        "auto_reapply_interval_unit": "minutes",
    })
    db = FakeDB({"frp": frp_row, "telegram": telegram_row, "tunnel": tunnel_row})

    payload = SettingsUpdate(tunnel=TunnelSettings(auto_reapply_enabled=True))
    response = await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert frp_row.value == {"enabled": True, "port": 7000, "token": "keep-me"}
    assert telegram_row.value == {"enabled": True, "bot_token": "keep-me-too", "admin_ids": ["123"]}
    assert response["sections"]["tunnel"]["runtime"] == "applied"
    assert "frp" not in response["sections"]
    assert "telegram" not in response["sections"]
    frp.start.assert_not_awaited()
    frp.stop.assert_not_awaited()
    telegram.start.assert_not_awaited()
    telegram.stop.assert_not_awaited()


def test_telegram_admin_ids_must_be_positive_numeric():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TelegramSettings(admin_ids=["@someone"])
    with pytest.raises(ValidationError):
        TelegramSettings(admin_ids=["-100123456"])
    with pytest.raises(ValidationError):
        TelegramSettings(admin_ids=["0"])
    with pytest.raises(ValidationError):
        TelegramSettings(admin_ids=[""])

    parsed = TelegramSettings(admin_ids=[" 123 ", "123", "456"])
    assert parsed.admin_ids == ["123", "456"]


def test_settings_token_validation_rejects_control_characters():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FrpSettings(token="bad\ntoken")
    with pytest.raises(ValidationError):
        TelegramSettings(bot_token="123456:abc def")
    with pytest.raises(ValidationError):
        TelegramSettings(bot_token="missing-colon")

    assert FrpSettings(token=MASKED_SECRET).token == MASKED_SECRET
    assert TelegramSettings(bot_token=MASKED_SECRET).bot_token == MASKED_SECRET


@pytest.mark.asyncio
async def test_frp_start_failure_restores_previous_settings(monkeypatch):
    frp = SimpleNamespace(
        start=AsyncMock(side_effect=[False, True]),
        stop=AsyncMock(),
        is_running=lambda: True,
    )
    monkeypatch.setattr("app.frp_comm_manager.frp_comm_manager", frp)

    frp_row = _Row("frp", {"enabled": True, "port": 7000, "token": "old-token"})
    db = FakeDB({"frp": frp_row})

    payload = SettingsUpdate(frp=FrpSettings(enabled=True, port=7001, token=MASKED_SECRET))
    response = await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert response["status"] == "partial"
    assert response["sections"]["frp"]["runtime"] == "failed"
    assert frp_row.value["port"] == 7000
    assert frp_row.value["token"] == "old-token"
    assert frp.start.await_count == 2
    assert frp.start.await_args_list[0].args == (7001, "old-token")
    assert frp.start.await_args_list[1].args == (7000, "old-token")


@pytest.mark.asyncio
async def test_telegram_token_failure_restores_previous_settings(monkeypatch):
    frp = SimpleNamespace(start=AsyncMock(return_value=True), stop=AsyncMock(), is_running=lambda: False)
    telegram = SimpleNamespace(
        start=AsyncMock(side_effect=[False, True]),
        stop=AsyncMock(),
        start_backup_task=AsyncMock(),
        is_running=lambda: True,
        last_error="Invalid bot token",
    )
    monkeypatch.setattr("app.frp_comm_manager.frp_comm_manager", frp)
    monkeypatch.setattr("app.telegram_bot.telegram_bot", telegram)

    telegram_row = _Row("telegram", {
        "enabled": True,
        "bot_token": "old-bot-token",
        "admin_ids": ["123"],
    })
    db = FakeDB({"telegram": telegram_row})

    payload = SettingsUpdate(telegram=TelegramSettings(
        enabled=True,
        bot_token="123456:new-token",
        admin_ids=["123"],
    ))
    response = await settings_router.update_settings(payload, None, db, SimpleNamespace(username="admin"))

    assert response["status"] == "partial"
    assert response["sections"]["telegram"]["runtime"] == "failed"
    assert telegram_row.value["bot_token"] == "old-bot-token"
    assert telegram.start.await_count == 2


def test_telegram_runtime_status_reports_state_without_token():
    bot = TelegramBot()
    bot.bot_token = "123456:super-secret"
    bot.enabled = True
    bot.last_error = bot._redact("Unauthorized for 123456:super-secret")

    status = bot.runtime_status()
    assert status["running"] is False
    assert status["polling"] is False
    assert status["desired"] is True
    assert "super-secret" not in status["last_error"]


@pytest.mark.asyncio
async def test_frp_comm_manager_restarts_only_when_effective_config_changes(tmp_path, monkeypatch):
    from app.frp_comm_manager import FrpCommManager

    manager = FrpCommManager()
    manager.config_dir = tmp_path
    manager.config_file = tmp_path / "frps_comm.yaml"
    manager.log_file = tmp_path / "frps_comm.log"
    monkeypatch.setattr(manager, "_resolve_binary_path", lambda: Path("/bin/frps"))
    monkeypatch.setattr("app.frp_comm_manager.asyncio.sleep", AsyncMock())

    class DummyProc:
        pid = 4242
        returncode = None

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    spawn = AsyncMock(side_effect=lambda *args, **kwargs: DummyProc())
    monkeypatch.setattr("app.frp_comm_manager.asyncio.create_subprocess_exec", spawn)

    assert await manager.start(7000, "token-a") is True
    assert spawn.await_count == 1

    # Same effective configuration: idempotent, no new process.
    assert await manager.start(7000, "token-a") is True
    assert spawn.await_count == 1

    # Port change: the manager must actually restart instead of reporting success.
    assert await manager.start(7001, "token-a") is True
    assert spawn.await_count == 2
    assert manager.port == 7001

    await manager.stop()
    assert manager.is_running() is False


@pytest.mark.asyncio
async def test_sanitized_backup_never_includes_persisted_secret_key(tmp_path, monkeypatch):
    data_dir = _configure_backup_paths(monkeypatch, tmp_path)
    (data_dir / ".secret_key").write_text("persisted-signing-key\n", encoding="utf-8")
    bot = _new_backup_bot(monkeypatch, passphrase=None)

    backup_path = await bot.create_backup()
    assert backup_path is not None
    try:
        names = set(zipfile.ZipFile(backup_path).namelist())
        assert "data/.secret_key" not in names
    finally:
        Path(backup_path).unlink(missing_ok=True)


def test_secret_key_persists_and_reloads_from_data_dir(tmp_path, monkeypatch):
    import app.config as cfg

    monkeypatch.setattr(cfg.settings, "db_path", str(tmp_path / "data" / "smite.db"))
    monkeypatch.setattr(cfg, "SECRET_KEY_AUTO_GENERATED", True)
    original_key = cfg.settings.secret_key

    key_path = cfg.ensure_secret_key_durability()
    assert key_path is not None and key_path.exists()

    # A later boot generates a different ephemeral key; the stored one must win.
    monkeypatch.setattr(cfg.settings, "secret_key", "ephemeral-key-" + "x" * 48)
    monkeypatch.setattr(cfg, "SECRET_KEY_AUTO_GENERATED", True)
    cfg.ensure_secret_key_durability()

    assert cfg.settings.secret_key == original_key.strip()


def test_reapply_manager_restart_budget_and_counters(monkeypatch):
    import app.tunnel_reapply_manager as reapply

    manager = reapply.TunnelReapplyManager()
    manager._record_failure("t1", "tunnel-one", "first failure")
    manager._record_failure("t1", "tunnel-one", "second failure")

    status = manager.runtime_status()
    assert status["last_failure_at"] is not None
    assert status["pending_retries"] == 1
    assert manager._failures["t1"] == 2

    manager._record_success("t1")
    assert manager.restarts_total == 1
    assert manager._failures.get("t1") is None
    assert manager._retry_at.get("t1") is None


@pytest.mark.asyncio
async def test_concurrent_backups_are_serialized(monkeypatch):
    import asyncio

    bot = TelegramBot()
    active = 0
    peak = 0

    async def fake_backup():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "backup.zip"

    monkeypatch.setattr(bot, "_create_backup_locked", fake_backup)
    results = await asyncio.gather(bot.create_backup(), bot.create_backup())

    assert results == ["backup.zip", "backup.zip"]
    assert peak == 1, "Two backups must never snapshot the database at the same time"
