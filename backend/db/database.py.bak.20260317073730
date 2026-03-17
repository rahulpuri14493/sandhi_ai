from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# Prefer DATABASE_URL; on Azure, also support Connection strings (POSTGRESQLCONNSTR_* or CUSTOMCONNSTR_*)
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRESQLCONNSTR_DefaultConnection")
    or os.getenv("CUSTOMCONNSTR_DefaultConnection")
    or "postgresql://postgres:postgres@localhost:5432/agent_marketplace"
)

# On Azure Web App, DATABASE_URL must point to a real PostgreSQL server (not localhost)
if os.getenv("WEBSITES_SITE_NAME") and ("localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL):
    raise RuntimeError(
        "Set your PostgreSQL URL in Azure: Environment variables → App settings (DATABASE_URL) "
        "or Connection strings (name 'DefaultConnection', type PostgreSQL). It cannot be localhost."
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
