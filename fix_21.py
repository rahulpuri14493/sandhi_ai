# backend/api/routes/auth.py

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, constr
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from backend.core.config import settings
from backend.db import get_db
from backend.core.security import authenticate_user, create_access_token

limiter = Limiter(key_func=get_remote_address)

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: str | None = None

class User(BaseModel):
    username: str
    email: EmailStr | None = None
    full_name: str | None = None
    disabled: bool | None = None

class UserInDB(User):
    hashed_password: str

class UserCreate(User):
    password: constr(min_length=8)

@router.post("/token", response_model=Token, dependencies=[Depends(limiter.limit(settings.RATE_LIMIT, per=60))])
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/register", response_model=User, dependencies=[Depends(limiter.limit(settings.RATE_LIMIT, per=60))])
async def register_user(user: UserCreate, db: Session = Depends(get_db)):
    # Add user registration logic here
    pass