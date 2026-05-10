from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM
    LLM_API_KEY: str
    LLM_MODEL: str

    # embedding model
    EMBEDDING_MODEL: str
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_DIMENSIONS: int | None = 1024

    # smtp
    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASS: str
    ALERT_RECEIVER: str

    # minio
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_SECURE: bool
    MINIO_BUCKET: str

    # database
    DATABASE_URL: str
    SQL_ECHO: bool = False

    # logging
    LOG_LEVEL: str = "INFO"

    # Prometheus
    PROMETHEUS_URL: str = "http://localhost:9090"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # LangSmith
    LANGSMITH_TRACING: bool | None = None
    LANGSMITH_ENDPOINT: str | None = None
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str | None = None

    # JWT
    SECRET_KEY: str = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30


    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
