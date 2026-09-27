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


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class SolicitarPermisoView(LoginRequiredMixin, View):
    template_name = "accesos/solicitar.html"

    def _get_persona(self, persona_pk):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return get_object_or_404(Persona, pk=persona_pk, organizacion_id__in=organizaciones_ids)

    def get(self, request, persona_pk):
        persona = self._get_persona(persona_pk)
        form = SolicitarPermisoForm(organizacion=persona.organizacion)
        return render(request, self.template_name, {"persona": persona, "form": form})

    def post(self, request, persona_pk):
        persona = self._get_persona(persona_pk)
        form = SolicitarPermisoForm(request.POST, organizacion=persona.organizacion)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return render(request, self.template_name, {"persona": persona, "form": form})
        try:
            permiso = services.solicitar_permiso_ingreso(
                persona=persona, instalacion=form.cleaned_data["instalacion"], actor=request.user,
                fecha_inicio_vigencia=form.cleaned_data["fecha_inicio_vigencia"],
                fecha_fin_vigencia=form.cleaned_data["fecha_fin_vigencia"],
            )
        except DomainError as error:
            messages.error(request, str(error))
            return render(request, self.template_name, {"persona": persona, "form": form})
        if permiso.estado == EstadoPermiso.APROBADO:
            messages.success(request, "Permiso aprobado.")
        else:
            messages.error(request, f"Permiso rechazado: {permiso.motivo}")
        return redirect(reverse("accesos:detalle", args=[permiso.pk]))


class PermisoListView(LoginRequiredMixin, ListView):
    model = PermisoIngreso
    template_name = "accesos/lista.html"
    context_object_name = "permisos"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return PermisoIngreso.objects.filter(organizacion_id__in=organizaciones_ids).select_related("persona", "instalacion").order_by("-created_at")


class PermisoDetalleView(LoginRequiredMixin, View):
    def get(self, request, pk):
        organizaciones_ids = _organizaciones_del_usuario(request.user)
        permiso = get_object_or_404(PermisoIngreso.objects.select_related("persona", "instalacion"), pk=pk, organizacion_id__in=organizaciones_ids)
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
                codigo=form.cleaned_data["codigo"], instalacion=form.cleaned_data["instalacion"],
                tipo=form.cleaned_data["tipo"], actor=request.user,
            )
        except DomainError as error:
            messages.error(request, str(error))
            return render(request, self.template_name, {"form": GaritaForm(organizacion=organizacion)})
        if evento.tipo == TipoEvento.DENEGADO:
            messages.error(request, f"Acceso denegado: {evento.motivo_denegacion}")
        else:
            messages.success(request, f"{evento.get_tipo_display()} registrada para {evento.persona.nombre_completo}.")
        return render(request, self.template_name, {"form": GaritaForm(organizacion=organizacion), "ultimo_evento": evento})