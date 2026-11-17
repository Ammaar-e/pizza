"""Django settings for the Pizza Franchise Platform.

All secrets come from environment variables. Nothing secret is hard-coded here;
development defaults are only used when DJANGO_DEBUG=1.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in (os.environ.get(name, default) or "").split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
TESTING = env_bool("DJANGO_TESTING", False) or "pytest" in sys.modules

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or TESTING:
        SECRET_KEY = "dev-only-insecure-secret-key-do-not-use-in-production"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "corsheaders",
    "channels",
    "apps.core",
    "apps.organization",
    "apps.accounts",
    "apps.catalog",
    "apps.orders",
    "apps.payments",
    "apps.kitchen",
    "apps.integrations",
    "apps.reporting",
    "apps.api",
    "apps.devices",
    "apps.promotions",
    "apps.dispatch",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "pizza"),
        "USER": env("POSTGRES_USER", "pizza"),
        "PASSWORD": env("POSTGRES_PASSWORD", "pizza" if (DEBUG or TESTING) else None),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        "OPTIONS": {"sslmode": env("POSTGRES_SSLMODE", "prefer")},
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        if TESTING
        else {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}
    )
}

CHANNEL_LAYERS = {
    "default": (
        {"BACKEND": "channels.layers.InMemoryChannelLayer"}
        if TESTING
        else {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [REDIS_URL]}}
    )
}

# --- Celery -----------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = TESTING or env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "apps.integrations.tasks.*": {"queue": "integrations"},
}
CELERY_BEAT_SCHEDULE = {
    "integration-ack-deadline-sweeper": {
        "task": "apps.integrations.tasks.sweep_pending_provider_acknowledgements",
        "schedule": 60.0,
    },
    "retry-failed-webhook-events": {
        "task": "apps.integrations.tasks.retry_failed_webhook_events",
        "schedule": 120.0,
    },
    "device-heartbeat-monitor": {
        "task": "apps.devices.tasks.monitor_device_heartbeats",
        "schedule": 60.0,
    },
    "device-heartbeat-prune": {
        "task": "apps.devices.tasks.prune_device_heartbeats",
        "schedule": 3600.0,
    },
    "promotion-status-refresh": {
        "task": "apps.promotions.tasks.refresh_promotion_statuses",
        "schedule": 60.0,
    },
    "retry-outbound-webhooks": {
        "task": "apps.api.tasks.retry_outbound_webhooks",
        "schedule": 60.0,
    },
}

# --- REST framework ---------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.DeviceAwareJWTAuthentication",
        "apps.api.authentication.ApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.core.throttling.UserOrClientRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user_or_client": env("API_RATE_LIMIT", "600/min"),
        "anon": env("API_ANON_RATE_LIMIT", "60/min"),
        "login": env("API_LOGIN_RATE_LIMIT", "20/min"),
        "webhook": env("API_WEBHOOK_RATE_LIMIT", "1200/min"),
    },
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MINUTES", "30"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=int(env("JWT_REFRESH_HOURS", "16"))),
    "ROTATE_REFRESH_TOKENS": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": SECRET_KEY,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Pizza Franchise Platform API",
    "DESCRIPTION": (
        "Centralized franchise POS, order intake, kitchen and integration API. "
        "Versioned under /api/v1/. External applications (call centre, website back-ends, "
        "future mobile apps) authenticate with `Authorization: Api-Key <key>`; staff "
        "applications use JWT bearer tokens."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "Authorization",
                           "description": "Format: `Api-Key <prefix>.<secret>`"},
        }
    },
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
CORS_ALLOW_HEADERS = (
    "accept", "authorization", "content-type", "idempotency-key", "x-device-token", "x-requested-with",
)

# --- Field encryption for integration credentials -------------------------
# Comma-separated list of Fernet keys; the first is used for encryption, all for
# decryption (supports key rotation).
FIELD_ENCRYPTION_KEYS = env_list("FIELD_ENCRYPTION_KEYS", "")
if not FIELD_ENCRYPTION_KEYS:
    if DEBUG or TESTING:
        # Deterministic dev/test key. Production refuses to start without a key.
        FIELD_ENCRYPTION_KEYS = ["ZGV2LW9ubHktZmllbGQta2V5LWRvLW5vdC11c2UtISE="]
    else:
        raise RuntimeError("FIELD_ENCRYPTION_KEYS must be set in production")

# Public base URL used to build webhook URLs shown to franchisees.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "http://localhost")

# Provider HTTP timeouts (seconds)
PROVIDER_HTTP_TIMEOUT = float(env("PROVIDER_HTTP_TIMEOUT", "15"))

# --- Security hardening ----------------------------------------------------
if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)  # nginx handles redirect

DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "apps.core.logging.RedactSecretsFilter"}},
    "formatters": {
        "json": {"()": "apps.core.logging.JsonFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain" if DEBUG else "json",
            "filters": ["redact"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "integrations": {"level": env("INTEGRATION_LOG_LEVEL", "INFO")},
    },
}
