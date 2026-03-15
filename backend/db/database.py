from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agent_marketplace")

# On Azure Web App, DATABASE_URL must point to a real PostgreSQL server (not localhost)
if os.getenv("WEBSITES_SITE_NAME") and ("localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL):
    raise RuntimeError(
        "DATABASE_URL must be set in Azure Application settings to your PostgreSQL server URL. "
        "It cannot be localhost. Add Configuration → Application settings → DATABASE_URL "
        "(e.g. postgresql://user:password@your-postgres-host:5432/your_db) and restart."
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
