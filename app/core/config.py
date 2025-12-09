from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    LLM_API_KEY: str
    LLM_MODEL: str

    EMBEDDING_MODEL: str

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
