"""
T2: usuarios internos de la organización. Solo la acción `gestionar_usuarios` (administrador del
cliente) abre estas vistas, y cada consulta filtra explícitamente por las organizaciones donde el
usuario tiene esa acción: un rol de otra organización es 404.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx, render_modal
from apps.identidad import services
from apps.identidad.forms import DesactivarRolForm, InvitarUsuarioForm
from apps.identidad.mixins import RequierePermisoMixin
from apps.identidad.models import RolAsignado, TipoCuenta
from apps.identidad.permisos import ROLES_INTERNOS, organizacion_con_permiso

ACCION = "gestionar_usuarios"


class _UsuariosMixin(RequierePermisoMixin):
    accion_requerida = ACCION

    def roles_internos(self):
        return RolAsignado.objects.filter(
            organizacion_id__in=self.organizaciones_permitidas(),
            rol__codigo__in=ROLES_INTERNOS,
            usuario__tipo_cuenta=TipoCuenta.INTERNA,
        ).select_related("usuario", "rol", "organizacion")

    def rol_asignado(self, pk):
        return get_object_or_404(self.roles_internos(), pk=pk)


class _ModalMixin(_UsuariosMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect("identidad:usuarios")
        return super().dispatch(request, *args, **kwargs)


class UsuarioListView(_UsuariosMixin, ListView):
    template_name = "identidad/usuarios.html"
    context_object_name = "roles"
    paginate_by = 50

    def get_queryset(self):
        return self.roles_internos().order_by("-activo", "usuario__nombre_completo", "rol__nombre")


class InvitarUsuarioModalView(_ModalMixin, View):
    def _modal(self, request, form):
        return render_modal(
            request,
            form=form,
            titulo="Invitar usuario interno",
            accion_url=reverse("identidad:invitar_usuario"),
            boton="Enviar invitación",
        )

    def get(self, request):
        return self._modal(request, InvitarUsuarioForm())

    def post(self, request):
        form = InvitarUsuarioForm(request.POST)
        if form.is_valid():
            organizacion = organizacion_con_permiso(request.user, ACCION)
            try:
                rol_asignado = services.invitar_usuario(organizacion=organizacion, actor=request.user, **form.cleaned_data)
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(
                    request,
                    mensaje=f"Invitación enviada a {rol_asignado.usuario.email}.",
                    url=reverse("identidad:usuarios"),
                )
        return self._modal(request, form)


class DesactivarRolModalView(_ModalMixin, View):
    def _modal(self, request, form, rol_asignado):
        return render_modal(
            request,
            form=form,
            titulo=f"Desactivar {rol_asignado.rol.nombre} de {rol_asignado.usuario.nombre_completo}",
            accion_url=reverse("identidad:desactivar_rol", args=[rol_asignado.pk]),
            boton="Desactivar",
        )

    def get(self, request, pk):
        return self._modal(request, DesactivarRolForm(), self.rol_asignado(pk))

    def post(self, request, pk):
        rol_asignado = self.rol_asignado(pk)
        form = DesactivarRolForm(request.POST)
        if form.is_valid():
            try:
                services.desactivar_rol_interno(
                    rol_asignado=rol_asignado, actor=request.user, motivo=form.cleaned_data["motivo"]
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(request, mensaje="Rol desactivado.", url=reverse("identidad:usuarios"))
        return self._modal(request, form, rol_asignado)


class ReenviarAccesoInternoView(_UsuariosMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        try:
            services.reenviar_acceso_interno(rol_asignado=self.rol_asignado(pk), actor=request.user)
        except DomainError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Se envió un enlace de acceso nuevo.")
        return redirect("identidad:usuarios")
