from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from jose import JWTError, jwt
from passlib.context import CryptContext
import os

# JWT & Password Hashing Config
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "bot_pcf_secret_key_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

class TokenData(BaseModel):
    client_id: Optional[str] = None
    tin_number: Optional[str] = None
    company_name: Optional[str] = None
    roles: list[str] = []

# --- Hashing & Password Utils ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# --- JWT Token Generation ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# --- Gateway & Dependency Validation ---
async def verify_gateway_token(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    Validates JWT tokens issued during login or gateway pass-through.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials or session expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        tin_number: str = payload.get("sub")
        company_name: str = payload.get("company_name")
        if tin_number is None:
            raise credentials_exception
        return TokenData(
            client_id="sme_investor_portal",
            tin_number=tin_number,
            company_name=company_name,
            roles=["investor_submitter"]
        )
    except JWTError:
        raise credentials_exception

async def get_current_user(token_data: TokenData = Depends(verify_gateway_token)) -> TokenData:
    return token_data