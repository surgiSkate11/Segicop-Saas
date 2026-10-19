from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx, render_modal
from apps.empresas import services
from apps.empresas.forms import EmpresaForm
from apps.empresas.models import EmpresaContratista
from apps.identidad.context_processors import organizacion_actual
from apps.identidad.mixins import EnviarAccesoModalView


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class EmpresaListView(LoginRequiredMixin, ListView):
    """
    Filtra por las organizaciones donde el usuario tiene un rol activo.
    El filtrado fino por alcance (instalación/contrato) llega en el
    endurecimiento de permisos.
    """

    model = EmpresaContratista
    template_name = "empresas/lista.html"
    context_object_name = "empresas"
    paginate_by = 25

    def get_queryset(self):
        return (
            EmpresaContratista.objects.filter(organizacion_id__in=_organizaciones_del_usuario(self.request.user))
            .select_related("organizacion", "usuario")
            .order_by("razon_social")
        )


class EmpresaModalView(LoginRequiredMixin, View):
    """Crear (sin pk) o editar (con pk) una empresa, dentro de un modal HTMX."""

    def dispatch(self, request, *args, **kwargs):
        # Abrir la URL directamente (sin HTMX) no tiene sentido: es un fragmento.
        if not request.htmx:
            return redirect("empresas:lista")
        return super().dispatch(request, *args, **kwargs)

    def _empresa(self, request, pk):
        if pk is None:
            return None
        return get_object_or_404(
            EmpresaContratista, pk=pk, organizacion_id__in=_organizaciones_del_usuario(request.user)
        )

    def _modal(self, request, form, empresa):
        if empresa:
            return render_modal(
                request,
                form=form,
                titulo="Editar empresa",
                accion_url=reverse("empresas:editar", args=[empresa.pk]),
                boton="Guardar cambios",
            )
        return render_modal(
            request, form=form, titulo="Nueva empresa", accion_url=reverse("empresas:crear"), boton="Crear empresa"
        )

    def get(self, request, pk=None):
        empresa = self._empresa(request, pk)
        initial = None
        if empresa:
            initial = {
                "razon_social": empresa.razon_social,
                "ruc": empresa.ruc,
                "representante_legal": empresa.representante_legal,
                "contacto_email": empresa.contacto_email,
                "contacto_telefono": empresa.contacto_telefono,
            }
        return self._modal(request, EmpresaForm(initial=initial, creando=empresa is None), empresa)

    def post(self, request, pk=None):
        empresa = self._empresa(request, pk)
        form = EmpresaForm(request.POST, creando=empresa is None)
        if form.is_valid():
            try:
                if empresa:
                    services.actualizar_empresa(empresa=empresa, actor=request.user, **form.cleaned_data)
                    mensaje = "Empresa actualizada."
                else:
                    organizacion = organizacion_actual(request).get("organizacion_actual")
                    if organizacion is None:
                        raise DomainError("Tu usuario no tiene una organización asignada.")
                    services.crear_empresa(organizacion=organizacion, actor=request.user, **form.cleaned_data)
                    mensaje = "Empresa creada."
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(request, mensaje=mensaje, url=reverse("empresas:lista"))
        return self._modal(request, form, empresa)


class EnviarAccesoEmpresaView(EnviarAccesoModalView):
    url_lista = "empresas:lista"
    url_accion = "empresas:enviar_acceso"

    def get_titular(self, request, pk):
        return get_object_or_404(
            EmpresaContratista, pk=pk, organizacion_id__in=_organizaciones_del_usuario(request.user)
        )
