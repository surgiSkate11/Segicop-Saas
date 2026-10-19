from django.urls import path, re_path

from apps.antecedentes.views import (
    AutorizarVerificacionModalView,
    BandejaAntecedentesView,
    DescargarDocumentoView,
    IniciarVerificacionView,
    RevocarAutorizacionModalView,
    VerificacionDetalleView,
)
from apps.antecedentes.api import (
    CargarDocumentoAPIView,
    ContinuarVerificacionAPIView,
    CrearVerificacionAPIView,
    DetalleVerificacionAPIView,
    DocumentoVerificacionAPIView,
    IniciarVerificacionAPIView,
    RevisionVerificacionAPIView,
    VerificacionDetalleAPIView,
)

app_name = "antecedentes"

# Identificador de la verificación en las rutas de la API de S10, que aceptan la barra final o no.
UUID = r"(?P<pk>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
API = r"^api/v1/verificaciones-antecedentes"

urlpatterns = [
    path("antecedentes/bandeja/", BandejaAntecedentesView.as_view(), name="bandeja"),
    path("antecedentes/iniciar/<uuid:persona_pk>/", IniciarVerificacionView.as_view(), name="iniciar"),
    path("antecedentes/<uuid:pk>/", VerificacionDetalleView.as_view(), name="detalle"),
    path("antecedentes/documentos/<uuid:pk>/descargar/", DescargarDocumentoView.as_view(), name="descargar-documento"),
    path("antecedentes/<uuid:pk>/autorizar/", AutorizarVerificacionModalView.as_view(), name="autorizar"),
    path(
        "antecedentes/<uuid:pk>/revocar-autorizacion/",
        RevocarAutorizacionModalView.as_view(),
        name="revocar-autorizacion",
    ),
    # Contrato anterior, se mantiene.
    path("api/v1/antecedentes/personas/<uuid:persona_pk>/consultar/", IniciarVerificacionAPIView.as_view(), name="api-iniciar"),
    path("api/v1/antecedentes/<uuid:pk>/", VerificacionDetalleAPIView.as_view(), name="api-detalle"),
    path(
        "api/v1/antecedentes/consultas/<uuid:consulta_pk>/documento/",
        CargarDocumentoAPIView.as_view(),
        name="api-cargar-documento",
    ),
    # S10.
    re_path(rf"{API}/?$", CrearVerificacionAPIView.as_view(), name="api-v1-crear"),
    re_path(rf"{API}/{UUID}/?$", DetalleVerificacionAPIView.as_view(), name="api-v1-detalle"),
    re_path(rf"{API}/{UUID}/continuar/?$", ContinuarVerificacionAPIView.as_view(), name="api-v1-continuar"),
    re_path(rf"{API}/{UUID}/documento/?$", DocumentoVerificacionAPIView.as_view(), name="api-v1-documento"),
    re_path(rf"{API}/{UUID}/revision/?$", RevisionVerificacionAPIView.as_view(), name="api-v1-revision"),
]
