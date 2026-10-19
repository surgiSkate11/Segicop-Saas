from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView

from apps.identidad.models import TipoCuenta
from apps.personas.models import Asignacion
from apps.portal.mixins import PortalEmpresaMixin, PortalPersonaMixin


class InicioPortalView(LoginRequiredMixin, View):
    """Lleva a cada cuenta a su portal; el personal interno vuelve a su panel."""

    def get(self, request):
        destinos = {
            TipoCuenta.EMPRESA: "portal:empresa",
            TipoCuenta.PERSONA: "portal:persona",
        }
        return redirect(destinos.get(request.user.tipo_cuenta, "identidad:panel"))


class EmpresaPortalView(PortalEmpresaMixin, TemplateView):
    """S4: solo lectura de las empresas vinculadas a la cuenta."""

    template_name = "portal/empresa.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        fichas = []
        for empresa in self.get_empresas().order_by("organizacion__nombre", "razon_social"):
            asignaciones = (
                Asignacion.objects.filter(empresa=empresa, activa=True)
                .select_related("persona")
                .order_by("persona__apellidos", "persona__nombres")
            )
            fichas.append(
                {
                    "empresa": empresa,
                    "asignaciones": asignaciones,
                    "contratos": empresa.contratos.filter(activo=True).order_by("fecha_inicio"),
                }
            )
        contexto["fichas"] = fichas
        return contexto


class PersonaPortalView(PortalPersonaMixin, TemplateView):
    """S5: perfil de solo lectura y espacio para el pase de ingreso."""

    template_name = "portal/persona.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        fichas = []
        for persona in self.get_personas().order_by("organizacion__nombre", "apellidos", "nombres"):
            asignaciones = (
                Asignacion.objects.filter(persona=persona, activa=True)
                .select_related("empresa")
                .order_by("empresa__razon_social")
            )
            fichas.append({"persona": persona, "asignaciones": asignaciones})
        contexto["fichas"] = fichas
        return contexto
