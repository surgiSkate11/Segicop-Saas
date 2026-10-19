from django.urls import path

from apps.empresas.views import EmpresaListView, EmpresaModalView, EnviarAccesoEmpresaView

app_name = "empresas"

urlpatterns = [
    path("empresas/", EmpresaListView.as_view(), name="lista"),
    path("empresas/nueva/", EmpresaModalView.as_view(), name="crear"),
    path("empresas/<uuid:pk>/editar/", EmpresaModalView.as_view(), name="editar"),
    path("empresas/<uuid:pk>/enviar-acceso/", EnviarAccesoEmpresaView.as_view(), name="enviar_acceso"),
]
