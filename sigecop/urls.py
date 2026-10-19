from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.core.urls", namespace="core")),
    path("", include("apps.identidad.urls", namespace="identidad")),
    path("", include("apps.empresas.urls", namespace="empresas")),
    path("", include("apps.personas.urls", namespace="personas")),
    path("", include("apps.cumplimiento.urls", namespace="cumplimiento")),
    path("", include("apps.antecedentes.urls", namespace="antecedentes")),
    path("", include("apps.accesos.urls", namespace="accesos")),
    path("", include("apps.trabajos.urls", namespace="trabajos")),
    path("portal/", include("apps.portal.urls", namespace="portal")),
    # API
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    # API Docs
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
