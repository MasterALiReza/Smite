"""Application configuration"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore"
    )

    node_api_port: int = 8888
    node_name: str = "node-1"
    node_role: str = "iran"  # "iran" or "foreign"
    node_ip: str = ""  # Explicit node IP override if set in .env
    
    # Optional shared secret for the node agent API. When set, all /api/agent/*
    # requests must include the matching X-Node-Token header (the panel sends it
    # when NODE_API_TOKEN is configured on the panel side). Leave empty for
    # backward compatibility with older panels.
    node_api_token: str = ""

    # Optional manual recovery secret. Normal enrollment uses a generated,
    # one-time proof that expires and is consumed after the first token install.
    node_bootstrap_secret: str = ""
    node_bootstrap_proof_ttl_seconds: int = 900

    # "", "permissive" or "enforce". Empty means the durable enforcement flag
    # written by the panel decides; "enforce" locks the API even without a token.
    agent_auth_mode: str = ""
    
    panel_ca_path: str = "/etc/smite-node/ca.crt"
    panel_address: str = "panel.example.com:443"
    panel_api_port: int = 8000


settings = Settings()


