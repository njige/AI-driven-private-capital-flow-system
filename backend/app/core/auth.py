"""
app/core/auth.py  —  REVISED

JWT signing, password hashing, and the dependency that turns a bearer token into
a caller identity.

WHAT CHANGED
------------
1. `create_access_token` built `exp` from `datetime.utcnow()`. That is deprecated
   in Python 3.12+ and scheduled for removal. It is now `datetime.now(timezone.utc)`.
   Both encode to the same integer, so **tokens already issued stay valid** — this
   is not a change that logs anyone out.

   The trap this avoids: the obvious-looking `datetime.now()` (no argument) also
   works and encodes to an integer, but python-jose treats a naive datetime as UTC,
   so on a UTC+3 machine a 60-minute token silently becomes 240 minutes. Measured,
   not theorised. A timezone-aware datetime is the correct one here — the opposite
   of app/db/models.py, where a tz-aware value breaks asyncpg inserts into a
   TIMESTAMP WITHOUT TIME ZONE column.

2. `roles` and `client_id` were hardcoded:

       client_id="sme_investor_portal",  roles=["investor_submitter"]

   so nothing in the token was read except `sub` and `company_name`, and every
   authenticated caller — staff included — was reported as an investor. Both are
   now read from the signed token **when present**, and fall back to exactly the
   old hardcoded values when absent. A token minted without those claims produces
   the same TokenData as before, which is what makes this change additive.

   >>> BEFORE YOU RELY ON `roles`, READ THIS. <<<
   Honouring a claim is only as trustworthy as the claim's origin. If your login
   endpoint copies a client-supplied `roles` field into the token, then honouring
   it is a privilege escalation — anyone could mint themselves a staff role.
   Nothing anywhere enforces these roles yet (see point 3), so today `roles` is
   *reported* and never *trusted*. That is the safe order. Confirm
   app/routers/auth_router.py builds `roles` from the database and not from the
   request body, and only then attach the guard below.

3. `company_name` is now read from either `company_name` or `company`. The
   unrevised auth_router.py minted the claim as `company`, so this reader — which
   looked only for `company_name` — returned None for every login ever made.
   Accepting both means already-issued tokens are not broken by the pairing being
   corrected in the router.

4. `require_roles()` added, and **attached to no route**. It is the piece the
   economist workspace needs once staff logins exist, written and tested now so
   the guard is not improvised later:

       @router.get("/submissions")
       async def fetch_bot_submissions(
           current_user: TokenData = Depends(require_roles(*ECONOMIST_ROLES)),
       ):

   Until auth_router.py is confirmed, wiring it up would lock the dashboard out
   with a 403 the moment a staff token carries no role — which is the state the
   routes are in today.

WHAT DID NOT CHANGE
-------------------
`SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `hash_password`,
`verify_password`, `oauth2_scheme`, `TokenData`'s field names, and the
`get_current_user` name that ingestion.py and economist.py import. The 401 body
string is also unchanged, so the diagnosis in AUTH_REVIEW.md still applies:

    {"detail": "Could not validate credentials or session expired."}
"""
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

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

# What a token without these claims is treated as. Kept identical to the old
# hardcoded values so this file's behaviour is unchanged for existing tokens.
DEFAULT_CLIENT_ID = "sme_investor_portal"
DEFAULT_ROLES = ["investor_submitter"]

# Roles that are allowed to read the economist workspace. Exported so the routers
# can import one definition instead of repeating a list.
ECONOMIST_ROLES = ("economist", "auditor", "bot_staff", "admin")


class TokenData(BaseModel):
    client_id: Optional[str] = None
    tin_number: Optional[str] = None
    company_name: Optional[str] = None
    # Pydantic deep-copies mutable defaults per instance, so this shared list is
    # safe. (The same line in a plain dataclass would be a bug.)
    roles: list[str] = []


# --- Hashing & Password Utils ---
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# --- JWT Token Generation ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _roles_from_claim(raw: object) -> list[str]:
    """Read the `roles` claim, tolerating the shapes a login endpoint produces.

    A caller holding a token with `"roles": "admin"` — a string rather than a
    list — would otherwise have its roles read as the characters
    ['a','d','m','i','n'], which matches nothing and denies access for a reason
    nobody would ever guess from the 403.
    """
    if raw is None:
        return list(DEFAULT_ROLES)
    if isinstance(raw, str):
        parts = raw.replace(",", " ").split()
    elif isinstance(raw, Iterable):
        parts = [str(part) for part in raw]
    else:
        return list(DEFAULT_ROLES)
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return cleaned or list(DEFAULT_ROLES)


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
        # `company_name` is the key app/routers/auth_router.py mints. The
        # unrevised router minted `company` instead, while this line read
        # `company_name` — so every token it ever issued arrived here with the
        # company name silently None. Both spellings are accepted so tokens
        # already sitting in a browser's localStorage keep working; new tokens
        # carry `company_name`.
        company_name: str = payload.get("company_name") or payload.get("company")
        if tin_number is None:
            raise credentials_exception
        return TokenData(
            client_id=payload.get("client_id") or DEFAULT_CLIENT_ID,
            tin_number=tin_number,
            company_name=company_name,
            roles=_roles_from_claim(payload.get("roles")),
        )
    except JWTError:
        raise credentials_exception


async def get_current_user(token_data: TokenData = Depends(verify_gateway_token)) -> TokenData:
    return token_data


# --- Role enforcement (built, tested, NOT attached anywhere) ---
def require_roles(*allowed: str):
    """Dependency factory: authenticated AND holding one of `allowed` roles.

    Deliberately not wired to a route in this revision — see point 3 at the top of
    this file. To use it, import it in the router and replace
    `Depends(get_current_user)` with `Depends(require_roles(*ECONOMIST_ROLES))`.

    Returns 401 when there is no identity at all, and 403 when the caller is
    authenticated but not permitted, so the two are never confused by the frontend.
    """
    allowed_set = frozenset(role.strip().lower() for role in allowed if role and role.strip())
    if not allowed_set:
        raise ValueError("require_roles() needs at least one role name.")

    async def _guard(user: TokenData = Depends(get_current_user)) -> TokenData:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        held = {str(role).strip().lower() for role in (getattr(user, "roles", None) or [])}
        if not (held & allowed_set):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "This account is not authorised for the economist workspace. "
                    f"Required role: one of {sorted(allowed_set)}; "
                    f"this account holds {sorted(held) or ['none']}."
                ),
            )
        return user

    return _guard