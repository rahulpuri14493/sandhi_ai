from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from db.database import get_db
from models.user import User
import os
import bcrypt
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password using bcrypt directly"""
    try:
        password_bytes = plain_password.encode('utf-8') if isinstance(plain_password, str) else plain_password
        hashed_bytes = hashed_password.encode('utf-8') if isinstance(hashed_password, str) else hashed_password
        
        # Check if hash starts with bcrypt identifier ($2a$, $2b$, $2x$, $2y$)
        if not hashed_password.startswith('$2'):
            print(f"Warning: Password hash doesn't appear to be bcrypt format: {hashed_password[:20]}...")
            return False
        
        result = bcrypt.checkpw(password_bytes, hashed_bytes)
        if not result:
            print(f"Password verification failed for hash: {hashed_password[:20]}...")
        return result
    except Exception as e:
        print(f"Password verification error: {e}")
        import traceback
        traceback.print_exc()
        return False


def get_password_hash(password: str) -> str:
    """Hash password using bcrypt directly"""
    password_bytes = password.encode('utf-8') if isinstance(password, str) else password
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    # Ensure 'sub' (subject) is a string as required by JWT spec
    if 'sub' in to_encode:
        to_encode['sub'] = str(to_encode['sub'])
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    if not token:
        print("ERROR: No token provided")
        raise credentials_exception
    
    try:
        # Decode the JWT token
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        
        if user_id_str is None:
            print(f"ERROR: Token payload missing 'sub' field. Payload: {payload}")
            raise credentials_exception
        
        # Convert string user_id back to int
        try:
            user_id: int = int(user_id_str)
        except (ValueError, TypeError):
            print(f"ERROR: Invalid user_id format in token: {user_id_str}")
            raise credentials_exception
            
    except jwt.ExpiredSignatureError:
        print("ERROR: Token has expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as e:
        print(f"ERROR: JWT decode error - {e}")
        raise credentials_exception
    except Exception as e:
        print(f"ERROR: Unexpected token validation error - {e}")
        import traceback
        traceback.print_exc()
        raise credentials_exception
    
    # Get user from database
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        print(f"ERROR: User with id {user_id} not found in database")
        raise credentials_exception
    
    return user


def get_current_business_user(current_user: User = Depends(get_current_user)) -> User:
    from models.user import UserRole
    if current_user.role != UserRole.BUSINESS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized. Business role required."
        )
    return current_user


def get_current_developer_user(current_user: User = Depends(get_current_user)) -> User:
    from models.user import UserRole
    if current_user.role != UserRole.DEVELOPER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized. Developer role required."
        )
    return current_user
