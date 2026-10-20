from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from apps.empresas.models import EmpresaContratista
from apps.identidad.models import TipoCuenta
from apps.personas.models import Persona


class PortalMixin(LoginRequiredMixin):
    """
    S7: base de toda vista del portal. Cada vista declara el `tipo_cuenta`
    que puede verla; cualquier otra cuenta (incluido el personal interno)
    recibe 403. Los datos salen solo de los titulares vinculados al usuario.
    """

    tipo_cuenta: str = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.tipo_cuenta != self.tipo_cuenta:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class PortalEmpresaMixin(PortalMixin):
    tipo_cuenta = TipoCuenta.EMPRESA

    def get_empresas(self):
        return EmpresaContratista.objects.filter(usuario=self.request.user).select_related("organizacion")


class PortalPersonaMixin(PortalMixin):
    tipo_cuenta = TipoCuenta.PERSONA

    def get_personas(self):
        return Persona.objects.filter(usuario=self.request.user).select_related("organizacion")
