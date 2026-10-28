import re
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp import oath
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.core.models import EventoAuditoria
from apps.empresas.services import crear_empresa
from apps.identidad import permisos
from apps.identidad.forms import LoginForm
from apps.identidad.models import Organizacion, Rol, RolAsignado, TipoCuenta, Usuario
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

    def test_documento_con_forma_de_correo_no_bloquea_al_personal_interno(self):
        crear_cuenta_portal(nombre_usuario="interno@example.com", email="otra@example.com")

        self.assertEqual(authenticate(username="interno@example.com", password="UnaClaveSegura123!"), self.interno)

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


def usuario_interno(email="interno@cliente.com"):
    return Usuario.objects.create_user(email=email, nombre_completo="Usuario interno", password="UnaClaveSegura123!")


def asignar_rol(usuario, organizacion, codigo, activo=True):
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
    return RolAsignado.objects.create(usuario=usuario, organizacion=organizacion, rol=rol, activo=activo)


class MatrizDePermisosTests(TestCase):
    """S2: la matriz vive en un solo módulo y todo lo que no está en ella se deniega."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.otra = Organizacion.objects.create(nombre="Otra minera")
        self.usuario = usuario_interno()

    def test_cada_rol_tiene_solo_las_acciones_de_la_matriz(self):
        esperado = {
            "admin_cliente": {
                "ver_registros", "editar_registros", "enviar_credenciales", "configurar_catalogo",
                "asignar_requisitos", "subir_documentos", "revisar_documentos", "solicitar_ingreso",
                "aprobar_ingreso", "crear_trabajo", "gestionar_trabajo", "ver_alertas", "exportar_reportes",
                "gestionar_usuarios",
            },
            "gestor_contratista": {
                "ver_registros", "editar_registros", "enviar_credenciales", "asignar_requisitos",
                "subir_documentos", "solicitar_ingreso", "crear_trabajo", "ver_alertas",
            },
            "revisor_documental": {"ver_registros", "revisar_documentos", "ver_alertas"},
            "responsable_area": {
                "ver_registros", "solicitar_ingreso", "aprobar_ingreso", "crear_trabajo", "gestionar_trabajo",
                "ver_alertas",
            },
            "seguridad_garita": {"garita"},
            "operador_antecedentes": {"ver_registros", "ver_alertas"},
            "revisor_antecedentes": {"ver_registros", "ver_alertas"},
            "contratista": set(),
            "admin_plataforma": set(),
        }
        for codigo, acciones in esperado.items():
            with self.subTest(rol=codigo):
                usuario = usuario_interno(f"{codigo}@cliente.com")
                asignar_rol(usuario, self.organizacion, codigo)

                self.assertEqual(permisos.acciones_permitidas(usuario), acciones)

    def test_accion_desconocida_falla_cerrado(self):
        asignar_rol(self.usuario, self.organizacion, "admin_cliente")

        for funcion in (
            permisos.puede,
            permisos.exigir,
            permisos.organizaciones_con_permiso,
            permisos.organizacion_con_permiso,
        ):
            with self.subTest(funcion=funcion.__name__), self.assertRaises(ValueError):
                funcion(self.usuario, "accion_inventada")

    def test_rol_inactivo_no_otorga_nada(self):
        asignar_rol(self.usuario, self.organizacion, "admin_cliente", activo=False)

        self.assertFalse(permisos.puede(self.usuario, "ver_registros"))
        self.assertEqual(list(permisos.organizaciones_con_permiso(self.usuario, "ver_registros")), [])
        self.assertIsNone(permisos.organizacion_con_permiso(self.usuario, "ver_registros"))
        self.assertEqual(permisos.acciones_permitidas(self.usuario), set())

    def test_el_permiso_es_por_organizacion(self):
        asignar_rol(self.usuario, self.organizacion, "gestor_contratista")
        asignar_rol(self.usuario, self.otra, "seguridad_garita")

        self.assertTrue(permisos.puede(self.usuario, "editar_registros", self.organizacion.pk))
        self.assertFalse(permisos.puede(self.usuario, "editar_registros", self.otra.pk))
        self.assertTrue(permisos.puede(self.usuario, "garita", self.otra))
        self.assertEqual(list(permisos.organizaciones_con_permiso(self.usuario, "editar_registros")), [self.organizacion.pk])
        self.assertEqual(permisos.organizacion_con_permiso(self.usuario, "garita"), self.otra)

    def test_exigir_lanza_403_sin_permiso(self):
        asignar_rol(self.usuario, self.organizacion, "seguridad_garita")

        with self.assertRaises(PermissionDenied):
            permisos.exigir(self.usuario, "editar_registros")
        permisos.exigir(self.usuario, "garita")

    def test_rol_de_plataforma_sin_organizacion_y_anonimos_no_reciben_nada(self):
        asignar_rol(self.usuario, None, "admin_cliente")

        self.assertFalse(permisos.puede(self.usuario, "ver_registros"))
        self.assertFalse(permisos.puede(AnonymousUser(), "ver_registros"))


class PanelYMenuPermisosTests(TestCase):
    """S3/S4: el panel exige un rol activo; el menú y los conteos solo muestran lo permitido."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        crear_empresa(
            organizacion=self.organizacion,
            razon_social="Constructora Andina",
            ruc="1790012345001",
            actor=usuario_interno("actor@cliente.com"),
        )
        self.usuario = usuario_interno()

    def _enlace(self, nombre):
        return f'href="{reverse(nombre)}"'

    def test_usuario_sin_rol_activo_recibe_403(self):
        asignar_rol(self.usuario, self.organizacion, "admin_cliente", activo=False)
        self.client.force_login(self.usuario)

        self.assertEqual(self.client.get(reverse("identidad:panel")).status_code, 403)

    def test_garita_abre_el_panel_sin_conteos_ni_enlaces_ajenos(self):
        asignar_rol(self.usuario, self.organizacion, "seguridad_garita")
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("identidad:panel"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["total_empresas"], 0)
        self.assertEqual(respuesta.context["total_personas"], 0)
        self.assertContains(respuesta, self._enlace("accesos:garita"))
        for nombre in ("empresas:lista", "personas:lista", "cumplimiento:bandeja", "accesos:lista", "identidad:alertas"):
            with self.subTest(enlace=nombre):
                self.assertNotContains(respuesta, self._enlace(nombre))

    def test_gestor_ve_conteos_y_su_menu_pero_no_la_garita_ni_el_catalogo(self):
        asignar_rol(self.usuario, self.organizacion, "gestor_contratista")
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("identidad:panel"))

        self.assertEqual(respuesta.context["total_empresas"], 1)
        self.assertContains(respuesta, self._enlace("empresas:lista"))
        self.assertContains(respuesta, self._enlace("identidad:alertas"))
        self.assertNotContains(respuesta, self._enlace("accesos:garita"))
        self.assertNotContains(respuesta, self._enlace("antecedentes:bandeja"))

    def test_operador_de_antecedentes_ve_el_menu_de_antecedentes(self):
        asignar_rol(self.usuario, self.organizacion, "operador_antecedentes")
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("identidad:panel"))

        self.assertContains(respuesta, self._enlace("antecedentes:bandeja"))
        self.assertNotContains(respuesta, self._enlace("accesos:garita"))


def enlace_del_correo(correo):
    return re.search(r"https?://\S+/activar/\S+/", correo.body).group(0)


class UsuariosInternosTests(TestCase):
    """T2: el administrador del cliente invita usuarios internos, les asigna un rol y los desactiva."""

    HTMX = {"HTTP_HX_REQUEST": "true"}

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.otra = Organizacion.objects.create(nombre="Otra minera")
        self.admin = usuario_interno("admin@cliente.com")
        self.rol_admin = asignar_rol(self.admin, self.organizacion, "admin_cliente")
        self.revisor = Rol.objects.get_or_create(codigo="revisor_documental", defaults={"nombre": "Revisor"})[0]
        self.client.force_login(self.admin)

    def _invitar(self, email="nuevo@cliente.com", rol=None, nombre="Ana Pérez"):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("identidad:invitar_usuario"),
                {"email": email, "nombre_completo": nombre, "rol": str((rol or self.revisor).pk)},
                **self.HTMX,
            )

    def _conteos(self):
        return (Usuario.objects.count(), RolAsignado.objects.count(), EventoAuditoria.objects.count())

    def test_solo_gestionar_usuarios_abre_la_pantalla(self):
        for codigo in ("gestor_contratista", "seguridad_garita", "revisor_documental", "responsable_area"):
            with self.subTest(codigo=codigo):
                otro = usuario_interno(f"{codigo}@cliente.com")
                asignar_rol(otro, self.organizacion, codigo)
                self.client.force_login(otro)
                self.assertEqual(self.client.get(reverse("identidad:usuarios")).status_code, 403)
                antes = self._conteos()
                respuesta = self.client.post(
                    reverse("identidad:invitar_usuario"),
                    {"email": "x@cliente.com", "nombre_completo": "X", "rol": str(self.revisor.pk)},
                    **self.HTMX,
                )
                self.assertEqual(respuesta.status_code, 403)
                self.assertEqual(self._conteos(), antes)

    def test_lista_solo_usuarios_internos_de_su_organizacion(self):
        ajeno = usuario_interno("ajeno@otra.com")
        asignar_rol(ajeno, self.otra, "revisor_documental")
        portal = crear_cuenta_portal()
        asignar_rol(portal, self.organizacion, "contratista")

        respuesta = self.client.get(reverse("identidad:usuarios"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "admin@cliente.com")
        self.assertNotContains(respuesta, "ajeno@otra.com")
        self.assertNotContains(respuesta, "empresa@example.com")

    def test_invitar_crea_usuario_interno_con_rol_y_envia_enlace_de_activacion(self):
        respuesta = self._invitar()

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("identidad:usuarios"))
        usuario = Usuario.objects.get(email="nuevo@cliente.com")
        self.assertEqual(usuario.tipo_cuenta, TipoCuenta.INTERNA)
        self.assertFalse(usuario.has_usable_password())
        self.assertTrue(
            RolAsignado.objects.filter(
                usuario=usuario, organizacion=self.organizacion, rol=self.revisor, activo=True
            ).exists()
        )
        self.assertTrue(EventoAuditoria.objects.filter(accion="INVITAR_USUARIO", recurso_id=str(usuario.pk)).exists())
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["nuevo@cliente.com"])
        self.assertIn("Tu usuario es: nuevo@cliente.com", correo.body)
        self.assertNotIn("None", correo.body)

    def test_usuario_invitado_activa_su_cuenta_e_inicia_sesion(self):
        self._invitar()
        ruta = "/" + enlace_del_correo(mail.outbox[0]).split("://", 1)[1].split("/", 1)[1]
        self.client.logout()

        formulario = self.client.get(ruta, follow=True)
        self.client.post(
            formulario.redirect_chain[-1][0], {"new_password1": CLAVE_NUEVA, "new_password2": CLAVE_NUEVA}
        )

        usuario = Usuario.objects.get(email="nuevo@cliente.com")
        self.assertEqual(authenticate(username="nuevo@cliente.com", password=CLAVE_NUEVA), usuario)

    def test_no_se_ofrecen_ni_aceptan_roles_de_portal_ni_de_plataforma(self):
        for codigo, plataforma in (("contratista", False), ("trabajador", False), ("admin_plataforma", True)):
            with self.subTest(codigo=codigo):
                rol = Rol.objects.get_or_create(
                    codigo=codigo, defaults={"nombre": codigo, "es_rol_plataforma": plataforma}
                )[0]
                formulario = self.client.get(reverse("identidad:invitar_usuario"), **self.HTMX)
                self.assertNotContains(formulario, str(rol.pk))
                antes = self._conteos()
                self._invitar(email=f"{codigo}@cliente.com", rol=rol)
                self.assertEqual(self._conteos(), antes)
                self.assertEqual(len(mail.outbox), 0)

    def test_correo_de_cuenta_de_portal_no_recibe_rol_interno(self):
        portal = crear_cuenta_portal(email="contratista@example.com")
        antes = self._conteos()

        respuesta = self._invitar(email="contratista@example.com")

        self.assertContains(respuesta, "cuenta de portal")
        self.assertEqual(self._conteos(), antes)
        self.assertFalse(RolAsignado.objects.filter(usuario=portal, rol=self.revisor).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_usuario_interno_existente_recibe_solo_aviso_sin_enlace(self):
        existente = usuario_interno("existente@otra.com")
        asignar_rol(existente, self.otra, "revisor_documental")

        self._invitar(email="existente@otra.com")

        self.assertTrue(RolAsignado.objects.filter(usuario=existente, organizacion=self.organizacion).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("/activar/", mail.outbox[0].body)
        existente.refresh_from_db()
        self.assertTrue(existente.check_password("UnaClaveSegura123!"))

    def test_desactivar_rol_quita_el_acceso_y_audita(self):
        usuario = usuario_interno("revisor@cliente.com")
        rol_asignado = asignar_rol(usuario, self.organizacion, "revisor_documental")

        respuesta = self.client.post(
            reverse("identidad:desactivar_rol", args=[rol_asignado.pk]), {"motivo": "Dejó la empresa"}, **self.HTMX
        )

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("identidad:usuarios"))
        rol_asignado.refresh_from_db()
        self.assertFalse(rol_asignado.activo)
        self.assertTrue(EventoAuditoria.objects.filter(accion="REVOCAR_ROL", recurso_id=str(rol_asignado.pk)).exists())
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse("cumplimiento:bandeja")).status_code, 403)

    def test_desactivar_exige_motivo(self):
        usuario = usuario_interno("revisor@cliente.com")
        rol_asignado = asignar_rol(usuario, self.organizacion, "revisor_documental")

        self.client.post(reverse("identidad:desactivar_rol", args=[rol_asignado.pk]), {"motivo": ""}, **self.HTMX)

        rol_asignado.refresh_from_db()
        self.assertTrue(rol_asignado.activo)

    def test_no_puede_desactivar_su_propio_rol_de_administrador(self):
        respuesta = self.client.post(
            reverse("identidad:desactivar_rol", args=[self.rol_admin.pk]), {"motivo": "Prueba"}, **self.HTMX
        )

        self.assertContains(respuesta, "tu propio rol de administrador")
        self.rol_admin.refresh_from_db()
        self.assertTrue(self.rol_admin.activo)

    def test_no_toca_roles_de_otra_organizacion(self):
        ajeno = usuario_interno("ajeno@otra.com")
        rol_ajeno = asignar_rol(ajeno, self.otra, "revisor_documental")

        respuesta = self.client.post(
            reverse("identidad:desactivar_rol", args=[rol_ajeno.pk]), {"motivo": "x"}, **self.HTMX
        )

        self.assertEqual(respuesta.status_code, 404)
        rol_ajeno.refresh_from_db()
        self.assertTrue(rol_ajeno.activo)

    def test_reenviar_acceso_envia_un_enlace_nuevo(self):
        self._invitar()
        rol_asignado = RolAsignado.objects.get(usuario__email="nuevo@cliente.com")

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(reverse("identidad:reenviar_acceso_interno", args=[rol_asignado.pk]))

        self.assertRedirects(respuesta, reverse("identidad:usuarios"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("/activar/", mail.outbox[1].body)

    def test_modales_sin_htmx_redirigen_a_la_lista(self):
        respuesta = self.client.get(reverse("identidad:invitar_usuario"))

        self.assertRedirects(respuesta, reverse("identidad:usuarios"), fetch_redirect_response=False)
