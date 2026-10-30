from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.empresas.models import EmpresaContratista
from apps.identidad.mixins import RequierePermisoMixin
from apps.trabajos import services
from apps.trabajos.forms import CrearPermisoTrabajoForm
from apps.trabajos.models import PermisoTrabajo
from apps.core.modals import exito_htmx, render_modal


class PermisoTrabajoListView(RequierePermisoMixin, ListView):
    accion_requerida = "ver_registros"
    model = PermisoTrabajo
    template_name = "trabajos/lista.html"
    context_object_name = "permisos"
    paginate_by = 25

    def get_queryset(self):
        return (
            PermisoTrabajo.objects.filter(organizacion_id__in=self.organizaciones_permitidas())
            .select_related("empresa", "instalacion")
            .order_by("-created_at")
        )


class CrearPermisoTrabajoView(RequierePermisoMixin, View):
    """Modal HTMX (antes era una página completa)."""

    accion_requerida = "crear_trabajo"

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect("empresas:lista")
        return super().dispatch(request, *args, **kwargs)

    def _get_empresa(self, request, empresa_pk):
        return get_object_or_404(
            EmpresaContratista, pk=empresa_pk, organizacion_id__in=self.organizaciones_permitidas()
        )

    def _modal(self, request, form, empresa):
        return render_modal(
            request,
            form=form,
            titulo=f"Nuevo permiso de trabajo · {empresa.razon_social}",
            accion_url=reverse("trabajos:crear", args=[empresa.pk]),
            boton="Crear permiso",
        )

    def get(self, request, empresa_pk):
        empresa = self._get_empresa(request, empresa_pk)
        return self._modal(request, CrearPermisoTrabajoForm(empresa=empresa), empresa)

    def post(self, request, empresa_pk):
        empresa = self._get_empresa(request, empresa_pk)
        form = CrearPermisoTrabajoForm(request.POST, empresa=empresa)
        if not form.is_valid():
            return self._modal(request, form, empresa)

        try:
            permiso = services.crear_permiso_trabajo(
                empresa=empresa,
                instalacion=form.cleaned_data["instalacion"],
                actividad=form.cleaned_data["actividad"],
                nivel_riesgo=form.cleaned_data["nivel_riesgo"],
                integrantes=form.cleaned_data["integrantes"],
                responsable_cuadrilla=form.cleaned_data["responsable_cuadrilla"],
                actor=request.user,
                fecha_inicio_programada=form.cleaned_data["fecha_inicio_programada"],
                fecha_fin_programada=form.cleaned_data["fecha_fin_programada"],
            )
        except DomainError as error:
            form.add_error(None, str(error))
            return self._modal(request, form, empresa)

        if permiso.estado == "RECHAZADO":
            mensaje = f"Permiso rechazado: {permiso.motivo}"
        else:
            mensaje = "Permiso de trabajo creado y listo para abrir."
        return exito_htmx(request, mensaje=mensaje, url=reverse("trabajos:detalle", args=[permiso.pk]))


class PermisoTrabajoDetalleView(RequierePermisoMixin, View):
    """Ver exige `ver_registros`; abrir, suspender, reanudar y cerrar exigen `gestionar_trabajo`."""

    accion_requerida = {"GET": "ver_registros", "POST": "gestionar_trabajo"}

    def _get_permiso(self, request, pk):
        return get_object_or_404(
            PermisoTrabajo.objects.select_related("empresa", "instalacion").prefetch_related("integrantes"),
            pk=pk,
            organizacion_id__in=self.organizaciones_permitidas(),
        )

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
