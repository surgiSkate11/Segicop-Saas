from django.urls import path

from apps.accesos.views import GaritaView, PermisoDetalleView, PermisoListView, SolicitarPermisoView

app_name = "accesos"

urlpatterns = [
    path("accesos/solicitar/<uuid:persona_pk>/", SolicitarPermisoView.as_view(), name="solicitar"),
    path("accesos/permisos/", PermisoListView.as_view(), name="lista"),
    path("accesos/permisos/<uuid:pk>/", PermisoDetalleView.as_view(), name="detalle"),
    path("accesos/garita/", GaritaView.as_view(), name="garita"),
]