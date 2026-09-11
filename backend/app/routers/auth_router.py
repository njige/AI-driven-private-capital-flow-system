from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.core.auth import verify_password, hash_password, create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["0. Authentication"])

# Registered test accounts (Hashes generated using Bcrypt)
MOCK_INVESTOR_DB = {
    "118-492-705": {
        "company_name": "NJIGE GROUP INVESTMENT LIMITED",
        "hashed_password": hash_password("Njige@2026!")
    },
    "104-892-311": {
        "company_name": "Coca-Cola Kwanza Limited",
        "hashed_password": hash_password("Coke@2026!")
    }
}

class LoginRequest(BaseModel):
    tin_number: str
    password: str

@router.post("/login")
async def login_investor(credentials: LoginRequest):
    # Standardize TIN format matching
    tin_key = credentials.tin_number.strip()
    user = MOCK_INVESTOR_DB.get(tin_key)
    
    if not user or not verify_password(credentials.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid TRA TIN Number or Passcode."
        )
    
    # Issue JWT token on successful verification
    access_token = create_access_token(data={
        "sub": tin_key,
        "company": user["company_name"]
    })
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "company_name": user["company_name"],
        "tin_number": tin_key
    }