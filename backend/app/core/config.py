"""Application settings, loaded from environment / .env."""
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "safety_app"
    db_password: str = ""
    db_name: str = "safety_db"
    test_db_name: str = "safety_test"
    random_seed: int = 20261022

    def url(self, database: str | None = None) -> str:
        db = database or self.db_name
        return (
            f"postgresql+psycopg://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
