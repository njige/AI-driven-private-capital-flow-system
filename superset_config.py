import os

# 1. Database Connection
SQLALCHEMY_DATABASE_URI = os.getenv(
    'SUPERSET_SQLALCHEMY_DATABASE_URI',
    'postgresql+psycopg2://postgres:postgres@postgres:5432/pcf_db'
)

# 2. Secret Key Setup
SECRET_KEY = os.getenv('SUPERSET_SECRET_KEY', 'bot_derp_secure_superset_key_2026')

# 3. Feature Flags (Required for Embedding & Jinja Templating)
FEATURE_FLAGS = {
    'EMBEDDED_SUPERSET': True,
    'ENABLE_TEMPLATE_PROCESSING': True,
}

# 4. Guest & Public Role Access Configuration
PUBLIC_ROLE_LIKE = "Gamma"
GUEST_ROLE_NAME = "Gamma"
AUTH_ROLE_PUBLIC = "Gamma"

# 5. Security & Frame Embedding (Disable Talisman for local iframe embedding)
TALISMAN_ENABLED = False
ENABLE_CORS = True

HTTP_HEADERS = {
    'X-Frame-Options': 'ALLOWALL'
}

OVERRIDE_HTTP_HEADERS = {
    'X-Frame-Options': 'ALLOWALL'
}

CORS_OPTIONS = {
    'supports_credentials': True,
    'allow_headers': ['*'],
    'resources': ['*'],
    'origins': [
        'http://localhost:5173',
        'http://127.0.0.1:5173'
    ]
}