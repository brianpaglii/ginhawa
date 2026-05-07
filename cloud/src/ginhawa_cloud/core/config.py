from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ENV: str = "development"

    # Origins permitted by CORS for the BHW portal. The browser sends a
    # CORS preflight (OPTIONS) for every cross-origin POST with a
    # Content-Type that isn't a CORS-safe value; without an explicit
    # allowlist + middleware FastAPI's router replies 405 to the
    # OPTIONS and the actual login never reaches the handler. Defaults
    # cover both Vite dev-server origins (localhost / 127.0.0.1 on
    # :5173); production deployments must set this explicitly via env
    # to the deployed portal origin. Wildcard ``*`` is intentionally
    # NOT supported — even though we don't use cookies, leaving an
    # open CORS policy on a network-exposed kiosk-data API has no
    # upside.
    CORS_ALLOW_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
