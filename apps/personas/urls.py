from django.urls import path

from apps.personas.views import AsignarPersonaModalView, PersonaListView, PersonaModalView

app_name = "personas"

urlpatterns = [
    path("personas/", PersonaListView.as_view(), name="lista"),
    path("personas/nueva/", PersonaModalView.as_view(), name="crear"),
    path("personas/<uuid:pk>/editar/", PersonaModalView.as_view(), name="editar"),
    path("personas/<uuid:pk>/asignar/", AsignarPersonaModalView.as_view(), name="asignar"),
]
