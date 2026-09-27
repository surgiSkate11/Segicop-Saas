from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from apps.empresas.models import EmpresaContratista


class EmpresaListView(LoginRequiredMixin, ListView):
    """
    Filtra por las organizaciones donde el usuario tiene un rol activo.
    Es una aproximación razonable para esta fase — el filtrado fino por
    alcance (instalación/contrato) llega en el endurecimiento de permisos.
    """
    model = EmpresaContratista
    template_name = "empresas/lista.html"
    context_object_name = "empresas"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = self.request.user.roles_asignados.filter(
            activo=True
        ).values_list("organizacion_id", flat=True)
        return (
            EmpresaContratista.objects.filter(organizacion_id__in=organizaciones_ids)
            .select_related("organizacion")
            .order_by("razon_social")
        )