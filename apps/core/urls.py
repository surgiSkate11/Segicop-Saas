from django.urls import path

from apps.core.views import AlertasView, ExportarAlertasView

app_name = "core"

urlpatterns = [
    path("alertas/", AlertasView.as_view(), name="alertas"),
    path("alertas/<uuid:pk>/leer/", AlertasView.as_view(), name="alerta_leer"),
    path("reportes/alertas.csv", ExportarAlertasView.as_view(), name="exportar_alertas"),
]
