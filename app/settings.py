import os
from pathlib import Path
import environ
import discord

environ.Env.read_env(Path(__file__).parent.parent / '.env')

env = environ.FileAwareEnv(
    DAPHNE_PORT=(int, 25923),
    DAPHNE_IFACE=(str, "127.0.0.1"),
    MAIN_COLOR=(str, "0x4c1f4c"),
    SECRET_KEY=(str),   # SEC-01: no default -> ImproperlyConfigured if unset (fail loud). Set in .env only.
    DJANGO_DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["blightveil.org", "www.blightveil.org"]),
    DATABASE_URI=str,
    REDIS_HOST=(str),   # SEC-02: no default. Set in .env only; firewall Redis to the app host.
    STATIC_VOLUME_MOUNT=(str, "/home/container/data/django/"),
    STATIC_URL=(str, "/static/"),
    MEDIA_URL=(str, "/media/"),
    SUPERUSERS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, ["https://blightveil.org", "https://www.blightveil.org"]),
    REDIS_PASSWORD=str,
    GOOGLE_AI_API_KEY=(str, ""),
    ANTHROPIC_API_KEY=(str, ""),
    GROQ_API_KEY=(str, ""),
    DISCORD_BOT_DESCRIPTION=(str, "BlightVeil Servitor"),
    MAIL_USERNAME=(str, ""),
    MAIL_PASSWORD=(str, ""),
    MAIL_IMAP_HOST=(str, "shadow.mxrouting.net"),
    MAIL_IMAP_PORT=(int, 993),
    MAIL_SMTP_HOST=(str, "shadow.mxrouting.net"),
    MAIL_SMTP_PORT=(int, 465),
    TOKEN_ENC_KEY=(str),   # SEC-06: Fernet key for Discord token encryption (required, no default)
    KILLTRACKER_CLIENT_SECRET=(str, ""),   # SEC-07: shared secret baked into the desktop client; gates killtracker API endpoints. Empty = gate disabled (logs a warning).
)

DAPHNE_PORT = env('DAPHNE_PORT')
DAPHNE_IFACE = env('DAPHNE_IFACE')

DEBUG = env('DJANGO_DEBUG')
SECRET_KEY = env('SECRET_KEY')

ROOT_URLCONF = "app.main.urls"
ASGI_APPLICATION = "app.main.asgi.application"
AUTH_USER_MODEL = 'unifieduser.OrgPlayer'
#ADMIN_TOOLS_MENU = 'app.main.menu.CustomMenu'
#ADMIN_TOOLS_INDEX_DASHBOARD = 'app.main.dashboard.CustomIndexDashboard'
#ADMIN_TOOLS_APP_INDEX_DASHBOARD = 'app.main.dashboard.CustomAppIndexDashboard'
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cloudflare & SSL Security Headers ---
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_HSTS_SECONDS = 31536000 
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

REDIS_BASE_URI = "redis://:{}@{}/".format(env("REDIS_PASSWORD"), env("REDIS_HOST"))
GOOGLE_AI_API_KEY = env('GOOGLE_AI_API_KEY')
ANTHROPIC_API_KEY = env('ANTHROPIC_API_KEY')
GROQ_API_KEY      = env('GROQ_API_KEY')
DISCORD_BOT_DESCRIPTION = env('DISCORD_BOT_DESCRIPTION')
DISCORD_INTENTS = discord.Intents.all()

MAIL_USERNAME = env('MAIL_USERNAME')
MAIL_PASSWORD = env('MAIL_PASSWORD')
MAIL_IMAP_HOST = env('MAIL_IMAP_HOST')
MAIL_IMAP_PORT = env('MAIL_IMAP_PORT')
MAIL_SMTP_HOST = env('MAIL_SMTP_HOST')
MAIL_SMTP_PORT = env('MAIL_SMTP_PORT')
TOKEN_ENC_KEY = env('TOKEN_ENC_KEY')  # SEC-06
KILLTRACKER_CLIENT_SECRET = env('KILLTRACKER_CLIENT_SECRET')  # SEC-07

USE_TZ = True
BASE_DIR = Path(__file__).absolute().parent.parent

STATICFILES_DIRS = [
    BASE_DIR / "app" / "main" / "static", 
]

DATABASES = {
    "default": env.db_url("DATABASE_URI")
}

SUPERUSERS = [x.split(':') for x in env.list('SUPERUSERS')]

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin-allow-popups'

# --- CORS Hardening ---
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOWED_ORIGINS = [
    "https://blightveil.org",
    "https://www.blightveil.org",
]

CSRF_USE_SESSIONS = True
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS')

SESSION_COOKIE_AGE = 60*60*24*365  # one year

SITE_URL = 'https://blightveil.org/'

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
]

LOGIN_URL = '/user/index/'
LOGIN_REDIRECT_URL = LOGIN_URL
OTP_LOGIN_URL = "/user/otp/login/"
LOGOUT_URL = '/user/logout/'
LOGOUT_REDIRECT_URL = LOGIN_URL

# SEC-08: fail-closed default for DRF. Any endpoint intended to be public must set an
# explicit `permission_classes = [AllowAny]`. Verify the unifieduser/preferences viewsets
# and their consumers still behave after enabling this.
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# django-csp 3.8 settings (csp.middleware.CSPMiddleware)
CSP_DEFAULT_SRC = ("'self'",)
CSP_SCRIPT_SRC = (
    "'self'",
    "'unsafe-inline'",   # Cloudflare Rocket Loader injects inline scripts
    # SEC-05: 'unsafe-eval' removed. Prebuilt Quill does not need it; if the editor breaks,
    # pin the prebuilt quill.min.js dist rather than re-adding eval.
    "https://code.jquery.com",
    "https://cdnjs.cloudflare.com",
    "https://cdn.jsdelivr.net",
    "https://static.cloudflareinsights.com",
)
CSP_STYLE_SRC = (
    "'self'",
    "'unsafe-inline'",
    "https://cdnjs.cloudflare.com",
    "https://*.fontawesome.com",
    "https://cdn.jsdelivr.net",
    "https://fonts.googleapis.com",
)
CSP_FONT_SRC = (
    "'self'",
    "https://*.fontawesome.com",
    "https://cdn.jsdelivr.net",
    "https://cdnjs.cloudflare.com",
    "https://fonts.gstatic.com",
    "data:",
)
CSP_IMG_SRC = (
    "'self'",
    "data:",
)
CSP_CONNECT_SRC = (
    "'self'",
    "https://cloudflareinsights.com",
    "webpack:",  # Quill CDN bundle source map requests
)

INSTALLED_APPS = [
    "daphne",

    "django.contrib.auth",
    "django.contrib.contenttypes",
    "qsessions",
    "simple_history",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "django.contrib.admin",
    "django.contrib.admindocs",
    "django.contrib.humanize",

    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',

    "guardian",
    "corsheaders",
    "ordered_model",

    "rest_framework",
    "admin_auto_filters",

    "compressor",
    "django_select2",
    "crispy_forms",
    "crispy_bootstrap5",

    "app.celerytools",
    "django_celery_beat",

    "app.unifieduser",
    "app.org",
    "app.discordauth",

    "app.disfunction",
    "app.discordwebcms",

    "app.preferences",

    'app.sc_tracker',
    'app.infantryboard',
    'app.discordlogger',
    'app.candidacy',
    # app.inventory and app.merits retired — absorbed into app.quartermaster
    "app.orgevents.apps.OrgeventsConfig",
    "app.schedevents",
    "app.pilotboard",
    "app.mailclient",
    "app.killtracker",
    "app.leadership",
    "app.goals",
    "app.analytics",
    "app.quartermaster",
    "app.specialty.apps.SpecialtyConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware", # Enabled CSP
    "qsessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "app.unifieduser.middleware.CustomOTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csp",
            ],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = (
    "app.discordauth.models.DiscordAuthBackend",
    "django.contrib.auth.backends.ModelBackend",
    'guardian.backends.ObjectPermissionBackend',
)

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_L10N = False

STATIC_VOLUME_MOUNT = env('STATIC_VOLUME_MOUNT')
MEDIA_ROOT = os.path.join(STATIC_VOLUME_MOUNT, "media")
MEDIA_URL = env('MEDIA_URL')
STATIC_ROOT = os.path.join(STATIC_VOLUME_MOUNT, "static")
STATIC_URL = env('STATIC_URL')

STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    'compressor.finders.CompressorFinder',
]

DATA_UPLOAD_MAX_NUMBER_FIELDS = 888


CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}2".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
    "sessions": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}3".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
    "channels": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}4".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
    "celery": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}5".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
    "ratelimit": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}6".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
    "select2": {
        "BACKEND": "django_redis.cache.RedisCache",
        'LOCATION': "{}7".format(REDIS_BASE_URI),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    },
}


RATELIMIT_USE_CACHE = "ratelimit"


CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": ["{}4".format(REDIS_BASE_URI)],
            "capacity": 10000,
            "channel_capacity": {
                "websocket.send*": 10010,
            },
        },
    },
}


CELERY_BROKER_URL = "{}5".format(REDIS_BASE_URI)
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_RESULT_BACKEND = "{}5".format(REDIS_BASE_URI)
CELERY_ONCE = {
    'backend': 'celery_once.backends.Redis',
    'settings': {
        'url': "{}5".format(REDIS_BASE_URI),
        'default_timeout': 60 * 60 * 2  # 2 hours
    }
}


SESSION_ENGINE = "qsessions.backends.cached_db"
SESSION_CACHE_ALIAS = "sessions"
SESSION_SAVE_EVERY_REQUEST = True  # keep cache warm to avoid cached_db race on cold reads

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

SELECT2_CACHE_BACKEND = "sessions"
SELECT2_CSS = ['https://cdn.jsdelivr.net/npm/select2-bootstrap-5-theme@1.3.0/dist/select2-bootstrap-5-theme.min.css']
SELECT2_JS = ["https://code.jquery.com/jquery-3.7.1.min.js", "https://code.jquery.com/ui/1.13.2/jquery-ui.min.js"]
SELECT2_THEME = "bootstrap-5"


COMPRESS_PRECOMPILERS = (
    ('text/x-scss', 'django_libsass.SassCompiler'),
)
COMPRESS_OFFLINE = True

if not DEBUG:
    LIBSASS_OUTPUT_STYLE = 'compressed'
