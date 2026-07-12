"""Application configuration"""
from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    panel_port: int = 8000
    panel_host: str = "0.0.0.0"
    panel_domain: str = ""
    https_enabled: bool = False
    https_cert_path: str = "./certs/server.crt"
    https_key_path: str = "./certs/server.key"
    docs_enabled: bool = True
    
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
    
    secret_key: str = "changeme-secret-key-change-in-production"
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()

