from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.empresas.models import EmpresaContratista
from apps.trabajos import services
from apps.trabajos.forms import CrearPermisoTrabajoForm
from apps.trabajos.models import PermisoTrabajo


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class PermisoTrabajoListView(LoginRequiredMixin, ListView):
    model = PermisoTrabajo
    template_name = "trabajos/lista.html"
    context_object_name = "permisos"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return PermisoTrabajo.objects.filter(organizacion_id__in=organizaciones_ids).select_related("empresa", "instalacion").order_by("-created_at")


class CrearPermisoTrabajoView(LoginRequiredMixin, View):
    template_name = "trabajos/crear.html"

    def _get_empresa(self, empresa_pk):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return get_object_or_404(EmpresaContratista, pk=empresa_pk, organizacion_id__in=organizaciones_ids)

    def get(self, request, empresa_pk):
        empresa = self._get_empresa(empresa_pk)
        form = CrearPermisoTrabajoForm(empresa=empresa)
        return render(request, self.template_name, {"empresa": empresa, "form": form})

    def post(self, request, empresa_pk):
        empresa = self._get_empresa(empresa_pk)
        form = CrearPermisoTrabajoForm(request.POST, empresa=empresa)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return render(request, self.template_name, {"empresa": empresa, "form": form})
        try:
            permiso = services.crear_permiso_trabajo(
                empresa=empresa, instalacion=form.cleaned_data["instalacion"],
                actividad=form.cleaned_data["actividad"], nivel_riesgo=form.cleaned_data["nivel_riesgo"],
                integrantes=form.cleaned_data["integrantes"], responsable_cuadrilla=form.cleaned_data["responsable_cuadrilla"],
                actor=request.user, fecha_inicio_programada=form.cleaned_data["fecha_inicio_programada"],
                fecha_fin_programada=form.cleaned_data["fecha_fin_programada"],
            )
        except DomainError as error:
            messages.error(request, str(error))
            return render(request, self.template_name, {"empresa": empresa, "form": form})
        if permiso.estado == "RECHAZADO":
            messages.error(request, f"Permiso rechazado: {permiso.motivo}")
        else:
            messages.success(request, "Permiso de trabajo creado y listo para abrir.")
        return redirect(reverse("trabajos:detalle", args=[permiso.pk]))


class PermisoTrabajoDetalleView(LoginRequiredMixin, View):
    def _get_permiso(self, request, pk):
        organizaciones_ids = _organizaciones_del_usuario(request.user)
        return get_object_or_404(PermisoTrabajo.objects.select_related("empresa", "instalacion").prefetch_related("integrantes"), pk=pk, organizacion_id__in=organizaciones_ids)

    def get(self, request, pk):
        permiso = self._get_permiso(request, pk)
        return render(request, "trabajos/detalle.html", {"permiso": permiso})

    def post(self, request, pk):
        permiso = self._get_permiso(request, pk)
        accion = request.POST.get("accion")
        motivo = request.POST.get("motivo", "")
        try:
            if accion == "abrir":
                services.abrir_permiso_trabajo(permiso=permiso, actor=request.user)
                messages.success(request, "Permiso abierto.")
            elif accion == "suspender":
                services.suspender_permiso_trabajo(permiso=permiso, motivo=motivo, actor=request.user)
                messages.success(request, "Permiso suspendido.")
            elif accion == "reanudar":
                services.reanudar_permiso_trabajo(permiso=permiso, actor=request.user)
                messages.success(request, "Permiso reanudado.")
            elif accion == "cerrar":
                services.cerrar_permiso_trabajo(permiso=permiso, motivo=motivo, actor=request.user)
                messages.success(request, "Permiso cerrado.")
            else:
                messages.error(request, "Acción no reconocida.")
        except DomainError as error:
            messages.error(request, str(error))
        return redirect(reverse("trabajos:detalle", args=[permiso.pk]))