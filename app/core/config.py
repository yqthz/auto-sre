from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LLM_API_KEY: str
    LLM_MODEL: str

    EMBEDDING_MODEL: str

    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASS: str
    ALERT_RECEIVER: str

    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_SECURE: bool
    MINIO_BUCKET: str


    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
