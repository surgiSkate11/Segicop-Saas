"""
Alert views. They live in `identidad` (not `core`) because they enforce the role matrix of
`identidad.permisos`, and `core` must not depend on later apps (dependencies only go backwards).
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core import services
from apps.core.models import AlertaNotificacion
from apps.identidad.mixins import RequierePermisoMixin


class AlertasView(RequierePermisoMixin, View):
    accion_requerida = "ver_alertas"
    template_name = "core/alertas.html"

    def _alertas(self, request):
        return AlertaNotificacion.objects.filter(
            organizacion_id__in=self.organizaciones_permitidas(),
        ).order_by("estado", "-created_at")

    def get(self, request):
        alertas = self._alertas(request)
        agrupadas = {}
        for alerta in alertas:
            agrupadas.setdefault(alerta.agrupador, []).append(alerta)
        return render(request, self.template_name, {"agrupadas": agrupadas})

    def post(self, request, pk):
        alerta = get_object_or_404(
            AlertaNotificacion,
            pk=pk,
            organizacion_id__in=self.organizaciones_permitidas(),
        )
        services.marcar_alerta_leida(alerta=alerta, actor=request.user)
        return redirect("identidad:alertas")


class ExportarAlertasView(RequierePermisoMixin, View):
    accion_requerida = "exportar_reportes"

    def get(self, request):
        organizaciones_ids = list(self.organizaciones_permitidas())
        estado = request.GET.get("estado") or None
        formato = request.GET.get("formato", "csv").lower()
        if formato == "xlsx":
            contenido, _ = services.exportar_alertas_xlsx(
                organizaciones_ids=organizaciones_ids, actor=request.user, estado=estado
            )
            respuesta = HttpResponse(
                contenido,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            respuesta["Content-Disposition"] = 'attachment; filename="alertas.xlsx"'
            return respuesta

        contenido, _ = services.exportar_alertas_csv(
            organizaciones_ids=organizaciones_ids, actor=request.user, estado=estado
        )
        respuesta = HttpResponse(contenido, content_type="text/csv; charset=utf-8")
        respuesta["Content-Disposition"] = 'attachment; filename="alertas.csv"'
        return respuesta
