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

    # CORS — override in production: ALLOWED_ORIGINS='["https://yourapp.com"]'
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8081"]

    # Anthropic
    ANTHROPIC_TIMEOUT: int = 30  # seconds per API call

    # Gemini (free plan)
    GEMINI_API_KEYS: str = ""          # 콤마구분 "key1,key2,key3"
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_TIMEOUT: int = 60

    # OpenAI (gpt plan)
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_TIMEOUT: int = 60

    # TimelyGPT (timely plan) — https://timelygpt.co.kr
    TIMELY_MODEL: str = "gpt-4.1"
    TIMELY_TIMEOUT: int = 60

    @property
    def gemini_keys_list(self) -> list[str]:
        return [k.strip() for k in self.GEMINI_API_KEYS.split(",") if k.strip()]

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
