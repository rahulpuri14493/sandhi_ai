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
        # MLOps Inference Endpoint Configuration
        MLOPS_API_URL: str = os.getenv("MLOPS_API_URL", "https://mle-uat-mlops.cloud.workfusion.com:4437/mlops-api/mlops-inference-service/v1/chat/completions")
        MLOPS_CERT_FILE: str = os.getenv("MLOPS_CERT_FILE", "/Users/rahulpuri/Desktop/me/test/crt")
        MLOPS_KEY_FILE: str = os.getenv("MLOPS_KEY_FILE", "/Users/rahulpuri/Desktop/me/test/key")
        MLOPS_MODEL: str = os.getenv("MLOPS_MODEL", "llama-3-1-8b-model")

        class Config:
            env_file = ".env"


settings = Settings()
