from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# ========================
# SECURITY
# ========================
SECRET_KEY = os.environ.get("SECRET_KEY", os.environ.get("DJANGO_SECRET_KEY", "change-this-in-production"))

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

DEBUG = os.environ.get("DEBUG", "True").lower() == "true"

_env_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".railway.app"
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
# SECURITY (SAFE VERSION)
# ========================
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# ⚠️ Disable for now (Railway safe)
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# Enable later when stable
# SECURE_SSL_REDIRECT = True

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
    "https://*.railway.app"
]
