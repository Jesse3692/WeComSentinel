import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/monitor.db"
    secret_key: str = ""
    payload_key: str = ""
    public_origin: str = "http://localhost:8000"
    cookie_secure: bool = False
    worker_enabled: bool = True
    media_dir: str = "./data/media"
    demo_enabled: bool = False
    demo_password: str = ""
    ding_corp_id: str = ""
    ding_client_id: str = ""
    ding_client_secret: str = ""
    ding_agent_id: str = ""

    def validate_runtime(self):
        if len(self.secret_key) < 32 or self.secret_key.startswith("replace-"):
            raise RuntimeError("Set SECRET_KEY to a random value of at least 32 characters")
        from cryptography.fernet import Fernet
        Fernet(self.payload_key.encode())
        if self.app_env == "production":
            if self.demo_enabled or not self.cookie_secure or not self.public_origin.startswith("https://"):
                raise RuntimeError("Production requires HTTPS, secure cookies and disabled demo")
        if self.demo_enabled and len(self.demo_password) < 12:
            raise RuntimeError("Demo requires a password of at least 12 characters")

    def credential(self, ref: str) -> dict:
        values = {**dotenv_values(".env"), **os.environ}
        prefix = f"WECOM_{ref.upper()}_"
        return {k: values.get(prefix + suffix, "") for k, suffix in (
            ("secret", "SECRET"), ("token", "TOKEN"), ("aes_key", "AES_KEY"))}

    def prepare_dirs(self):
        Path(self.media_dir).mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite+aiosqlite:///"):
            path = self.database_url.removeprefix("sqlite+aiosqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings():
    return Settings()
