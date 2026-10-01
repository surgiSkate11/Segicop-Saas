"""
WSGI config for sigecop project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from sigecop.observability import configure_observability

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sigecop.settings.prod")

application = get_wsgi_application()
configure_observability()
