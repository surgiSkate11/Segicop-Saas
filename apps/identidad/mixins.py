from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx
from apps.identidad import services


class EnviarAccesoModalView(LoginRequiredMixin, View):
    """
    S9: modal HTMX para (re)enviar el enlace de acceso a cualquier titular
    con CuentaAccesoMixin. Cada app solo declara cómo encontrar al titular
    (respetando su aislamiento por organización) y a qué URLs volver:

        class EnviarAccesoEmpresaView(EnviarAccesoModalView):
            url_lista = "empresas:lista"
            url_accion = "empresas:enviar_acceso"
            def get_titular(self, request, pk): ...
    """

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
