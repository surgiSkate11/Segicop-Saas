from django.urls import path

from apps.personas.views import PersonaListView

app_name = "personas"

urlpatterns = [
    path("personas/", PersonaListView.as_view(), name="lista"),
]