from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ANTHROPIC_API_KEY: str = ""
    REDIS_URL: str = ""
    UPLOAD_DIR: str = "/data/uploads"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]
    UZS_RATE: float = 12800.0

    class Config:
        env_file = ".env"


settings = Settings()
