from django.urls import path

from apps.empresas.views import EmpresaListView, EmpresaModalView

app_name = "empresas"

urlpatterns = [
    path("empresas/", EmpresaListView.as_view(), name="lista"),
    path("empresas/nueva/", EmpresaModalView.as_view(), name="crear"),
    path("empresas/<uuid:pk>/editar/", EmpresaModalView.as_view(), name="editar"),
]
