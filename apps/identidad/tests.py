from django.test import TestCase
from django_otp import oath
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.identidad.forms import LoginForm
from apps.identidad.models import Usuario


class MFATests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="mfa@example.com",
            nombre_completo="Usuario MFA",
            password="UnaClaveSegura123!",
        )

    def test_usuario_sin_mfa_puede_autenticarse_sin_token(self):
        formulario = LoginForm(data={"username": self.usuario.email, "password": "UnaClaveSegura123!"})

        self.assertTrue(formulario.is_valid())

    def test_usuario_con_mfa_requiere_token_totp_valido(self):
        self.usuario.mfa_habilitado = True
        self.usuario.save(update_fields=["mfa_habilitado", "updated_at"])
        dispositivo = TOTPDevice.objects.create(user=self.usuario, name="Principal", confirmed=True)
        token = oath.totp(dispositivo.bin_key, step=dispositivo.step, digits=dispositivo.digits, t0=dispositivo.t0)

        formulario = LoginForm(
            data={
                "username": self.usuario.email,
                "password": "UnaClaveSegura123!",
                "otp_token": token,
            }
        )

        self.assertTrue(formulario.is_valid())

    def test_usuario_con_mfa_rechaza_token_invalido(self):
        self.usuario.mfa_habilitado = True
        self.usuario.save(update_fields=["mfa_habilitado", "updated_at"])
        TOTPDevice.objects.create(user=self.usuario, name="Principal", confirmed=True)

        formulario = LoginForm(
            data={
                "username": self.usuario.email,
                "password": "UnaClaveSegura123!",
                "otp_token": "000000",
            }
        )

        self.assertFalse(formulario.is_valid())
