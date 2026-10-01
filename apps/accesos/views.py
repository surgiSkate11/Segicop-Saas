from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.accesos import services
from apps.accesos.forms import GaritaForm, SolicitarPermisoForm
from apps.accesos.models import EstadoPermiso, PermisoIngreso, TipoEvento
from apps.core.exceptions import DomainError
from apps.identidad.context_processors import organizacion_actual
from apps.personas.models import Persona
from apps.core.modals import exito_htmx, render_modal


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class SolicitarPermisoView(LoginRequiredMixin, View):
    """Modal HTMX (antes era una página completa)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect("personas:lista")
        return super().dispatch(request, *args, **kwargs)

    def _get_persona(self, request, persona_pk):
        organizaciones_ids = _organizaciones_del_usuario(request.user)
        return get_object_or_404(Persona, pk=persona_pk, organizacion_id__in=organizaciones_ids)

    def _modal(self, request, form, persona):
        return render_modal(
            request,
            form=form,
            titulo=f"Solicitar ingreso · {persona.nombre_completo}",
            accion_url=reverse("accesos:solicitar", args=[persona.pk]),
            boton="Solicitar",
        )

    def get(self, request, persona_pk):
        persona = self._get_persona(request, persona_pk)
        return self._modal(request, SolicitarPermisoForm(organizacion=persona.organizacion), persona)

    def post(self, request, persona_pk):
        persona = self._get_persona(request, persona_pk)
        form = SolicitarPermisoForm(request.POST, organizacion=persona.organizacion)
        if not form.is_valid():
            return self._modal(request, form, persona)

        try:
            permiso = services.solicitar_permiso_ingreso(
                persona=persona,
                instalacion=form.cleaned_data["instalacion"],
                actor=request.user,
                fecha_inicio_vigencia=form.cleaned_data["fecha_inicio_vigencia"],
                fecha_fin_vigencia=form.cleaned_data["fecha_fin_vigencia"],
            )
        except DomainError as error:
            form.add_error(None, str(error))
            return self._modal(request, form, persona)

        if permiso.estado == EstadoPermiso.APROBADO:
            mensaje = "Permiso aprobado."
        else:
            mensaje = f"Permiso rechazado: {permiso.motivo}"
        return exito_htmx(request, mensaje=mensaje, url=reverse("accesos:detalle", args=[permiso.pk]))


class PermisoListView(LoginRequiredMixin, ListView):
    model = PermisoIngreso
    template_name = "accesos/lista.html"
    context_object_name = "permisos"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return (
            PermisoIngreso.objects.filter(organizacion_id__in=organizaciones_ids)
            .select_related("persona", "instalacion")
            .order_by("-created_at")
        )


class PermisoDetalleView(LoginRequiredMixin, View):
    def get(self, request, pk):
        organizaciones_ids = _organizaciones_del_usuario(request.user)
        permiso = get_object_or_404(
            PermisoIngreso.objects.select_related("persona", "instalacion"),
            pk=pk,
            organizacion_id__in=organizaciones_ids,
        )
        return render(request, "accesos/detalle.html", {"permiso": permiso, "eventos": permiso.eventos.all()})


class GaritaView(LoginRequiredMixin, View):
    template_name = "accesos/garita.html"

    def get(self, request):
        organizacion = organizacion_actual(request).get("organizacion_actual")
        form = GaritaForm(organizacion=organizacion)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        organizacion = organizacion_actual(request).get("organizacion_actual")
        form = GaritaForm(request.POST, organizacion=organizacion)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return render(request, self.template_name, {"form": form})
        try:
            evento = services.registrar_evento_acceso(
                codigo=form.cleaned_data["codigo"],
                instalacion=form.cleaned_data["instalacion"],
                tipo=form.cleaned_data["tipo"],
                actor=request.user,
            )
        except DomainError as error:
            messages.error(request, str(error))
            return render(request, self.template_name, {"form": GaritaForm(organizacion=organizacion)})
        if evento.tipo == TipoEvento.DENEGADO:
            messages.error(request, f"Acceso denegado: {evento.motivo_denegacion}")
        else:
            messages.success(request, f"{evento.get_tipo_display()} registrada para {evento.persona.nombre_completo}.")
        return render(
            request, self.template_name, {"form": GaritaForm(organizacion=organizacion), "ultimo_evento": evento}
        )
