"""Application configuration"""
import os
from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
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
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

# Honor legacy SMITE_LOCAL_IPS when PANEL_LOCAL_IPS is not explicitly set
if settings.smite_local_ips and not os.environ.get("PANEL_LOCAL_IPS"):
    settings.panel_local_ips = settings.smite_local_ips

_KNOWN_WEAK_DEFAULTS = {"changeme-secret-key-change-in-production", ""}
if settings.secret_key in _KNOWN_WEAK_DEFAULTS:
    import logging
    import secrets
    from pathlib import Path
    logger = logging.getLogger(__name__)
    
    # Auto-generate a cryptographically secure 256-bit secret key
    auto_secret = secrets.token_hex(32)
    settings.secret_key = auto_secret
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
