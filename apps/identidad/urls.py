from django.contrib.auth import views as auth_views
from django.urls import path

from apps.identidad.forms import LoginForm
from apps.identidad.views import ActivarCuentaView, PanelView
from apps.identidad.views_alertas import AlertasView, ExportarAlertasView
from apps.identidad.views_usuarios import (
    DesactivarRolModalView,
    InvitarUsuarioModalView,
    ReenviarAccesoInternoView,
    UsuarioListView,
)

app_name = "identidad"

urlpatterns = [
    path("", PanelView.as_view(), name="panel"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="identidad/login.html",
            authentication_form=LoginForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("activar/<uidb64>/<token>/", ActivarCuentaView.as_view(), name="activar_cuenta"),
    path("alertas/", AlertasView.as_view(), name="alertas"),
    path("alertas/<uuid:pk>/leer/", AlertasView.as_view(), name="alerta_leer"),
    path("reportes/alertas.csv", ExportarAlertasView.as_view(), name="exportar_alertas"),
    path("usuarios/", UsuarioListView.as_view(), name="usuarios"),
    path("usuarios/invitar/", InvitarUsuarioModalView.as_view(), name="invitar_usuario"),
    path("usuarios/roles/<uuid:pk>/desactivar/", DesactivarRolModalView.as_view(), name="desactivar_rol"),
    path("usuarios/roles/<uuid:pk>/reenviar/", ReenviarAccesoInternoView.as_view(), name="reenviar_acceso_interno"),
]
