from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from apps.personas.models import Persona


class PersonaListView(LoginRequiredMixin, ListView):
    model = Persona
    template_name = "personas/lista.html"
    context_object_name = "personas"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = self.request.user.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)
        return Persona.objects.filter(organizacion_id__in=organizaciones_ids).prefetch_related("asignaciones__empresa").order_by("apellidos", "nombres")