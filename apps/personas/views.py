from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx, render_modal
from apps.identidad.mixins import EnviarAccesoModalView, RequierePermisoMixin
from apps.identidad.permisos import organizacion_con_permiso
from apps.personas import services
from apps.personas.forms import AsignarPersonaForm, PersonaForm
from apps.personas.models import Persona


class PersonaListView(RequierePermisoMixin, ListView):
    accion_requerida = "ver_registros"
    model = Persona
    template_name = "personas/lista.html"
    context_object_name = "personas"
    paginate_by = 25

    def get_queryset(self):
        return (
            Persona.objects.filter(organizacion_id__in=self.organizaciones_permitidas())
            .select_related("usuario")
            .prefetch_related("asignaciones__empresa")
            .order_by("apellidos", "nombres")
        )


class _ModalView(RequierePermisoMixin, View):
    """Base común: los modales son fragmentos HTMX, no páginas completas."""

    accion_requerida = "editar_registros"

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect("personas:lista")
        return super().dispatch(request, *args, **kwargs)

    def _persona(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(Persona, pk=pk, organizacion_id__in=self.organizaciones_permitidas())


class PersonaModalView(_ModalView):
    """Crear (sin pk) o editar (con pk) una persona."""

    def _modal(self, request, form, persona):
        if persona:
            return render_modal(
                request,
                form=form,
                titulo="Editar persona",
                accion_url=reverse("personas:editar", args=[persona.pk]),
                boton="Guardar cambios",
            )
        return render_modal(
            request, form=form, titulo="Nueva persona", accion_url=reverse("personas:crear"), boton="Registrar persona"
        )

    def get(self, request, pk=None):
        persona = self._persona(request, pk)
        initial = None
        if persona:
            initial = {
                "tipo_documento": persona.tipo_documento,
                "numero_documento": persona.numero_documento,
                "nombres": persona.nombres,
                "apellidos": persona.apellidos,
                "fecha_nacimiento": persona.fecha_nacimiento,
                "contacto_telefono": persona.contacto_telefono,
                "contacto_email": persona.contacto_email,
                "nacionalidad": persona.nacionalidad,
                "domicilio": persona.domicilio,
                "telefono_emergencia": persona.telefono_emergencia,
            }
        return self._modal(request, PersonaForm(initial=initial, creando=persona is None), persona)

    def post(self, request, pk=None):
        persona = self._persona(request, pk)
        form = PersonaForm(request.POST, creando=persona is None)
        if form.is_valid():
            try:
                if persona:
                    services.actualizar_persona(persona=persona, actor=request.user, **form.cleaned_data)
                    mensaje = "Persona actualizada."
                else:
                    organizacion = organizacion_con_permiso(request.user, self.accion_actual)
                    if organizacion is None:
                        raise DomainError("Tu usuario no tiene una organización asignada.")
                    services.registrar_persona(organizacion=organizacion, actor=request.user, **form.cleaned_data)
                    mensaje = "Persona registrada."
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(request, mensaje=mensaje, url=reverse("personas:lista"))
        return self._modal(request, form, persona)


class AsignarPersonaModalView(_ModalView):
    """Vincula una persona a una empresa (requisito previo para poder habilitarla)."""

    def _modal(self, request, form, persona):
        return render_modal(
            request,
            form=form,
            titulo=f"Asignar a empresa · {persona.nombre_completo}",
            accion_url=reverse("personas:asignar", args=[persona.pk]),
            boton="Asignar",
        )

    def get(self, request, pk):
        persona = self._persona(request, pk)
        return self._modal(request, AsignarPersonaForm(organizacion=persona.organizacion), persona)

    def post(self, request, pk):
        persona = self._persona(request, pk)
        form = AsignarPersonaForm(request.POST, organizacion=persona.organizacion)
        if form.is_valid():
            try:
                services.asignar_persona_a_empresa(
                    persona=persona,
                    empresa=form.cleaned_data["empresa"],
                    cargo_o_actividad=form.cleaned_data["cargo_o_actividad"],
                    fecha_inicio=form.cleaned_data["fecha_inicio"],
                    fecha_fin=form.cleaned_data["fecha_fin"],
                    actor=request.user,
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(request, mensaje="Persona asignada a la empresa.", url=reverse("personas:lista"))
        return self._modal(request, form, persona)


class EnviarAccesoPersonaView(EnviarAccesoModalView):
    url_lista = "personas:lista"
    url_accion = "personas:enviar_acceso"

    def get_titular(self, request, pk):
        return get_object_or_404(Persona, pk=pk, organizacion_id__in=self.organizaciones_permitidas())
