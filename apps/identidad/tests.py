from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import authenticate
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp import oath
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.models import EventoAuditoria
from apps.identidad.forms import LoginForm
from apps.identidad.models import TipoCuenta, Usuario
from apps.identidad.tokens import ActivacionCuentaTokenGenerator, activacion_cuenta_token

CLAVE_NUEVA = "Contratista-Segura-2026"


def crear_cuenta_portal(nombre_usuario="1790000000001", email="empresa@example.com"):
    """Cuenta de portal tal como la deja el aprovisionamiento: sin contraseña usable."""
    return Usuario.objects.create_user(
        email=email,
        nombre_completo="Constructora Andina",
        nombre_usuario=nombre_usuario,
        tipo_cuenta=TipoCuenta.EMPRESA,
    )


def url_activacion(usuario):
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    return reverse("identidad:activar_cuenta", args=[uid, activacion_cuenta_token.make_token(usuario)])


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


class LogoutTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            email="logout@example.com",
            nombre_completo="Usuario Logout",
            password="UnaClaveSegura123!",
        )

    def test_logout_requiere_post(self):
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("identidad:logout"))

        self.assertEqual(respuesta.status_code, 405)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_logout_por_post_cierra_la_sesion(self):
        self.client.force_login(self.usuario)

        respuesta = self.client.post(reverse("identidad:logout"))

        self.assertRedirects(respuesta, reverse("identidad:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class LoginPorNombreUsuarioTests(TestCase):
    """S3: las cuentas de portal entran con RUC/cédula; el personal interno sigue con su correo."""

    def setUp(self):
        self.cuenta = crear_cuenta_portal()
        self.cuenta.set_password(CLAVE_NUEVA)
        self.cuenta.save()
        self.interno = Usuario.objects.create_user(
            email="interno@example.com", nombre_completo="Interno", password="UnaClaveSegura123!"
        )

    def test_autentica_con_nombre_usuario(self):
        self.assertEqual(authenticate(username="1790000000001", password=CLAVE_NUEVA), self.cuenta)

    def test_autentica_con_nombre_usuario_con_espacios_alrededor(self):
        self.assertEqual(authenticate(username="  1790000000001 ", password=CLAVE_NUEVA), self.cuenta)

    def test_personal_interno_sigue_entrando_con_correo(self):
        self.assertEqual(authenticate(username="interno@example.com", password="UnaClaveSegura123!"), self.interno)

    def test_rechaza_contrasena_incorrecta(self):
        self.assertIsNone(authenticate(username="1790000000001", password="otra"))

    def test_rechaza_usuario_inexistente(self):
        self.assertIsNone(authenticate(username="0000000000", password=CLAVE_NUEVA))

    def test_cuenta_sin_activar_no_puede_ingresar(self):
        pendiente = crear_cuenta_portal(nombre_usuario="0912345678", email="persona@example.com")

        self.assertFalse(pendiente.has_usable_password())
        self.assertIsNone(authenticate(username="0912345678", password=""))

    def test_formulario_de_login_acepta_ruc(self):
        formulario = LoginForm(data={"username": "1790000000001", "password": CLAVE_NUEVA})

        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertEqual(formulario.get_user(), self.cuenta)

    def test_login_http_con_ruc_inicia_sesion(self):
        respuesta = self.client.post(
            reverse("identidad:login"), {"username": "1790000000001", "password": CLAVE_NUEVA}
        )

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.cuenta.pk))

    def test_pantalla_de_login_indica_que_acepta_ruc_cedula_o_correo(self):
        respuesta = self.client.get(reverse("identidad:login"))

        self.assertContains(respuesta, "Usuario (RUC, cédula o correo)")


class ActivacionCuentaTests(TestCase):
    """S2: enlace de un solo uso, válido 72 h, donde el titular define su contraseña."""

    def setUp(self):
        self.cuenta = crear_cuenta_portal()

    def _abrir_formulario(self, url):
        # Django cambia el token de la URL por uno en sesión (no se filtra por Referer).
        return self.client.get(url, follow=True)

    def _definir(self, url, clave1=CLAVE_NUEVA, clave2=CLAVE_NUEVA):
        respuesta = self._abrir_formulario(url)
        return self.client.post(
            respuesta.redirect_chain[-1][0], {"new_password1": clave1, "new_password2": clave2}
        )

    def test_enlace_valido_muestra_el_usuario_y_el_formulario(self):
        respuesta = self._abrir_formulario(url_activacion(self.cuenta))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "1790000000001")
        self.assertContains(respuesta, 'name="new_password1"')

    def test_definir_contrasena_activa_la_cuenta_y_audita(self):
        respuesta = self._definir(url_activacion(self.cuenta))

        self.assertRedirects(respuesta, reverse("identidad:login"), fetch_redirect_response=False)
        self.cuenta.refresh_from_db()
        self.assertTrue(self.cuenta.check_password(CLAVE_NUEVA))
        self.assertIsNotNone(self.cuenta.cuenta_activada_at)
        self.assertTrue(EventoAuditoria.objects.filter(accion="ACTIVAR_CUENTA", recurso_id=str(self.cuenta.pk)).exists())
        self.assertEqual(authenticate(username="1790000000001", password=CLAVE_NUEVA), self.cuenta)

    def test_enlace_es_de_un_solo_uso(self):
        url = url_activacion(self.cuenta)
        self._definir(url)
        self.client.cookies.clear()

        respuesta = self._abrir_formulario(url)

        self.assertContains(respuesta, "El enlace no es válido o ya venció")
        self.assertNotContains(respuesta, 'name="new_password1"')

    def test_enlace_vence_a_las_72_horas(self):
        url = url_activacion(self.cuenta)
        futuro = datetime.now() + timedelta(hours=72, minutes=1)

        with mock.patch.object(ActivacionCuentaTokenGenerator, "_now", return_value=futuro):
            respuesta = self._abrir_formulario(url)

        self.assertContains(respuesta, "El enlace no es válido o ya venció")

    def test_enlace_sigue_valido_antes_de_las_72_horas(self):
        url = url_activacion(self.cuenta)
        casi = datetime.now() + timedelta(hours=71, minutes=59)

        with mock.patch.object(ActivacionCuentaTokenGenerator, "_now", return_value=casi):
            respuesta = self._abrir_formulario(url)

        self.assertContains(respuesta, 'name="new_password1"')

    def test_token_manipulado_es_rechazado(self):
        uid = urlsafe_base64_encode(force_bytes(self.cuenta.pk))

        respuesta = self._abrir_formulario(reverse("identidad:activar_cuenta", args=[uid, "abc-123"]))

        self.assertContains(respuesta, "El enlace no es válido o ya venció")

    def test_contrasenas_distintas_no_activan_y_no_auditan(self):
        respuesta = self._definir(url_activacion(self.cuenta), clave2="Otra-Clave-Distinta-1")

        self.assertEqual(respuesta.status_code, 200)
        self.cuenta.refresh_from_db()
        self.assertFalse(self.cuenta.has_usable_password())
        self.assertIsNone(self.cuenta.cuenta_activada_at)
        self.assertFalse(EventoAuditoria.objects.filter(accion="ACTIVAR_CUENTA").exists())

    def test_contrasena_debil_es_rechazada_por_los_validadores(self):
        respuesta = self._definir(url_activacion(self.cuenta), clave1="12345678", clave2="12345678")

        self.assertEqual(respuesta.status_code, 200)
        self.cuenta.refresh_from_db()
        self.assertFalse(self.cuenta.has_usable_password())

    def test_token_de_restablecimiento_de_django_no_sirve_para_activar(self):
        from django.contrib.auth.tokens import default_token_generator

        uid = urlsafe_base64_encode(force_bytes(self.cuenta.pk))
        token = default_token_generator.make_token(self.cuenta)

        respuesta = self._abrir_formulario(reverse("identidad:activar_cuenta", args=[uid, token]))

        self.assertContains(respuesta, "El enlace no es válido o ya venció")
