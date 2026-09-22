"""superset_config.py — Apache Superset configuration for the PCF/C17 portal

Loaded by Superset from the directory on `PYTHONPATH` (the docker-compose file
usually mounts this file at /app/pythonpath/superset_config.py) or from whatever
`SUPERSET_CONFIG_PATH` points at. Settings here win over Superset's own defaults;
environment variables win over everything below.

The purpose of this file in this deployment is narrow: `EconomistDashboard.tsx`
frames Superset in the "Superset Analytics" tab, and Superset refuses to be
framed by default.

WHAT CHANGED, AND WHY EACH ONE MATTERS
--------------------------------------
1. `SECRET_KEY` no longer has a literal default. It is read from
   `SUPERSET_SECRET_KEY` and the process REFUSES TO START without it.

   The old value was `'bot_derp_secure_superset_key_2026'` — hardcoded in a file
   that lives in the repository. This key signs every session cookie and encrypts
   the stored data-source passwords. Anyone who can read the file can mint a
   valid admin session cookie and read every dashboard, dataset and connection
   this instance holds. That is exactly the class of bug behind CVE-2023-27524
   (Superset's own default key), which is why Superset 2.1+ refuses to start on
   its known-bad defaults — a hand-written literal dodges that guard entirely.

   Generate one:  openssl rand -base64 42
   Then:          SUPERSET_SECRET_KEY=<that value>  in the container's environment.
   If Superset has already run with the old key, existing encrypted values stay
   readable by putting the old value in PREVIOUS_SECRET_KEY and running
   `superset re-encrypt-secrets`.

2. The metadata database defaults to its OWN database (`superset_metadata`), and
   the file refuses to point it at the application database.

   It was `.../pcf_db`, i.e. Superset's ~100 internal tables would live inside the
   database the PCF backend writes to. The sharp edge is not the extra tables: it
   is that both tools track their migration state in a table called
   `alembic_version`. The backend has its own `alembic/` directory. Run
   `superset db upgrade` and `alembic upgrade head` against one database and they
   overwrite each other's version row — after which a real migration can be
   silently skipped on the next deploy.

   Opt out deliberately with SUPERSET_ALLOW_SHARED_METADATA_DB=1 if you are not
   ready to move the metadata yet; the warning that prints says what you are
   accepting.

3. Embedding is allowed by an explicit allow-list, not by an invented header value.

   The old file set `X-Frame-Options: ALLOWALL`. `ALLOWALL` is not one of the
   three values that header defines (DENY / SAMEORIGIN / ALLOW-FROM), so browsers
   ignore it — the frame shows today by accident of the header being invalid, not
   because the configuration asked for it. `ALLOW-FROM` is no longer honoured by
   any current browser either.

   The supported control is CSP `frame-ancestors`, which is what this file now
   sends, listing exactly the origins allowed to frame Superset. Browsers prefer
   `frame-ancestors` whenever it is present, so the allow-list is the thing that
   decides. Configure it with SUPERSET_FRAME_ANCESTORS (comma-separated); the
   default is the local Vite dev origins.

   `HTTP_HEADERS` was removed from this file: Superset deprecated it in favour of
   DEFAULT_HTTP_HEADERS / OVERRIDE_HTTP_HEADERS, so keeping both was misleading.

4. Turning Talisman off silently removed four other protections. Three are put back.

   Talisman is disabled on purpose — its CSP and its `X-Frame-Options: SAMEORIGIN`
   are what made the frame blank. But it was also the thing adding
   `X-Content-Type-Options: nosniff`, HSTS and a referrer policy, and nothing
   replaced them. The headers that do NOT block framing are restored here;
   `Strict-Transport-Security` is added only when SUPERSET_HTTPS=1, because
   sending it over plain HTTP is worse than not sending it.

5. Anonymous visitors no longer get the Gamma role.

   The old file had BOTH `PUBLIC_ROLE_LIKE = "Gamma"` and
   `AUTH_ROLE_PUBLIC = "Gamma"`. The second one is the serious one: it assigns
   the Gamma role itself to anyone who has not logged in. Public in Superset
   explicitly excludes SQL Lab; Gamma does not. So an unauthenticated visitor to
   port 8088 could browse every dashboard and reach SQL Lab, limited only by
   dataset access.

   The default here is `AUTH_ROLE_PUBLIC = "Public"`. Anonymous dashboard viewing
   still works — but only for dashboards whose datasets the Public role has been
   granted (Menu -> Security -> List Roles -> Public -> add the data sources),
   which is the point: visibility becomes an explicit decision per dataset.

   One part of this is NOT in the file: if the instance was initialised while
   `PUBLIC_ROLE_LIKE = "Gamma"` was in place — which is what the old file said —
   the Public role in the metadata database already holds a COPY of Gamma's
   permissions. That copy is stored state; changing the config does not remove
   it. Check it against the Superset metadata database:

       SELECT r.name, COUNT(pvr.id) AS permission_rows
       FROM ab_role r
       LEFT JOIN ab_permission_view_role pvr ON pvr.role_id = r.id
       WHERE r.name IN ('Public', 'Gamma')
       GROUP BY r.name;

   Equal row counts for Public and Gamma means the copy is still there. Clear it
   and re-sync:

       DELETE FROM ab_permission_view_role
       WHERE role_id = (SELECT id FROM ab_role WHERE name = 'Public');
       -- then, in the container:
       superset init

   With `PUBLIC_ROLE_LIKE = "Public"` (Superset's documented value for this
   setting) `superset init` syncs Public's own sensible defaults; note that
   afterwards anonymous visitors see nothing until datasets are granted, so do
   this when you can also grant them.

   If that breaks a demo before you can grant those permissions, set
   SUPERSET_ALLOW_ANON_GAMMA=1 to restore the previous behaviour exactly. It
   prints a warning when it does, so it cannot be left on unnoticed.

6. Guest-token settings are now stated instead of implied.

   `EMBEDDED_SUPERSET` was already on, but the rest of what guest-token embedding
   needs was not: the JWT secret, the algorithm, the token lifetime and the
   header name. `GUEST_TOKEN_JWT_SECRET` in particular falls back to SECRET_KEY
   when unset, which quietly widens finding 1 to the guest tokens as well.

   `GUEST_ROLE_NAME` here keeps Superset's own default of "Public", so an
   embedded guest sits on exactly the same footing as an anonymous visitor and
   is governed by the same dataset grants. Most embedding tutorials tell you to
   set it to "Gamma" to make the dashboard appear — that is the same foot-gun as
   finding 5, moved onto the guest token. When a demo needs it, set
   `SUPERSET_GUEST_ROLE=Gamma` deliberately, and put it back afterwards.

7. CSRF stays ON, and this file says so.

   `WTF_CSRF_ENABLED` is not disabled here. It is the setting most often switched
   off to make an embedding demo "work" — the GitHub threads recommending it say
   so themselves — and that removes the protection on every state-changing form
   in the Superset UI.

WHAT IS DELIBERATELY LEFT ALONE
-------------------------------
* `EMBEDDED_SUPERSET` and `ENABLE_TEMPLATE_PROCESSING` stay enabled, and the two
  plugin-era names (`ENABLE_CORS`, `CORS_OPTIONS`) keep working. CORS is NOT what
  makes an iframe load — CORS governs XHR, framing is governed by the headers in
  3 above — so it is kept, scoped, and left on to avoid changing behaviour.
* `DASHBOARD_RBAC` is available but OFF by default: turning it on hides every
  dashboard that has no explicit owner/role assignment, which is a change you
  should make having looked at the dashboard list first. `SUPERSET_DASHBOARD_RBAC=1`.
* No database connection, dashboard or user is defined here — this file only
  configures the server. Superset's own objects live in its metadata database.

VERIFY IT:  python verify_superset_config.py            (static checks)
            python verify_superset_config.py --url http://localhost:8088
            python verify_superset_config.py --url http://localhost:8088 \
                   --ancestor http://localhost:5173   (says what a browser decides)
"""
from __future__ import annotations

import os
import sys

# ===========================================================================
# Helpers
# ===========================================================================


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def _flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on"}


def _warn(*lines: str) -> None:
    for line in lines:
        print(f"[superset_config] {line}", file=sys.stderr)


def _refuse(title: str, *lines: str) -> "NoReturn":  # type: ignore[name-defined]  # noqa: F821
    """Stop the process with an actionable message.

    Failing to start is the correct outcome for a missing secret: Superset would
    otherwise run with a key that is public knowledge, and no later warning makes
    that safe.
    """
    _warn("", f"REFUSING TO START — {title}", "")
    for line in lines:
        _warn(line)
    _warn("")
    raise SystemExit(1)


def _db_name(uri: str) -> str | None:
    """The database name out of a SQLAlchemy URL, without importing SQLAlchemy."""
    try:
        from sqlalchemy.engine import make_url  # present inside the Superset image

        return make_url(uri).database
    except Exception:  # noqa: BLE001 - fall back to a plain parse
        from urllib.parse import urlsplit

        try:
            path = urlsplit(uri).path
        except ValueError:
            return None
        return path.lstrip("/") or None


# ===========================================================================
# 1. Secret key — from the environment, or not at all
# ===========================================================================
SECRET_KEY = _env("SUPERSET_SECRET_KEY")

# A short blocklist of the values that have circulated in Superset tutorials,
# the docker-compose templates and this project's own history. Refusing these is
# the same guard Superset applies to its built-in default, extended to the
# lookalikes that got past it.
KNOWN_BAD_SECRETS = {
    "CHANGE_ME_TO_A_COMPLEX_RANDOM_SECRET",
    "thisismyscretkey",
    "TEST_NON_DEV_SECRET",
    "thisisnotapropersecretkey",
    "bot_derp_secure_superset_key_2026",   # <- what this file used to hardcode
}

if not SECRET_KEY:
    _refuse(
        "SUPERSET_SECRET_KEY is not set",
        "This key signs every session cookie and encrypts the stored database",
        "passwords. It cannot have a default.",
        "",
        "Generate one and give it to the container:",
        "    openssl rand -base64 42",
        "then in the Superset service environment (or its .env file):",
        "    SUPERSET_SECRET_KEY=<the value you just generated>",
    )
if SECRET_KEY in KNOWN_BAD_SECRETS:
    _refuse(
        "SUPERSET_SECRET_KEY is a known/guessable value",
        f"the value in use is {SECRET_KEY!r}, which appears in public tutorials,",
        "in Superset's own docker-compose templates, or in this repository's history.",
        "Anyone who has read any of those can forge an admin session cookie.",
        "",
        "    openssl rand -base64 42   # generate a new one",
        "    SUPERSET_SECRET_KEY=<that value>",
        "then, if Superset has already stored encrypted values with the old key,",
        "    PREVIOUS_SECRET_KEY=<old value>   superset re-encrypt-secrets",
    )

# Set only during a key rotation, alongside the new SECRET_KEY.
PREVIOUS_SECRET_KEY = _env("SUPERSET_PREVIOUS_SECRET_KEY")

# ===========================================================================
# 2. Metadata database — its own database, not the application's
# ===========================================================================
SQLALCHEMY_DATABASE_URI = (
    _env("SUPERSET_SQLALCHEMY_DATABASE_URI")
    or "postgresql+psycopg2://postgres:postgres@postgres:5432/superset_metadata"
)

APP_DB_NAME = _env("SUPERSET_APP_DB_NAME", "pcf_db")
_metadata_db = _db_name(SQLALCHEMY_DATABASE_URI)
_shared_ok = _flag("SUPERSET_ALLOW_SHARED_METADATA_DB")

if _metadata_db and APP_DB_NAME and _metadata_db == APP_DB_NAME and not _shared_ok:
    _refuse(
        "the Superset metadata database is the APPLICATION database",
        f"SQLALCHEMY_DATABASE_URI points at {_metadata_db!r}, which is where the",
        "PCF backend keeps its own tables and its own Alembic migration state.",
        "",
        "Both Superset and Alembic record their version in a table named",
        "`alembic_version`. Running `superset db upgrade` and `alembic upgrade head`",
        "against one database overwrite each other's version row, and the next",
        "deploy can silently skip a real migration.",
        "",
        "Either give Superset its own database:",
        "    CREATE DATABASE superset_metadata;",
        "    SUPERSET_SQLALCHEMY_DATABASE_URI=postgresql+psycopg2://postgres:postgres@postgres:5432/superset_metadata",
        "    docker compose exec superset superset db upgrade && superset init",
        "or accept the collision deliberately:",
        "    SUPERSET_ALLOW_SHARED_METADATA_DB=1",
    )

if _metadata_db and APP_DB_NAME and _metadata_db == APP_DB_NAME and _shared_ok:
    _warn(
        f"Superset is using {APP_DB_NAME!r} as its metadata database",
        "because SUPERSET_ALLOW_SHARED_METADATA_DB=1 is set. Superset and the",
        "backend now share one `alembic_version` table — never run `superset db",
        "upgrade` and `alembic upgrade head` against this database in the same",
        "deploy without checking which one owns that row.",
    )
elif not _metadata_db:
    _warn(f"could not read a database name out of {SQLALCHEMY_DATABASE_URI!r}")

SQLALCHEMY_ENGINE_OPTIONS = {
    # A dropped connection between requests otherwise surfaces as a 500 on the
    # first click after an idle period.
    "pool_pre_ping": True,
    "pool_recycle": 1800,
}

# ===========================================================================
# 3. Embedding: Talisman off, then an explicit frame-ancestors allow-list
# ===========================================================================
TALISMAN_ENABLED = False

# The origins allowed to put Superset in a frame. Defaults are the Vite dev
# server; set SUPERSET_FRAME_ANCESTORS to the real portal origin in production,
# e.g. SUPERSET_FRAME_ANCESTORS=https://pcf.bot.go.tz
_frame_ancestors = [
    origin.strip()
    for origin in (
        _env("SUPERSET_FRAME_ANCESTORS")
        or "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

OVERRIDE_HTTP_HEADERS = {
    # Kept so tools that look for a permissive XFO see one, but note the real
    # control is the frame-ancestors list below: `ALLOWALL` is not a value the
    # X-Frame-Options header defines, so browsers ignore the header entirely.
    "X-Frame-Options": "ALLOWALL",
    # The allow-list. Only frame-ancestors is set, so nothing else about how the
    # page loads is restricted — this cannot break the Superset UI.
    "Content-Security-Policy": "frame-ancestors 'self' " + " ".join(_frame_ancestors),
    # Put back the three protections that disabling Talisman removed and that do
    # not affect framing.
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
}

if _flag("SUPERSET_HTTPS"):
    # Only when Superset is actually served over TLS: sending HSTS over plain
    # HTTP is ignored at best and locks a browser into https:// at worst.
    OVERRIDE_HTTP_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

# ===========================================================================
# 4. Who anonymous visitors are
# ===========================================================================
_anon_gamma = _flag("SUPERSET_ALLOW_ANON_GAMMA")

if _anon_gamma:
    AUTH_ROLE_PUBLIC = "Gamma"
    PUBLIC_ROLE_LIKE = "Gamma"
    _warn(
        "SUPERSET_ALLOW_ANON_GAMMA=1 — anonymous visitors are being given the",
        "Gamma role. They can browse every dashboard and reach SQL Lab. This is",
        "the pre-revision behaviour, kept only so a demonstration is not blocked;",
        "remove the variable and grant the Public role the datasets it needs.",
    )
else:
    # Anyone not logged in gets the Public role, which has no SQL Lab access.
    # Anonymous dashboard viewing still works, but only for dashboards whose
    # datasets have been granted to Public (Menu -> Security -> List Roles ->
    # Public -> add data sources) — visibility is an explicit decision.
    AUTH_ROLE_PUBLIC = "Public"
    # Sync the sensible built-in Public permissions on `superset init`.
    PUBLIC_ROLE_LIKE = _env("SUPERSET_PUBLIC_ROLE_LIKE", "Public")

# ===========================================================================
# 5. Guest-token embedding (the supported way to show a dashboard in a portal)
# ===========================================================================
# Superset's own default, kept here: a guest token is a session created for
# someone who is not logged in, so it should carry the same role as any other
# anonymous visitor. SUPERSET_GUEST_ROLE=Gamma widens it — that is the line
# most embedding tutorials add, and it hands an embedded session Gamma's
# permissions, SQL Lab included.
GUEST_ROLE_NAME = _env("SUPERSET_GUEST_ROLE", "Public")
GUEST_TOKEN_HEADER_NAME = "X-GuestToken"
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_TOKEN_JWT_EXP_SECONDS = int(_env("SUPERSET_GUEST_TOKEN_TTL", "300") or 300)

_guest_secret = _env("SUPERSET_GUEST_TOKEN_JWT_SECRET")
if _guest_secret:
    GUEST_TOKEN_JWT_SECRET = _guest_secret
else:
    # Superset signs guest tokens with GUEST_TOKEN_JWT_SECRET and falls back to
    # SECRET_KEY when it is unset — which is why the SECRET_KEY finding above
    # matters twice over. Set a separate value so the two can be rotated apart.
    GUEST_TOKEN_JWT_SECRET = SECRET_KEY
    _warn(
        "GUEST_TOKEN_JWT_SECRET is not set, so guest tokens are signed with",
        "SECRET_KEY. That is Superset's fallback, not a recommendation: set",
        "SUPERSET_GUEST_TOKEN_JWT_SECRET=<openssl rand -base64 42> so a guest",
        "token can be invalidated without rotating every session.",
    )

# ===========================================================================
# 6. CORS — scoped, and not the thing that fixes the frame
# ===========================================================================
ENABLE_CORS = _flag("SUPERSET_ENABLE_CORS", True)

CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["Content-Type", "Authorization", "X-GuestToken", "X-CSRFToken"],
    "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    # Scoped to the API the portal would call. It does not XHR Superset today —
    # it frames it — so this is here for the embedded SDK's token exchange.
    "resources": [r"/api/v1/*", r"/security/*"],
    "origins": _frame_ancestors,
}

# ===========================================================================
# 7. Feature flags
# ===========================================================================
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "ENABLE_TEMPLATE_PROCESSING": True,
}
if _flag("SUPERSET_DASHBOARD_RBAC"):
    # Per-dashboard access control. Turning it on HIDES every dashboard that has
    # no explicit owner or role assignment, including for admins' views of the
    # public portal, so enable it having looked at the dashboard list first.
    FEATURE_FLAGS["DASHBOARD_RBAC"] = True

# ===========================================================================
# 8. Session/CSRF hardening that this deployment keeps
# ===========================================================================
WTF_CSRF_ENABLED = True
WTF_CSRF_TIME_LIMIT = 60 * 60 * 24 * 365

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = _flag("SUPERSET_HTTPS")   # True once the portal is https
SESSION_COOKIE_SAMESITE = _env("SUPERSET_SAMESITE", "Lax")

if _frame_ancestors and not any(o.startswith("https://") for o in _frame_ancestors):
    _warn(
        "frame-ancestors is HTTP-only (development). Before this portal is",
        "reachable from anywhere but your own machine, set SUPERSET_FRAME_ANCESTORS",
        "to the https origin and SUPERSET_HTTPS=1.",
    )
