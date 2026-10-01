from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.forms import CharField, EmailField, PasswordInput, TextInput
from django_otp.plugins.otp_totp.models import TOTPDevice


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
    otp_token = CharField(
        label="Código MFA",
        required=False,
        max_length=6,
        widget=TextInput(attrs={"class": "sc-input", "inputmode": "numeric", "autocomplete": "one-time-code"}),
    )

    def clean(self):
        datos = super().clean()
        usuario = self.get_user()
        if usuario is None or not usuario.mfa_habilitado:
            return datos

        token = datos.get("otp_token")
        if not token:
            raise ValidationError("Este usuario requiere un código MFA.")
        dispositivo = TOTPDevice.objects.filter(user=usuario, confirmed=True).first()
        if dispositivo is None:
            raise ValidationError("El MFA está habilitado, pero no existe un dispositivo TOTP confirmado.")
        if not dispositivo.verify_token(token):
            raise ValidationError("El código MFA no es válido.")
        return datos
