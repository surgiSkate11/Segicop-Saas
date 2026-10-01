from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

# En dev, correo a consola en vez de proveedor real.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Celery/Redis se agregan en la Fase 5 (job de vencimientos), todavía no.
