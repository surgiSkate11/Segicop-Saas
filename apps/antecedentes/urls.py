from django.urls import path

from apps.antecedentes.views import BandejaAntecedentesView, IniciarVerificacionView, VerificacionDetalleView

app_name = "antecedentes"

urlpatterns = [
    path("antecedentes/bandeja/", BandejaAntecedentesView.as_view(), name="bandeja"),
    path("antecedentes/iniciar/<uuid:persona_pk>/", IniciarVerificacionView.as_view(), name="iniciar"),
    path("antecedentes/<uuid:pk>/", VerificacionDetalleView.as_view(), name="detalle"),
]
