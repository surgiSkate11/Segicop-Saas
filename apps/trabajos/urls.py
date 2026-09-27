from django.urls import path

from apps.trabajos.views import CrearPermisoTrabajoView, PermisoTrabajoDetalleView, PermisoTrabajoListView

app_name = "trabajos"

urlpatterns = [
    path("trabajos/", PermisoTrabajoListView.as_view(), name="lista"),
    path("trabajos/nuevo/<uuid:empresa_pk>/", CrearPermisoTrabajoView.as_view(), name="crear"),
    path("trabajos/<uuid:pk>/", PermisoTrabajoDetalleView.as_view(), name="detalle"),
]