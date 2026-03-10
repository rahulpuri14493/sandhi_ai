import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
        DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agent_marketplace")
        SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
        ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
        PLATFORM_COMMISSION_RATE: float = float(os.getenv("PLATFORM_COMMISSION_RATE", "0.10"))
        # External API (for end users / systems outside the platform)
        EXTERNAL_API_KEY: str = os.getenv("EXTERNAL_API_KEY", "")
        EXTERNAL_TOKEN_EXPIRE_DAYS: int = int(os.getenv("EXTERNAL_TOKEN_EXPIRE_DAYS", "7"))
        EXTERNAL_API_BASE_URL: str = os.getenv("EXTERNAL_API_BASE_URL", "http://localhost:8000")
        # A2A adapter: when set, OpenAI-compatible agents are called via this A2A endpoint (platform runs A2A everywhere)
        A2A_ADAPTER_URL: str = os.getenv("A2A_ADAPTER_URL", "http://a2a-openai-adapter:8080")

        class Config:
            env_file = ".env"


settings = Settings()
