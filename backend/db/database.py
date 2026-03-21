from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url
import os
from core.config import settings

# Centralized settings with env fallbacks is handled in core.config.Settings.
DATABASE_URL = settings.DATABASE_URL

# On Azure Web App, DATABASE_URL must point to a real PostgreSQL server (not localhost)
if os.getenv("WEBSITES_SITE_NAME") and ("localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL):
    raise RuntimeError(
        "Set your PostgreSQL URL in Azure: Environment variables → App settings (DATABASE_URL) "
        "or Connection strings (name 'DefaultConnection', type PostgreSQL). It cannot be localhost."
    )

# Prefer connection timeout and SSL for cloud Postgres (e.g. Azure) to fail fast instead of hanging
_connect_args = {"connect_timeout": 10}
parsed_db_url = make_url(DATABASE_URL)
host = parsed_db_url.host or ""
query = parsed_db_url.query or ""
has_sslmode = "sslmode" in query
# On Azure App Service, enforce SSL unless caller explicitly set sslmode.
# Avoid hostname substring heuristics so local/dev and Docker (often host "db") keep working.
is_running_on_azure_app_service = bool(os.getenv("WEBSITES_SITE_NAME"))
if (is_running_on_azure_app_service or has_sslmode) and not has_sslmode:
    _connect_args["sslmode"] = "require"
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
