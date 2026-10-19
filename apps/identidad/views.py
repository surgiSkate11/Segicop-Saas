from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import INTERNAL_RESET_SESSION_TOKEN, PasswordResetConfirmView
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from apps.core.models import AlertaNotificacion, EstadoAlerta
from apps.empresas.models import EmpresaContratista
from apps.identidad import services
from apps.identidad.forms import DefinirPasswordForm
from apps.identidad.tokens import activacion_cuenta_token
from apps.personas.models import Persona


class ActivarCuentaView(PasswordResetConfirmView):
    """
    S2: el titular define su contraseña desde el enlace del correo.
    Reutiliza la vista de Django: valida uid/token, mueve el token de la
    URL a la sesión (no se filtra por Referer) y aplica los validadores.
    La mutación pasa por el servicio de dominio, no por form.save().
    """

    template_name = "identidad/activar_cuenta.html"
    token_generator = activacion_cuenta_token
    form_class = DefinirPasswordForm
    reset_url_token = "definir-contrasena"
    success_url = reverse_lazy("identidad:login")

    def form_valid(self, form):
        services.activar_cuenta(usuario=form.user, password=form.cleaned_data["new_password1"])
        del self.request.session[INTERNAL_RESET_SESSION_TOKEN]
        messages.success(self.request, "Tu contraseña quedó definida. Ya puedes ingresar con tu usuario.")
        return HttpResponseRedirect(self.get_success_url())


class PanelView(LoginRequiredMixin, TemplateView):
    template_name = "identidad/panel.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        organizaciones_ids = self.request.user.roles_asignados.filter(activo=True).values_list(
            "organizacion_id", flat=True
        )

        contexto["total_empresas"] = EmpresaContratista.objects.filter(organizacion_id__in=organizaciones_ids).count()
        contexto["total_personas"] = Persona.objects.filter(organizacion_id__in=organizaciones_ids).count()
        contexto["alertas_pendientes"] = AlertaNotificacion.objects.filter(
            organizacion_id__in=organizaciones_ids,
            estado=EstadoAlerta.PENDIENTE,
        )[:8]
        contexto["total_alertas"] = AlertaNotificacion.objects.filter(
            organizacion_id__in=organizaciones_ids,
            estado=EstadoAlerta.PENDIENTE,
        ).count()
        return contexto
