import logging
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from db.database import get_db
from models.user import User
from schemas.user import UserCreate, UserResponse, UserLogin, Token
from core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_data: UserCreate, 
    db: Session = Depends(get_db)
) -> UserResponse:
    """
    Register a new user.

    Args:
    - user_data (UserCreate): User data to register.
    - db (Session): Database session.

    Returns:
    - UserResponse: Registered user data.
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        password_hash=hashed_password,
        role=user_data.role
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.post("/login", response_model=Token)
def login(
    user_data: UserLogin, 
    db: Session = Depends(get_db)
) -> dict:
    """
    Login an existing user.

    Args:
    - user_data (UserLogin): User data to login.
    - db (Session): Database session.

    Returns:
    - dict: Login token.
    """
    try:
        user = db.query(User).filter(User.email == user_data.email).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not verify_password(user_data.password, user.password_hash):
            logger.warning("Password verification failed for user: %s", user.email)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": user.id}, expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"An error occurred during login: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error"
        )


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    Get current user info.

    Args:
    - current_user (User): Current user.

    Returns:
    - UserResponse: Current user data.
    """
    return current_user


@router.get("/debug/token")
def debug_token(request: Request) -> dict:
    """
    Debug endpoint to check token extraction.

    Args:
    - request (Request): Request object.

    Returns:
    - dict: Debug information.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return {"error": "No Authorization header"}
    
    if not auth_header.startswith("Bearer "):
        return {"error": "Authorization header doesn't start with 'Bearer '", "header": auth_header}
    
    token = auth_header.replace("Bearer ", "")
    return {
        "has_header": True,
        "header_prefix": auth_header[:20] + "..." if len(auth_header) > 20 else auth_header,
        "token_length": len(token),
        "token_prefix": token[:20] + "..." if len(token) > 20 else token
    }