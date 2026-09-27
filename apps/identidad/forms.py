from django.contrib.auth.forms import AuthenticationForm
from django.forms import CharField, EmailField, PasswordInput, TextInput


class LoginForm(AuthenticationForm):
    """
    AuthenticationForm estándar de Django, pero con las clases CSS del
    layout ya inyectadas en los widgets, para no depender de estilos
    "sueltos" en el template de login.
    """

    username = EmailField(
        label="Correo electrónico",
        widget=TextInput(attrs={"class": "sc-input", "autofocus": True}),
    )
    password = CharField(
        label="Contraseña",
        widget=PasswordInput(attrs={"class": "sc-input"}),
    )