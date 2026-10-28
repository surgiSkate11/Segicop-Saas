from django.urls import path

from apps.reportes.views import ExportarReporteView, IndiceReportesView, ReporteView

app_name = "reportes"

urlpatterns = [
    path("reportes/", IndiceReportesView.as_view(), name="indice"),
    path(
        "reportes/documentos-trabajadores/",
        ReporteView.as_view(reporte="documentos"),
        name="documentos_trabajadores",
    ),
    path(
        "reportes/documentos-trabajadores.xlsx",
        ExportarReporteView.as_view(reporte="documentos"),
        name="documentos_trabajadores_excel",
    ),
    path(
        "reportes/informacion-trabajadores/",
        ReporteView.as_view(reporte="informacion"),
        name="informacion_trabajadores",
    ),
    path(
        "reportes/informacion-trabajadores.xlsx",
        ExportarReporteView.as_view(reporte="informacion"),
        name="informacion_trabajadores_excel",
    ),
]
