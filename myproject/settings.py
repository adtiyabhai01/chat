from pathlib import Path
import os
import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load local .env for development (setdefault: never overrides real env vars,
# so production/Vercel behaviour is unchanged).
_env_path = BASE_DIR / '.env'
if _env_path.exists():
    for _line in _env_path.read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# ========================
# SECURITY
# ========================
SECRET_KEY = os.environ.get("SECRET_KEY", os.environ.get("DJANGO_SECRET_KEY", "change-this-in-production"))

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

# Standalone admin-console login (/admin/). SET THESE IN PRODUCTION ENV.
CONSOLE_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
CONSOLE_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Aditya")

# Default OFF: production must never run with DEBUG=True by accident.
# Local dev overrides via .env when needed.
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"

# Fail fast in production instead of booting with known/default secrets.
# Local DEBUG=False runs are NOT blocked (dev convenience) — only real
# hosted platforms are enforced, detected via their auto-injected env vars.
_ON_HOSTED_PLATFORM = any(
    os.environ.get(k) for k in (
        "RAILWAY_ENVIRONMENT",  # Railway always injects this
        "VERCEL",               # Vercel injects VERCEL=1
        "DYNO",                 # Heroku
        "RENDER",               # Render
        "FLY_APP_NAME",         # Fly.io
    )
)
_WEAK_KEYS = {"", "change-this-in-production", "change-me", "test", "testing"}
_WEAK_ADMIN_PASSWORDS = {"", "Aditya", "admin", "password", "change-me-strong-password"}
if not DEBUG and _ON_HOSTED_PLATFORM:
    if SECRET_KEY in _WEAK_KEYS:
        raise ImproperlyConfigured(
            "SECRET_KEY is not set (or is the default). Set SECRET_KEY env var in production."
        )
    if CONSOLE_ADMIN_PASSWORD in _WEAK_ADMIN_PASSWORDS:
        raise ImproperlyConfigured(
            "ADMIN_PASSWORD is not set (or is the default). Set ADMIN_PASSWORD env var in production."
        )

_env_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".railway.app",
    ".vercel.app",
]
for _h in _env_hosts:
    if _h not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_h)

# ========================
# APPLICATIONS
# ========================
INSTALLED_APPS = [
    'myapp',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# ========================
# MIDDLEWARE
# ========================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'myapp.middleware.NoStoreCacheMiddleware',
]

ROOT_URLCONF = 'myproject.urls'

# ========================
# TEMPLATES
# ========================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],  # optional but recommended
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'myproject.wsgi.application'

# ========================
# DATABASE
# ========================
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get('DATABASE_URL', f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
        conn_max_age=600,
        ssl_require='DATABASE_URL' in os.environ,
    )
}

# ========================
# STATIC FILES
# ========================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'myapp' / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ========================
# MEDIA FILES
# ========================
MEDIA_URL = '/media/'
# Vercel (/var/task) is read-only -> set MEDIA_ROOT=/tmp in Vercel env.
# Local default stays ./media
MEDIA_ROOT = os.environ.get('MEDIA_ROOT', str(BASE_DIR / 'media'))

# ========================
# IMAGEKIT (chat photo storage)
# ========================
IMAGEKIT_PUBLIC_KEY = os.environ.get('IMAGEKIT_PUBLIC_KEY', '')
IMAGEKIT_PRIVATE_KEY = os.environ.get('IMAGEKIT_PRIVATE_KEY', '')
IMAGEKIT_URL_ENDPOINT = os.environ.get('IMAGEKIT_URL_ENDPOINT', '')
IMAGEKIT_FOLDER = os.environ.get('IMAGEKIT_FOLDER', '/dashsocial-chat')

# ========================
# SECURITY (SAFE VERSION)
# ========================
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# Railway/Vercel terminate TLS at the proxy and forward plain HTTP to the
# app — tell Django to trust the X-Forwarded-Proto header, otherwise
# request.is_secure() is always False behind the proxy.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Secure cookies in production; plain HTTP cookies for local dev.
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG

# Keep False on Railway/Vercel (the proxy handles HTTPS). Only turn on via
# env if the app serves TLS directly.
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "False").lower() == "true"

# ========================
# TIMEZONE — India (IST)
# ========================
TIME_ZONE = 'Asia/Kolkata'
USE_TZ = True

# ========================
# AUTH SETTINGS
# ========================
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/home/'
LOGOUT_REDIRECT_URL = '/login/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ========================
# LOGGING (SAFE)
# ========================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
}

CSRF_TRUSTED_ORIGINS = [
    "https://*.railway.app",
    "https://*.vercel.app",
]
for _o in [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]:
    if _o not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS.append(_o)
