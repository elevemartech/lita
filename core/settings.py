from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    eleve_api_url: str = "http://localhost:8000"
    openai_api_key: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    redis_url: str = "redis://localhost:6379/1"
    database_url: str = "postgresql+asyncpg://nicodemus:nicodemus@localhost:5432/nicodemus_db"
    file_storage_ttl: int = 900
    file_storage_dir: str = "/tmp"
    environment: str = "development"
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: object) -> object:
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [o.strip() for o in v.split(",") if o.strip()]
        return v


settings = Settings()
