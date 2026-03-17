"""
Module for database connection setup and session management.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# Load database URL from environment variables, prioritizing DATABASE_URL
# On Azure, also support Connection strings (POSTGRESQLCONNSTR_* or CUSTOMCONNSTR_*)
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRESQLCONNSTR_DefaultConnection")
    or os.getenv("CUSTOMCONNSTR_DefaultConnection")
    or "postgresql://postgres:postgres@localhost:5432/agent_marketplace"
)

# Check if running on Azure Web App and DATABASE_URL is set to localhost
if os.getenv("WEBSITES_SITE_NAME") and ("localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL):
    raise RuntimeError(
        "Set your PostgreSQL URL in Azure: Environment variables → App settings (DATABASE_URL) "
        "or Connection strings (name 'DefaultConnection', type PostgreSQL). It cannot be localhost."
    )

# Create database engine from the loaded URL
engine = create_engine(DATABASE_URL)

# Configure session maker with the engine and set autocommit and autoflush to False
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Define the base class for declarative models
Base = declarative_base()


def get_db() -> sessionmaker:
    """
    Returns a database session.

    Yields:
        sessionmaker: A database session.
    """
    db = SessionLocal()
    try:
        # Yield the database session
        yield db
    except Exception as e:
        # Log or handle the exception
        print(f"Error closing database session: {e}")
    finally:
        # Close the database session
        db.close()
```

```python
# Improved code for get_db function
def get_db() -> sessionmaker:
    """
    Returns a database session.

    Yields:
        sessionmaker: A database session.
    """
    db = SessionLocal()
    try:
        # Yield the database session
        yield db
    except Exception as e:
        # Log or handle the exception
        print(f"Error closing database session: {e}")
    finally:
        # Close the database session
        db.close()