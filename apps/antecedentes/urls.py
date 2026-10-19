from django.urls import path

from apps.antecedentes.views import (
    AutorizarVerificacionModalView,
    BandejaAntecedentesView,
    DescargarDocumentoView,
    IniciarVerificacionView,
    VerificacionDetalleView,
)
from apps.antecedentes.api import CargarDocumentoAPIView, IniciarVerificacionAPIView, VerificacionDetalleAPIView

app_name = "antecedentes"

urlpatterns = [
    path("antecedentes/bandeja/", BandejaAntecedentesView.as_view(), name="bandeja"),
    path("antecedentes/iniciar/<uuid:persona_pk>/", IniciarVerificacionView.as_view(), name="iniciar"),
    path("antecedentes/<uuid:pk>/", VerificacionDetalleView.as_view(), name="detalle"),
    path("antecedentes/documentos/<uuid:pk>/descargar/", DescargarDocumentoView.as_view(), name="descargar-documento"),
    path("antecedentes/<uuid:pk>/autorizar/", AutorizarVerificacionModalView.as_view(), name="autorizar"),
    path("api/v1/antecedentes/personas/<uuid:persona_pk>/consultar/", IniciarVerificacionAPIView.as_view(), name="api-iniciar"),
    path("api/v1/antecedentes/<uuid:pk>/", VerificacionDetalleAPIView.as_view(), name="api-detalle"),
    path(
        "api/v1/antecedentes/consultas/<uuid:consulta_pk>/documento/",
        CargarDocumentoAPIView.as_view(),
        name="api-cargar-documento",
    ),
]
