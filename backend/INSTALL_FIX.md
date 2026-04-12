# Fix for Python 3.13 Installation Issues

If you're encountering build errors with `psycopg2-binary` or `pydantic-core` on Python 3.13, here are solutions:

## Solution 1: Use Python 3.11 or 3.12 (Recommended)

Python 3.13 is very new and some packages don't have full support yet. Use Python 3.11 or 3.12:

```bash
# Install Python 3.12 using Homebrew (macOS)
brew install python@3.12

# Create virtual environment with Python 3.12
python3.12 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Solution 2: Use psycopg (v3) instead of psycopg2-binary

The requirements.txt has been updated to use `psycopg[binary]` which is the newer version that has better Python 3.13 support.

If you still have issues, try:

```bash
# Remove old virtual environment
rm -rf venv

# Create new one
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

## Solution 3: Install packages individually

If bulk install fails, try installing in this order:

```bash
pip install --upgrade pip setuptools wheel
pip install pydantic pydantic-settings
pip install fastapi uvicorn[standard]
pip install sqlalchemy alembic
pip install psycopg[binary]
pip install PyJWT[crypto] passlib[bcrypt]
pip install python-multipart httpx
```

## Note

The code has been updated to work with both `psycopg2` and `psycopg` (v3), so either should work.
