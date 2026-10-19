from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.forms import CharField, PasswordInput, TextInput
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.forms import EstiloFormMixin


class LoginForm(AuthenticationForm):
    """
    AuthenticationForm estándar de Django, pero con las clases CSS del
    layout ya inyectadas en los widgets, para no depender de estilos
    "sueltos" en el template de login.
    """

    # S3: RUC/cédula para cuentas de portal, correo para el personal interno.
    username = CharField(
        label="Usuario (RUC, cédula o correo)",
        max_length=254,
        widget=TextInput(attrs={"class": "sc-input", "autofocus": True, "autocomplete": "username"}),
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


class DefinirPasswordForm(EstiloFormMixin, SetPasswordForm):
    """SetPasswordForm de Django (aplica AUTH_PASSWORD_VALIDATORS) con el estilo del layout."""


class CorreoAccesoFormMixin:
    """
    S12: al CREAR un titular con cuenta de portal (empresa, persona) el
    correo de contacto es obligatorio: ahí llega su enlace de acceso.
    Al editar sigue siendo opcional, como antes.
    """

    campo_correo_acceso = "contacto_email"

    def __init__(self, *args, creando=False, **kwargs):
        super().__init__(*args, **kwargs)
        if creando:
            campo = self.fields[self.campo_correo_acceso]
            campo.required = True
            campo.help_text = "Aquí llegará el enlace para activar el acceso."
