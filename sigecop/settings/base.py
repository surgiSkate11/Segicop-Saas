"""
Settings compartidos por dev/prod. Nada de secretos hardcodeados salvo
un fallback de desarrollo: en producción se sobreescriben por variable
de entorno (django-environ).
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")  # no-op si el archivo no existe

SECRET_KEY = env(
    "DJANGO_SECRET_KEY",
    default="django-insecure-(*ym#pqbynvdybpmz+j2v@84o5!#wci2#78aygm03r)8hry9*5",
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Terceros
    "rest_framework",
    "drf_spectacular",
    "django_htmx",
    "django_otp",
    "django_otp.plugins.otp_totp",
    # Apps propias
    "apps.core",
    "apps.identidad",
    "apps.empresas",
    "apps.personas",
    "apps.cumplimiento",
    "apps.antecedentes",
    "apps.accesos",
    "apps.trabajos",
    "apps.portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "apps.portal.middleware.AccesoPortalMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "sigecop.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.identidad.context_processors.organizacion_actual",
            ],
        },
    },
]

WSGI_APPLICATION = "sigecop.wsgi.application"
ASGI_APPLICATION = "sigecop.asgi.application"

AUTH_USER_MODEL = "identidad.Usuario"

# Login con RUC/cédula (cuentas de portal) o correo (personal interno).
AUTHENTICATION_BACKENDS = ["apps.identidad.backends.NombreUsuarioOCorreoBackend"]

# Vigencia del enlace de activación de cuentas de portal (y de restablecimiento): 72 h.
PASSWORD_RESET_TIMEOUT = 72 * 60 * 60

# URL pública usada para armar los enlaces de los correos (no hay request en los servicios).
SIGECOP_SITIO_URL = env("SIGECOP_SITIO_URL", default="http://localhost:8000")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="SIGECOP <no-responder@sigecop.local>")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/Guayaquil"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 25,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SIGECOP API",
    "DESCRIPTION": "Sistema Integral de Gestión de Contratistas y Permisos",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGIN_URL = "identidad:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "identidad:login"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "procesar-vencimientos-diario": {
        "task": "apps.core.tasks.procesar_vencimientos",
        "schedule": 86400.0,
    },
}

# Antecedentes penales (consulta asistida al portal oficial; nunca se resuelve el CAPTCHA).
# "asistido": comprueba el portal y deja el caso listo para que una persona lo consulte; "manual": solo carga manual.
ANTECEDENTES_CONECTOR_MODE = env("ANTECEDENTES_CONECTOR_MODE", default="asistido")
# False: la comprobación corre en la misma petición (no requiere worker). True: se encola en Celery.
ANTECEDENTES_CONSULTA_ASINCRONA = env.bool("ANTECEDENTES_CONSULTA_ASINCRONA", default=False)
ANTECEDENTES_SONDEO_TIMEOUT = 5  # segundos por intento
ANTECEDENTES_ESPERAS_REINTENTO = (2, 5)  # tiempo agotado o 5xx: máximo dos reintentos, espera incremental
ANTECEDENTES_CIRCUITO_UMBRAL = 3  # fallos seguidos que pausan la fuente
ANTECEDENTES_CIRCUITO_PAUSA = 600  # segundos de pausa; es la hora que se indica para reintentar

OTEL_ENABLED = env.bool("OTEL_ENABLED", default=False)
OTEL_SERVICE_NAME = env("OTEL_SERVICE_NAME", default="sigecop")
OTEL_EXPORTER_OTLP_ENDPOINT = env("OTEL_EXPORTER_OTLP_ENDPOINT", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "sigecop.logging.JsonFormatter"},
    },
    "handlers": {
        "console_json": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "django": {"handlers": ["console_json"], "level": "INFO", "propagate": False},
        "sigecop": {"handlers": ["console_json"], "level": "INFO", "propagate": False},
    },
}
