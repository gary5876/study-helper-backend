import json
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: str = "development"
    APP_NAME: str = "Fundamentals Backend"
    APP_VERSION: str = "1.0.0"

    # AWS
    AWS_REGION: str = "ap-northeast-2"
    AWS_S3_BUCKET: str = "fundamentals-pdf-temp"

    # PostgreSQL (question bank)
    DATABASE_URL: str = "postgresql://study@localhost:5432/studyhelper"

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

    # CORS — override in production via env var (JSON array or comma-separated):
    #   ALLOWED_ORIGINS='["https://yourapp.com"]'  or  ALLOWED_ORIGINS='https://yourapp.com,http://localhost:3000'
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8081"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        v = v.strip()
        if v.startswith("["):
            return json.loads(v)
        return [o.strip() for o in v.split(",") if o.strip()]

    # Anthropic
    ANTHROPIC_TIMEOUT: int = 30  # seconds per API call

    # OpenAI (gpt plan)
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 60

    # TimelyGPT (timely plan) — https://timelygpt.co.kr
    TIMELY_MODEL: str = "gpt-4.1"
    TIMELY_TIMEOUT: int = 60

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    # Direct PostgreSQL connection to Supabase DB
    # Format: postgresql://postgres.[project-ref]:[password]@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres
    SUPABASE_DB_URL: str = ""

    # Rate limiting (requests per minute per IP)
    RATE_LIMIT_PER_MINUTE: int = 30

    # Redis TLS — set to true when REDIS_URL uses rediss:// scheme
    REDIS_TLS_ENABLED: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
