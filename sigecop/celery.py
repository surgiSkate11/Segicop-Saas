import os

from celery import Celery

from sigecop.observability import configure_observability

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sigecop.settings.dev")

app = Celery("sigecop")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
configure_observability()
