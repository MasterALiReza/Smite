"""Application configuration"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )

    panel_port: int = 8000
    panel_host: str = "0.0.0.0"
    panel_domain: str = ""
    https_enabled: bool = False
    https_cert_path: str = "./certs/server.crt"
    https_key_path: str = "./certs/server.key"
    # Swagger/OpenAPI docs are disabled by default in production for security.
    # Set DOCS_ENABLED=true explicitly if you need /docs.
    docs_enabled: bool = False

    # Comma-separated list of allowed CORS origins. Use "*" for development only.
    cors_origins: str = "*"

    db_type: Literal["sqlite"] = "sqlite"
    db_path: str = "./data/smite.db"
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "smite"
    db_user: str = "smite"
    db_password: str = "changeme"
    
    node_port: int = 4443
    node_cert_path: str = "./certs/ca.crt"
    node_key_path: str = "./certs/ca.key"
    node_server_cert_path: str = "./certs/ca-server.crt"
    node_server_key_path: str = "./certs/ca-server.key"

    # Fail-closed switch that suspends automatic fleet mutations (tunnel reapply,
    # agent token provisioning, auto-reset, SSL renewal). Kept outside the
    # database so restoring a database snapshot cannot rewind it.
    mutation_guard_path: str = "./certs/.mutation-guard.json"

    # Optional shared secret for panel->node agent API authentication.
    # When set on the node (NODE_API_TOKEN), the panel must send it as X-Node-Token.
    # Leave empty for backward compatibility with older deployments.
    node_api_token: str = ""

    # Comma-separated list of addresses the panel treats as "local/collocated"
    # when routing node communication. Add your own collocated server IPs here
    # (never commit real IPs to the repository).
    panel_local_ips: str = "127.0.0.1,localhost,::1"
    # Legacy alias kept for backward compatibility with SMITE_LOCAL_IPS docs
    smite_local_ips: str = ""
    
    secret_key: str = "changeme-secret-key-change-in-production"
    access_token_expire_minutes: int = 1440  # 24 hours default, configurable via ACCESS_TOKEN_EXPIRE_MINUTES


settings = Settings()

# Honor legacy SMITE_LOCAL_IPS when PANEL_LOCAL_IPS is not explicitly set
if settings.smite_local_ips and not os.environ.get("PANEL_LOCAL_IPS"):
    settings.panel_local_ips = settings.smite_local_ips

_KNOWN_WEAK_DEFAULTS = {"changeme-secret-key-change-in-production", ""}
SECRET_KEY_AUTO_GENERATED = False
SECRET_KEY_FILENAME = ".secret_key"

if settings.secret_key in _KNOWN_WEAK_DEFAULTS:
    import logging
    import secrets
    from pathlib import Path
    logger = logging.getLogger(__name__)
    
    # Auto-generate a cryptographically secure 256-bit secret key
    auto_secret = secrets.token_hex(32)
    settings.secret_key = auto_secret
    SECRET_KEY_AUTO_GENERATED = True
    logger.warning(
        "SECURITY NOTICE: Default or empty SECRET_KEY detected. Automatically generated a cryptographically secure 256-bit SECRET_KEY."
    )
    
    # Persist the newly generated key to .env if possible
    candidate_env_paths = [
        Path(".env"),
        Path("/opt/smite/.env"),
        Path(__file__).resolve().parent.parent.parent / ".env"
    ]
    for env_path in candidate_env_paths:
        try:
            if env_path.exists():
                lines = env_path.read_text(encoding="utf-8").splitlines()
                new_lines = []
                replaced = False
                for line in lines:
                    if line.strip().startswith("SECRET_KEY="):
                        new_lines.append(f"SECRET_KEY={auto_secret}")
                        replaced = True
                    else:
                        new_lines.append(line)
                if not replaced:
                    new_lines.append(f"SECRET_KEY={auto_secret}")
                env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                logger.info(f"Persisted secure SECRET_KEY to {env_path}")
                break
        except Exception as err:
            logger.debug(f"Could not persist auto-generated SECRET_KEY to {env_path}: {err}")


def _secret_key_file_path():
    from pathlib import Path

    db_file = Path(settings.db_path).expanduser()
    if not db_file.is_absolute():
        db_file = Path(os.getcwd()) / db_file
    return db_file.parent / SECRET_KEY_FILENAME


def ensure_secret_key_durability():
    """Make an auto-generated SECRET_KEY survive restarts.

    Docker mounts .env read-only, so writing it back is not possible there.
    The data directory is a writable volume; a 0600 key file next to the
    database keeps issued tokens valid across restarts.
    """
    global SECRET_KEY_AUTO_GENERATED
    if not SECRET_KEY_AUTO_GENERATED:
        return None

    import logging

    logger = logging.getLogger(__name__)
    key_path = _secret_key_file_path()
    try:
        if key_path.exists():
            stored = key_path.read_text(encoding="utf-8").strip()
            if stored and stored not in _KNOWN_WEAK_DEFAULTS and len(stored) >= 32:
                settings.secret_key = stored
                SECRET_KEY_AUTO_GENERATED = False
                logger.info("Loaded persisted SECRET_KEY from %s", key_path)
                return key_path

        key_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(settings.secret_key + "\n")
        try:
            os.chmod(str(key_path), 0o600)
        except OSError:
            pass
        logger.warning("Persisted auto-generated SECRET_KEY to %s (mode 0600)", key_path)
        return key_path
    except Exception as err:
        logger.warning("Could not persist SECRET_KEY to %s: %s", key_path, err)
        return None
