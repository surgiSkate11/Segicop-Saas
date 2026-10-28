from django.urls import path

from apps.cumplimiento.views import (
    AsignarRequisitoModalView,
    BandejaRevisionView,
    RevisarLoteView,
    CatalogoListView,
    CatalogoRequisitoModalView,
    RequisitoDetalleView,
)

app_name = "cumplimiento"

urlpatterns = [
    path("cumplimiento/bandeja/", BandejaRevisionView.as_view(), name="bandeja"),
    path("cumplimiento/bandeja/revisar/", RevisarLoteView.as_view(), name="revisar_lote"),
    path("cumplimiento/catalogo/", CatalogoListView.as_view(), name="catalogo"),
    path("cumplimiento/catalogo/nuevo/", CatalogoRequisitoModalView.as_view(), name="catalogo_nuevo"),
    path("cumplimiento/catalogo/<uuid:pk>/asignar/", AsignarRequisitoModalView.as_view(), name="catalogo_asignar"),
    path("cumplimiento/requisitos/<uuid:pk>/", RequisitoDetalleView.as_view(), name="detalle"),
]
