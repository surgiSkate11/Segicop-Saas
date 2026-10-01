from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from apps.core import services
from apps.core.models import AlertaNotificacion


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class AlertasView(LoginRequiredMixin, View):
    template_name = "core/alertas.html"

    def _alertas(self, request):
        return AlertaNotificacion.objects.filter(
            organizacion_id__in=_organizaciones_del_usuario(request.user),
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
            organizacion_id__in=_organizaciones_del_usuario(request.user),
        )
        services.marcar_alerta_leida(alerta=alerta, actor=request.user)
        return redirect("core:alertas")


class ExportarAlertasView(LoginRequiredMixin, View):
    def get(self, request):
        organizaciones_ids = list(_organizaciones_del_usuario(request.user))
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


# Create your views here.
