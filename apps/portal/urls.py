from django.urls import path

from apps.portal.views import EmpresaPortalView, InicioPortalView, PersonaPortalView

app_name = "portal"

urlpatterns = [
    path("", InicioPortalView.as_view(), name="inicio"),
    path("empresa/", EmpresaPortalView.as_view(), name="empresa"),
    path("persona/", PersonaPortalView.as_view(), name="persona"),
]
