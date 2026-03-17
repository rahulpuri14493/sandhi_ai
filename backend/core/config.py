import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Settings(BaseSettings):
    """
    Application settings.

    These settings are loaded from environment variables, with default values provided when not set.
    """

    # Database connection URL
    DATABASE_URL: str = (
        os.getenv("DATABASE_URL")
        or os.getenv("POSTGRESQLCONNSTR_DefaultConnection")
        or os.getenv("CUSTOMCONNSTR_DefaultConnection")
        or "postgresql://postgres:postgres@localhost:5432/agent_marketplace"
    )

    # Secret key for signing JWTs
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")

    # Algorithm used for signing JWTs
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")

    # Token expiration time in minutes
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

    # Commission rate for the platform
    PLATFORM_COMMISSION_RATE: float = float(os.getenv("PLATFORM_COMMISSION_RATE", "0.10"))

    # External API settings
    EXTERNAL_API_KEY: str = os.getenv("EXTERNAL_API_KEY", "")
    EXTERNAL_TOKEN_EXPIRE_DAYS: int = int(os.getenv("EXTERNAL_TOKEN_EXPIRE_DAYS", "7"))
    EXTERNAL_API_BASE_URL: str = os.getenv("EXTERNAL_API_BASE_URL", "http://localhost:8000")

    # A2A adapter settings
    A2A_ADAPTER_URL: str = os.getenv("A2A_ADAPTER_URL", "http://a2a-openai-adapter:8080")

    # Platform MCP server settings
    PLATFORM_MCP_SERVER_URL: str = os.getenv("PLATFORM_MCP_SERVER_URL", "http://platform-mcp-server:8081")

    # Internal secret for platform MCP server and backend-to-MCP-server calls
    MCP_INTERNAL_SECRET: str = os.getenv("MCP_INTERNAL_SECRET", "")

    # Allow agent endpoints that resolve to private/loopback IPs
    ALLOW_PRIVATE_AGENT_ENDPOINTS: bool = os.getenv("ALLOW_PRIVATE_AGENT_ENDPOINTS", "false").lower() in ("true", "1", "yes")

    class Config:
        """
        Configuration for the Settings class.

        This class is used to specify the environment file to load settings from.
        """
        env_file = ".env"


def load_settings() -> Settings:
    """
    Load application settings from environment variables.

    Returns:
        Settings: The loaded settings.
    """
    return Settings()


settings = load_settings()