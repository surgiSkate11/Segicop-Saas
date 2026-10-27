from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx
from apps.identidad import services
from apps.identidad.permisos import organizaciones_con_permiso, puede


class RequierePermisoMixin(LoginRequiredMixin):
    """
    S3: denegar por defecto. La vista declara la acción de la matriz (`identidad.permisos`) que exige;
    sin ella en ninguna organización del usuario responde 403 antes de leer o escribir nada.

        class EmpresaListView(RequierePermisoMixin, ListView):
            accion_requerida = "ver_registros"

    Cuando GET y POST exigen acciones distintas, `accion_requerida` es un dict por método HTTP
    ({"GET": "ver_registros", "POST": "gestionar_trabajo"}). Un método ausente del dict se deniega.
    Quien consulta datos filtra con `self.organizaciones_permitidas()` (explícito, constitución §2.6),
    de modo que un objeto de otra organización —o de una donde falta la acción— es 404.
    """

    accion_requerida = None

    def accion_para(self, metodo):
        accion = self.accion_requerida
        if isinstance(accion, dict):
            return accion.get("GET" if metodo == "HEAD" else metodo)
        return accion

    def organizaciones_permitidas(self):
        return organizaciones_con_permiso(self.request.user, self.accion_actual)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.accion_actual = self.accion_para(request.method)
        if self.accion_actual is None or not puede(request.user, self.accion_actual):
            raise PermissionDenied("Tu rol no permite esta acción.")
        return super().dispatch(request, *args, **kwargs)


class EnviarAccesoModalView(RequierePermisoMixin, View):
    """
    S9: modal HTMX para (re)enviar el enlace de acceso a cualquier titular
    con CuentaAccesoMixin. Cada app solo declara cómo encontrar al titular
    (respetando su aislamiento por organización) y a qué URLs volver:

        class EnviarAccesoEmpresaView(EnviarAccesoModalView):
            url_lista = "empresas:lista"
            url_accion = "empresas:enviar_acceso"
            def get_titular(self, request, pk): ...
    """

    accion_requerida = "enviar_credenciales"
    url_lista = ""
    url_accion = ""
    template_name = "identidad/_modal_enviar_acceso.html"

    def get_titular(self, request, pk):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect(self.url_lista)
        return super().dispatch(request, *args, **kwargs)

    def _modal(self, request, titular, error=None):
        return render(
            request,
            self.template_name,
            {
                "titular": titular,
                "cuenta": titular.usuario,
                "error": error,
                "accion_url": reverse(self.url_accion, args=[titular.pk]),
            },
        )

    def get(self, request, pk):
        return self._modal(request, self.get_titular(request, pk))

    def post(self, request, pk):
        titular = self.get_titular(request, pk)
        try:
            usuario = services.enviar_acceso(titular=titular, actor=request.user)
        except DomainError as error:
            return self._modal(request, titular, error=str(error))
        if usuario.email.lower() == (titular.correo_cuenta or "").strip().lower():
            mensaje = f"Enlace de acceso enviado a {usuario.email}."
        else:
            # Cuenta compartida: su correo pertenece a otra organización y no se revela.
            mensaje = "Enlace de acceso enviado al correo registrado de la cuenta."
        return exito_htmx(request, mensaje=mensaje, url=reverse(self.url_lista))
