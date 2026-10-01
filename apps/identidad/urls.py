from django.contrib.auth import views as auth_views
from django.urls import path

from apps.identidad.forms import LoginForm
from apps.identidad.views import PanelView

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
]
