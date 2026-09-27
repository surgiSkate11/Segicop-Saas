from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from apps.empresas.models import EmpresaContratista
from apps.personas.models import Persona


class PanelView(LoginRequiredMixin, TemplateView):
    template_name = "identidad/panel.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        organizaciones_ids = self.request.user.roles_asignados.filter(
            activo=True
        ).values_list("organizacion_id", flat=True)

        contexto["total_empresas"] = EmpresaContratista.objects.filter(
            organizacion_id__in=organizaciones_ids
        ).count()
        contexto["total_personas"] = Persona.objects.filter(
            organizacion_id__in=organizaciones_ids
        ).count()
        return contexto