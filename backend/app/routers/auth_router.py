"""
app/routers/auth_router.py  —  REVISED

Login: verify a TRA TIN + passcode, issue a JWT.

WHAT WAS WRONG IN THE PREVIOUS VERSION
--------------------------------------
1. THE COMPANY NAME NEVER REACHED THE TOKEN'S READER. This file minted the claim
   as `company`:

       create_access_token(data={"sub": tin_key, "company": user["company_name"]})

   while app/core/auth.py reads a different key:

       company_name: str = payload.get("company_name")

   So `payload.get("company_name")` was None for every token this router has ever
   issued — the login response contained the company name, and the token did not.
   Nothing crashed, which is why it went unnoticed: `TokenData.company_name` was
   simply always empty. Confirmed by running your file unchanged against your
   `auth.py`:

       claims in the token : 'company' = 'NJIGE GROUP INVESTMENT LIMITED'
       TokenData received  : company_name = None

   This router now mints `company_name`. `auth.py` has also been taught to read
   the old `company` key, so tokens already sitting in a browser's localStorage
   keep working instead of silently losing the field.

2. `roles` WAS NEVER IN THE TOKEN, and no account could have supplied one. Every
   caller — including a logged-in investor — therefore arrived as
   `roles=["investor_submitter"]` from auth.py's fallback, and that fallback was
   the only thing deciding anyone's role. Roles are now part of the ACCOUNT
   RECORD below, and are minted from it:

       "roles": user.get("roles", DEFAULT_ROLES)

   They are never read from the request body. That property is what makes it safe
   for app/core/auth.py to honour the claim — a client cannot assert its own role
   by adding a field to the login JSON.

   >>> AND THIS IS THE END OF THE 401 STORY. <<<
   Both accounts below are investors, and the workspace has no staff login screen,
   so **no browser can obtain a token that this system recognises as staff**. Two
   consequences, stated plainly rather than buried:
     - `Depends(require_roles(*ECONOMIST_ROLES))` must NOT be attached to the
       economist routes yet. It would answer 403 to every caller alive.
     - the honest options today are: run with ECONOMIST_REQUIRE_AUTH=0 in a LOCAL
       .env, or accept that any logged-in investor can open the economist
       workspace — because a valid token is all it currently takes.
   Neither is the end state. The end state is a staff login screen plus an account
   with `roles=["economist"]`. The optional `BOT_STAFF_*` block below supplies the
   account half of that so it is ready when the screen exists.

3. `hash_password()` RAN AT IMPORT TIME, inside the account dictionary literal:

       "hashed_password": hash_password("Njige@2026!")

   Two costs. It burns ~0.5 s of every startup, twice, including on every
   `--reload`. Worse, it moves a password-library failure into module import —
   and this module is imported by app/services/main.py, so a bad passlib/bcrypt
   pairing stops the entire API from starting rather than making one endpoint
   fail. Reproduced by injecting a bcrypt backend that raises:

       from app.routers import auth_router
       ValueError: password cannot be longer than 72 bytes, truncate manually ...

   i.e. the app never reached `include_router`. The passwords are now precomputed
   bcrypt hashes, so importing this file does no hashing and cannot fail on one.
   (Your environment is fine today — your server starts, which proves your bcrypt
   works. This removes the trap rather than fixing a live fault.)

4. Missing credentials produced a 500, not a 401. bcrypt operates on at most 72
   bytes; passlib 1.7.4 permits it to raise for longer input while passlib with a
   newer bcrypt raises `ValueError` instead of returning False. A long passcode —
   or a garbage one — therefore returned an unhandled 500. Independently, an
   unknown TIN returned early, which makes the response measurably faster for
   unknown accounts than for known ones, so the endpoint leaks which TINs exist.
   Both are handled below.

WHAT DID NOT CHANGE
-------------------
The route path `/api/v1/auth/login`, the request model's field names
(`tin_number`, `password`), the response field names the frontend reads, and the
401 message `"Invalid TRA TIN Number or Passcode."` The two test accounts keep
their existing TINs, company names and passwords.

>>> BEFORE THIS GOES ANYWHERE REAL <<<
`MOCK_INVESTOR_DB` is an in-memory dictionary with the passwords written in this
file. That is fine for a demonstration and not fine for a bank: anyone with the
repository can read two working logins. Move accounts into the database (or at
minimum into environment variables) before this is reachable by anyone else, and
set JWT_SECRET_KEY in .env — see AUTH_REVIEW.md.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.auth import create_access_token, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["0. Authentication"])

# The role a token is given when its account does not name one. Same value
# app/core/auth.py falls back to, so the two cannot drift apart silently.
DEFAULT_ROLES: List[str] = ["investor_submitter"]

# bcrypt reads at most 72 bytes of input. Checked explicitly so a long passcode is
# a clean 401 rather than a backend exception surfacing as a 500.
BCRYPT_MAX_PASSWORD_BYTES = 72


# ==========================================================================
# ACCOUNTS
# ==========================================================================
# These two hashes are bcrypt of the existing test passwords. The plaintext is
# kept in the comment below ONLY so the credentials remain discoverable for a
# demonstration; it is not read by any code.
#
#     "118-492-705"  password  Njige@2026!
#     "104-892-311"  password  Coke@2026!
#
# To change a password, or to add an account, generate a hash with:
#
#     python -c "from app.core.auth import hash_password; print(hash_password('new-password'))"
#
# A bcrypt hash is portable across machines and bcrypt versions, so it can be
# pasted here (or into the database) without re-hashing on startup.
MOCK_INVESTOR_DB: Dict[str, Dict[str, Any]] = {
    "118-492-705": {
        "company_name": "NJIGE GROUP INVESTMENT LIMITED",
        "hashed_password": "$2b$12$sAxEpnUNPeulEzfGfDiMLeGRlTv.Zle8xiSRjBBl2/hP6wf8LoTiK",
        "roles": ["investor_submitter"],
    },
    "104-892-311": {
        "company_name": "Coca-Cola Kwanza Limited",
        "hashed_password": "$2b$12$mVzfq218I1pghH6nGyK.vue38yakh41g4nM0fhJsNEPj0PMXXjZS.",
        "roles": ["investor_submitter"],
    },
}

# A hash of a password nobody has, verified against when the TIN is unknown. This
# makes the "no such account" path take the same time as a wrong password, so the
# endpoint stops revealing which TINs exist.
_UNKNOWN_TIN_HASH = "$2b$12$TEadzFYoAtkhDvFr22z8qejTr9iVqdWhdmCA8IndD2rMx9EdXxsgi"


def _register_staff_account() -> None:
    """Optional staff login, enabled only when its credentials are configured.

    Reads at import, like ALLOW_SUBMISSION_CLEAR in app/routers/economist.py.

        # .env — test only
        BOT_STAFF_TIN=000-111-222
        BOT_STAFF_PASSWORD_HASH=$2b$12$...        (generate with hash_password)
        BOT_STAFF_COMPANY=Bank of Tanzania — DERD  (optional)

    When BOT_STAFF_TIN and BOT_STAFF_PASSWORD_HASH are both set, that account
    exists and its tokens carry roles=["economist"], which is what
    `auth.require_roles(*ECONOMIST_ROLES)` looks for. When either is unset the
    account does not exist and the TIN gets the ordinary 401.

    No password is written here and none is defaulted — an unset variable is a
    refused login, not a weak fallback.
    """
    tin = os.environ.get("BOT_STAFF_TIN", "").strip()
    password_hash = os.environ.get("BOT_STAFF_PASSWORD_HASH", "").strip()
    if not (tin and password_hash):
        return
    MOCK_INVESTOR_DB[tin] = {
        "company_name": os.environ.get("BOT_STAFF_COMPANY", "Bank of Tanzania — DERD"),
        "hashed_password": password_hash,
        "roles": ["economist"],
    }


_register_staff_account()


# ==========================================================================
# REQUEST / RESPONSE
# ==========================================================================

class LoginRequest(BaseModel):
    tin_number: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    company_name: str
    tin_number: str
    roles: List[str] = []


def _authenticate(tin_number: str, password: str) -> Optional[Dict[str, Any]]:
    """Return the account for these credentials, or None.

    Always performs one bcrypt verification, even when the TIN is unknown, so the
    two failure paths cannot be told apart by timing.
    """
    tin_key = (tin_number or "").strip()
    account = MOCK_INVESTOR_DB.get(tin_key)

    if password is None:
        password = ""
    # Over-long input is rejected before the backend sees it: bcrypt silently
    # truncates past 72 bytes, and some passlib/bcrypt pairings raise instead of
    # returning False, which would surface as a 500 on a login screen.
    if len(password.encode("utf-8", errors="replace")) > BCRYPT_MAX_PASSWORD_BYTES:
        password = "\x00" * BCRYPT_MAX_PASSWORD_BYTES

    stored_hash = account["hashed_password"] if account else _UNKNOWN_TIN_HASH

    try:
        password_ok = verify_password(password, stored_hash)
    except Exception:
        # A malformed stored hash (a mistyped paste, an over-long raise from the
        # backend) must not become a 500 that says "this account exists".
        password_ok = False

    if account is None or not password_ok:
        return None
    return account


# ==========================================================================
# ROUTES
# ==========================================================================

@router.post("/login", response_model=TokenResponse)
async def login_investor(credentials: LoginRequest):
    """Verify a TIN and passcode, and issue a signed JWT.

    The response keeps the four fields the frontend already reads
    (`access_token`, `token_type`, `company_name`, `tin_number`) and adds
    `roles`, so a client can route a staff user without decoding the token.
    """
    account = _authenticate(credentials.tin_number, credentials.password)

    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid TRA TIN Number or Passcode.",
        )

    tin_key = credentials.tin_number.strip()
    roles = list(account.get("roles") or DEFAULT_ROLES)

    access_token = create_access_token(data={
        "sub": tin_key,
        # `company_name`, NOT `company`: this is the key app/core/auth.py reads.
        # auth.py also accepts `company` so tokens issued before this fix keep
        # working, but new tokens use the name its reader expects.
        "company_name": account["company_name"],
        "roles": roles,
        "client_id": "bot_portal",
    })

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        company_name=account["company_name"],
        tin_number=tin_key,
        roles=roles,
    )