from django.urls import path

from apps.cumplimiento.views import BandejaRevisionView, RequisitoDetalleView

app_name = "cumplimiento"

urlpatterns = [
    path("cumplimiento/bandeja/", BandejaRevisionView.as_view(), name="bandeja"),
    path("cumplimiento/requisitos/<uuid:pk>/", RequisitoDetalleView.as_view(), name="detalle"),
]