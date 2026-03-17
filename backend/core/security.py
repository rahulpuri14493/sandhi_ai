import logging
import os
from datetime import datetime, timedelta
from typing import Optional
import bcrypt
from dotenv import load_dotenv
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from db.database import get_db
from models.user import User, UserRole

load_dotenv()
logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(
    tokenUrl="api/auth/login", auto_error=False
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify password using bcrypt directly.

    Args:
        plain_password (str): Plain password to verify.
        hashed_password (str): Hashed password to verify against.

    Returns:
        bool: True if password is valid, False otherwise.
    """
    try:
        # Ensure password and hashed password are bytes
        password_bytes = (
            plain_password.encode("utf-8")
            if isinstance(plain_password, str)
            else plain_password
        )
        hashed_bytes = (
            hashed_password.encode("utf-8")
            if isinstance(hashed_password, str)
            else hashed_password
        )

        # Check if hash starts with bcrypt identifier
        if not hashed_bytes.startswith(b"$2"):
            logger.warning(
                "Password hash doesn't appear to be bcrypt format: %s...",
                hashed_bytes[:20],
            )
            return False

        # Verify password
        result = bcrypt.checkpw(password_bytes, hashed_bytes)
        if not result:
            logger.debug(
                "Password verification failed for hash: %s...", hashed_bytes[:20]
            )
        return result
    except Exception as e:
        logger.exception("Password verification error: %s", e)
        return False


def get_password_hash(password: str) -> str:
    """
    Hash password using bcrypt directly.

    Args:
        password (str): Password to hash.

    Returns:
        str: Hashed password.
    """
    password_bytes = password.encode("utf-8") if isinstance(password, str) else password
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create access token.

    Args:
        data (dict): Data to encode in token.
        expires_delta (Optional[timedelta]): Expiration delta. Defaults to None.

    Returns:
        str: Encoded JWT token.
    """
    to_encode = data.copy()
    # Ensure 'sub' (subject) is a string as required by JWT spec
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """
    Get current user.

    Args:
        token (str): JWT token. Defaults to Depends(oauth2_scheme).
        db (Session): Database session. Defaults to Depends(get_db).

    Returns:
        User: Current user.

    Raises:
        HTTPException: If token is invalid or missing.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        logger.error("No token provided")
        raise credentials_exception

    try:
        # Decode the JWT token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")

        if user_id_str is None:
            logger.error("Token payload missing 'sub' field. Payload: %s", payload)
            raise credentials_exception

        # Convert string user_id back to int
        try:
            user_id: int = int(user_id_str)
        except (ValueError, TypeError):
            logger.error("Invalid user_id format in token: %s", user_id_str)
            raise credentials_exception

    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as e:
        logger.warning("JWT decode error: %s", e)
        raise credentials_exception
    except Exception as e:
        logger.exception("Unexpected token validation error: %s", e)
        raise credentials_exception

    # Get user from database
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        logger.error("User with id %s not found in database", user_id)
        raise credentials_exception

    return user


def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Get current user if valid JWT is present; otherwise return None.

    Args:
        token (Optional[str]): JWT token. Defaults to Depends(oauth2_scheme_optional).
        db (Session): Database session. Defaults to Depends(get_db).

    Returns:
        Optional[User]: Current user if valid JWT is present; otherwise None.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            return None
        user_id = int(user_id_str)
    except (JWTError, ValueError, TypeError):
        return None
    user = db.query(User).filter(User.id == user_id).first()
    return user


def get_current_business_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Get current business user.

    Args:
        current_user (User): Current user. Defaults to Depends(get_current_user).

    Returns:
        User: Current business user.

    Raises:
        HTTPException: If current user is not a business user.
    """
    if current_user.role != UserRole.BUSINESS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized. Business role required.",
        )
    return current_user


def get_current_developer_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Get current developer user.

    Args:
        current_user (User): Current user. Defaults to Depends(get_current_user).

    Returns:
        User: Current developer user.

    Raises:
        HTTPException: If current user is not a developer user.
    """
    if current_user.role != UserRole.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized. Developer role required.",
        )
    return current_user
