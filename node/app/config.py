"""Application configuration"""
from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    node_api_port: int = 8888
    node_name: str = "node-1"
    node_role: str = "iran"  # "iran" or "foreign"
    
    # Optional shared secret for the node agent API. When set, all /api/agent/*
    # requests must include the matching X-Node-Token header (the panel sends it
    # when NODE_API_TOKEN is configured on the panel side). Leave empty for
    # backward compatibility with older panels.
    node_api_token: str = ""
    
    panel_ca_path: str = "/etc/smite-node/ca.crt"
    panel_address: str = "panel.example.com:443"
    panel_api_port: int = 8000
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


settings = Settings()


