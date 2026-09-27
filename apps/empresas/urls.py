from django.urls import path

from apps.empresas.views import EmpresaListView

app_name = "empresas"

urlpatterns = [
    path("empresas/", EmpresaListView.as_view(), name="lista"),
]