from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"
    APP_NAME: str = "Fundamentals Backend"
    APP_VERSION: str = "1.0.0"

    # AWS
    AWS_REGION: str = "ap-northeast-2"
    AWS_S3_BUCKET: str = "fundamentals-pdf-temp"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"
    CACHE_TTL_SECONDS: int = 1800  # 30 minutes

    # PDF Processing
    MAX_PDF_PAGES: int = 50
    MAX_PDF_SIZE_MB: int = 20
    CHUNK_TOKEN_SIZE: int = 800

    # Generation
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    MAX_RETRIES: int = 2

    # CORS — override in production: ALLOWED_ORIGINS='["https://yourapp.com"]'
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8081"]

    # Anthropic
    ANTHROPIC_TIMEOUT: int = 30  # seconds per API call

    # Rate limiting (requests per minute per IP)
    RATE_LIMIT_PER_MINUTE: int = 30

    # Redis TLS — set to true when REDIS_URL uses rediss:// scheme
    REDIS_TLS_ENABLED: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
