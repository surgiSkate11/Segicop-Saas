import re
import smtplib
from unittest import mock

from django.contrib.auth import authenticate
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.core.exceptions import DomainError
from apps.core.models import AlertaNotificacion, EventoAuditoria
from apps.empresas.models import EmpresaContratista
from apps.empresas.services import actualizar_empresa, crear_empresa
from apps.identidad.services import enviar_acceso
from apps.identidad.models import Organizacion, Rol, RolAsignado, TipoCuenta, Usuario

RUC = "1790012345001"
CLAVE = "Contratista-Segura-2026"
HTMX = {"HTTP_HX_REQUEST": "true"}


def preparar_staff(organizacion, email="admin@cliente.com"):
    """Personal interno con rol en la organizaci\u00f3n (as\u00ed lo ven las vistas)."""
    staff = Usuario.objects.create_user(email=email, nombre_completo="Admin Cliente", password="UnaClaveSegura123!")
    rol, _ = Rol.objects.get_or_create(codigo="admin_cliente", defaults={"nombre": "Administrador del cliente"})
    RolAsignado.objects.create(usuario=staff, organizacion=organizacion, rol=rol)
    return staff


def enlace_del_correo(correo):
    return re.search(r"http://\S+/activar/\S+/", correo.body).group(0)


class CrearEmpresaConCuentaTests(TestCase):
    """S1/S2: al crear una empresa se crea su cuenta (usuario = RUC) y se env\u00eda el enlace de activaci\u00f3n."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.actor = preparar_staff(self.organizacion)

    def _crear(self, **extra):
        datos = {"contacto_email": "gerencia@andina.com", **extra}
        with self.captureOnCommitCallbacks(execute=True):
            return crear_empresa(
                organizacion=self.organizacion,
                razon_social="Constructora Andina",
                ruc=RUC,
                actor=self.actor,
                **datos,
            )

    def test_crea_cuenta_con_ruc_como_usuario_y_sin_contrasena(self):
        empresa = self._crear()

        usuario = empresa.usuario
        self.assertEqual(usuario.nombre_usuario, RUC)
        self.assertEqual(usuario.email, "gerencia@andina.com")
        self.assertEqual(usuario.nombre_completo, "Constructora Andina")
        self.assertEqual(usuario.tipo_cuenta, TipoCuenta.EMPRESA)
        self.assertFalse(usuario.has_usable_password())
        self.assertTrue(
            RolAsignado.objects.filter(
                usuario=usuario, organizacion=self.organizacion, rol__codigo="contratista", activo=True
            ).exists()
        )
        self.assertTrue(EventoAuditoria.objects.filter(accion="CREAR_CUENTA", recurso_id=str(usuario.pk)).exists())

    def test_envia_un_correo_con_el_ruc_y_el_enlace_sin_contrasena(self):
        self._crear()

        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["gerencia@andina.com"])
        self.assertEqual(correo.subject, "SIGECOP \u00b7 Activa tu acceso para Minera del Sur")
        self.assertIn(f"Tu usuario es: {RUC}", correo.body)
        self.assertIn("vence en 72 horas", correo.body)
        self.assertNotIn("contrase\u00f1a es", correo.body.lower())
        self.assertEqual(correo.alternatives[0][1], "text/html")
        self.assertTrue(EventoAuditoria.objects.filter(accion="ENVIAR_ACCESO", resultado="OK").exists())

    def test_flujo_completo_enlace_define_contrasena_y_entra_con_ruc(self):
        empresa = self._crear()
        enlace = enlace_del_correo(mail.outbox[0]).replace("http://localhost:8000", "")

        formulario = self.client.get(enlace, follow=True)
        self.client.post(formulario.redirect_chain[-1][0], {"new_password1": CLAVE, "new_password2": CLAVE})

        self.assertEqual(authenticate(username=RUC, password=CLAVE), empresa.usuario)

    def test_sin_correo_de_contacto_no_crea_cuenta_ni_envia(self):
        empresa = self._crear(contacto_email="")

        self.assertIsNone(empresa.usuario)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(Usuario.objects.filter(nombre_usuario=RUC).exists())

    def test_correo_ya_usado_por_otra_cuenta_rechaza_todo_el_alta(self):
        Usuario.objects.create_user(email="gerencia@andina.com", nombre_completo="Otro")
        usuarios_antes = Usuario.objects.count()

        with self.assertRaisesMessage(DomainError, "El correo gerencia@andina.com ya est\u00e1 asociado a otra cuenta"):
            self._crear()

        self.assertFalse(EmpresaContratista.objects.filter(ruc=RUC).exists())
        self.assertEqual(Usuario.objects.count(), usuarios_antes)
        self.assertEqual(len(mail.outbox), 0)

    def test_mismo_ruc_en_otra_organizacion_reutiliza_la_cuenta(self):
        primera = self._crear()
        otra = Organizacion.objects.create(nombre="Petrolera Norte")

        with self.captureOnCommitCallbacks(execute=True):
            segunda = crear_empresa(
                organizacion=otra, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
                contacto_email="otra@andina.com",
            )

        self.assertEqual(segunda.usuario, primera.usuario)
        self.assertEqual(Usuario.objects.filter(nombre_usuario=RUC).count(), 1)
        self.assertEqual(primera.usuario.roles_asignados.filter(rol__codigo="contratista").count(), 2)
        self.assertTrue(EventoAuditoria.objects.filter(accion="VINCULAR_CUENTA").exists())
        # Todav\u00eda sin activar: el segundo correo vuelve a llevar enlace, a la cuenta existente.
        self.assertEqual(mail.outbox[1].to, ["gerencia@andina.com"])
        self.assertIn("/activar/", mail.outbox[1].body)

    def test_cuenta_ya_activa_en_otra_organizacion_recibe_aviso_sin_enlace(self):
        primera = self._crear()
        primera.usuario.set_password(CLAVE)
        primera.usuario.save()
        otra = Organizacion.objects.create(nombre="Petrolera Norte")

        with self.captureOnCommitCallbacks(execute=True):
            crear_empresa(organizacion=otra, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
                          contacto_email="gerencia@andina.com")

        aviso = mail.outbox[1]
        self.assertEqual(aviso.subject, "SIGECOP \u00b7 Nuevo acceso habilitado en Petrolera Norte")
        self.assertNotIn("/activar/", aviso.body)
        self.assertTrue(primera.usuario.check_password(CLAVE))

    def test_ruc_que_pertenece_a_una_cuenta_de_persona_es_rechazado(self):
        Usuario.objects.create_user(
            email="p@x.com", nombre_completo="P", nombre_usuario=RUC, tipo_cuenta=TipoCuenta.PERSONA
        )

        with self.assertRaisesMessage(DomainError, f"El usuario {RUC} ya existe con otro tipo de cuenta."):
            self._crear()

        self.assertFalse(EmpresaContratista.objects.filter(ruc=RUC).exists())

    def test_fallo_smtp_no_revierte_la_empresa_y_deja_alerta_y_auditoria(self):
        with mock.patch("apps.identidad.correos.enviar_correo", side_effect=smtplib.SMTPException("caido")):
            empresa = self._crear()

        self.assertIsNotNone(empresa.usuario)
        self.assertTrue(EventoAuditoria.objects.filter(accion="ENVIAR_ACCESO", resultado="ERROR").exists())
        alerta = AlertaNotificacion.objects.get(tipo="ACCESO_NO_ENVIADO")
        self.assertEqual(alerta.organizacion, self.organizacion)
        self.assertIn(RUC, alerta.mensaje)


class ActualizarEmpresaSincronizaCuentaTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.actor = preparar_staff(self.organizacion)
        with self.captureOnCommitCallbacks(execute=True):
            self.empresa = crear_empresa(
                organizacion=self.organizacion, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
                contacto_email="gerencia@andina.com",
            )

    def test_cambiar_ruc_y_razon_social_actualiza_la_cuenta(self):
        actualizar_empresa(empresa=self.empresa, actor=self.actor, razon_social="Andina S.A.", ruc="1790099999001")

        self.empresa.usuario.refresh_from_db()
        self.assertEqual(self.empresa.usuario.nombre_usuario, "1790099999001")
        self.assertEqual(self.empresa.usuario.nombre_completo, "Andina S.A.")
        self.assertTrue(EventoAuditoria.objects.filter(accion="SINCRONIZAR_CUENTA").exists())

    def test_cuenta_compartida_no_se_renombra_y_se_revierte_la_edicion(self):
        otra = Organizacion.objects.create(nombre="Petrolera Norte")
        crear_empresa(organizacion=otra, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
                      contacto_email="gerencia@andina.com")

        with self.assertRaisesMessage(DomainError, "La cuenta de acceso se comparte con otra organizaci\u00f3n"):
            actualizar_empresa(empresa=self.empresa, actor=self.actor, razon_social="X", ruc="1790099999001")

        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.ruc, RUC)
        self.assertEqual(Usuario.objects.get(pk=self.empresa.usuario_id).nombre_usuario, RUC)

    def test_editar_solo_contacto_no_toca_la_cuenta(self):
        actualizar_empresa(
            empresa=self.empresa, actor=self.actor, razon_social="Constructora Andina", ruc=RUC,
            contacto_telefono="099",
        )

        self.assertFalse(EventoAuditoria.objects.filter(accion="SINCRONIZAR_CUENTA").exists())


class EmpresaVistasTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.staff = preparar_staff(self.organizacion)
        self.client.force_login(self.staff)

    def test_crear_desde_el_modal_exige_correo_de_contacto(self):
        respuesta = self.client.post(
            reverse("empresas:crear"), {"razon_social": "Constructora Andina", "ruc": RUC}, **HTMX
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Este campo es requerido.")
        self.assertFalse(EmpresaContratista.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_crear_desde_el_modal_crea_cuenta_y_envia_enlace(self):
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                reverse("empresas:crear"),
                {"razon_social": "Constructora Andina", "ruc": RUC, "contacto_email": "gerencia@andina.com"},
                **HTMX,
            )

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("empresas:lista"))
        self.assertEqual(EmpresaContratista.objects.get().usuario.nombre_usuario, RUC)
        self.assertEqual(mail.outbox[0].to, ["gerencia@andina.com"])

    def test_editar_sigue_permitiendo_correo_vacio(self):
        empresa = crear_empresa(organizacion=self.organizacion, razon_social="Sin correo", ruc=RUC, actor=self.staff)

        respuesta = self.client.post(
            reverse("empresas:editar", args=[empresa.pk]), {"razon_social": "Sin correo SA", "ruc": RUC}, **HTMX
        )

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("empresas:lista"))
        empresa.refresh_from_db()
        self.assertEqual(empresa.razon_social, "Sin correo SA")

    def test_lista_muestra_el_estado_del_acceso(self):
        crear_empresa(organizacion=self.organizacion, razon_social="Sin correo", ruc="1700000000001", actor=self.staff)
        crear_empresa(organizacion=self.organizacion, razon_social="Con correo", ruc=RUC, actor=self.staff,
                      contacto_email="gerencia@andina.com")

        respuesta = self.client.get(reverse("empresas:lista"))

        self.assertContains(respuesta, "Sin cuenta")
        self.assertContains(respuesta, "Pendiente de activaci\u00f3n")
        self.assertContains(respuesta, reverse("empresas:enviar_acceso", args=[EmpresaContratista.objects.get(ruc=RUC).pk]))


class EnviarAccesoEmpresaTests(TestCase):
    """S9: el personal reenv\u00eda el enlace al correo de contacto vigente."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.staff = preparar_staff(self.organizacion)
        self.client.force_login(self.staff)
        self.empresa = crear_empresa(
            organizacion=self.organizacion, razon_social="Constructora Andina", ruc=RUC, actor=self.staff,
            contacto_email="gerencia@andina.com",
        )
        self.url = reverse("empresas:enviar_acceso", args=[self.empresa.pk])

    def test_modal_muestra_usuario_y_destinatario(self):
        respuesta = self.client.get(self.url, **HTMX)

        self.assertContains(respuesta, RUC)
        self.assertContains(respuesta, "gerencia@andina.com")

    def test_reenvia_enlace_al_correo_de_contacto_actualizado(self):
        actualizar_empresa(empresa=self.empresa, actor=self.staff, razon_social="Constructora Andina", ruc=RUC,
                           contacto_email="nuevo@andina.com")

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(self.url, **HTMX)

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("empresas:lista"))
        self.assertEqual(mail.outbox[-1].to, ["nuevo@andina.com"])
        self.assertIn("/activar/", mail.outbox[-1].body)
        self.empresa.usuario.refresh_from_db()
        self.assertEqual(self.empresa.usuario.email, "nuevo@andina.com")

    def test_empresa_sin_cuenta_la_crea_al_enviar(self):
        sin_cuenta = crear_empresa(
            organizacion=self.organizacion, razon_social="Tard\u00eda", ruc="1700000000001", actor=self.staff
        )
        sin_cuenta.contacto_email = "tardia@x.com"
        sin_cuenta.save()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("empresas:enviar_acceso", args=[sin_cuenta.pk]), **HTMX)

        sin_cuenta.refresh_from_db()
        self.assertEqual(sin_cuenta.usuario.nombre_usuario, "1700000000001")
        self.assertEqual(mail.outbox[-1].to, ["tardia@x.com"])

    def test_sin_correo_muestra_error_y_no_envia(self):
        sin_correo = crear_empresa(
            organizacion=self.organizacion, razon_social="Sin correo", ruc="1700000000001", actor=self.staff
        )

        respuesta = self.client.post(reverse("empresas:enviar_acceso", args=[sin_correo.pk]), **HTMX)

        self.assertContains(respuesta, "Se necesita un correo de contacto para enviar el acceso.")
        self.assertEqual(len(mail.outbox), 0)

    def test_empresa_de_otra_organizacion_da_404(self):
        ajena = crear_empresa(
            organizacion=Organizacion.objects.create(nombre="Ajena"), razon_social="Ajena", ruc="1700000000001",
            actor=self.staff, contacto_email="ajena@x.com",
        )

        respuesta = self.client.post(reverse("empresas:enviar_acceso", args=[ajena.pk]), **HTMX)

        self.assertEqual(respuesta.status_code, 404)

    def test_sin_htmx_redirige_a_la_lista(self):
        respuesta = self.client.get(self.url)

        self.assertRedirects(respuesta, reverse("empresas:lista"))


class CuentaCompartidaTests(TestCase):
    """Verificación T5: una cuenta compartida entre organizaciones no la controla ninguna de ellas sola."""

    def setUp(self):
        self.org_victima = Organizacion.objects.create(nombre="Minera del Sur")
        self.org_otra = Organizacion.objects.create(nombre="Petrolera Norte")
        self.actor = preparar_staff(self.org_victima)
        self.original = crear_empresa(
            organizacion=self.org_victima, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
            contacto_email="gerencia@andina.com",
        )
        self.original.usuario.set_password(CLAVE)
        self.original.usuario.save()
        self.ajena = crear_empresa(
            organizacion=self.org_otra, razon_social="Constructora Andina", ruc=RUC, actor=self.actor,
            contacto_email="atacante@evil.com",
        )

    def test_reenviar_desde_otra_organizacion_no_cambia_el_correo_de_la_cuenta(self):
        with self.captureOnCommitCallbacks(execute=True):
            enviar_acceso(titular=self.ajena, actor=self.actor)

        usuario = Usuario.objects.get(nombre_usuario=RUC)
        self.assertEqual(usuario.email, "gerencia@andina.com")
        self.assertEqual(mail.outbox[-1].to, ["gerencia@andina.com"])
        self.assertTrue(usuario.check_password(CLAVE))

    def test_modal_de_reenvio_no_revela_el_correo_de_la_cuenta_compartida(self):
        staff_otra = preparar_staff(self.org_otra, email="admin@petrolera.com")
        self.client.force_login(staff_otra)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("empresas:enviar_acceso", args=[self.ajena.pk]), **HTMX)
        respuesta = self.client.get(reverse("empresas:lista"))

        self.assertNotContains(respuesta, "gerencia@andina.com")
        self.assertContains(respuesta, "Enlace de acceso enviado al correo registrado de la cuenta.")

    def test_editar_razon_social_de_titular_con_cuenta_compartida_esta_permitido(self):
        actualizar_empresa(empresa=self.ajena, actor=self.actor, razon_social="Andina Norte", ruc=RUC)

        self.ajena.refresh_from_db()
        self.assertEqual(self.ajena.razon_social, "Andina Norte")
        self.assertEqual(Usuario.objects.get(nombre_usuario=RUC).nombre_completo, "Constructora Andina")


def usuario_con_rol(organizacion, codigo, activo=True):
    usuario = Usuario.objects.create_user(
        email=f"{codigo}@cliente.com", nombre_completo=codigo, password="UnaClaveSegura123!"
    )
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
    RolAsignado.objects.create(usuario=usuario, organizacion=organizacion, rol=rol, activo=activo)
    return usuario


class EmpresaPermisosTests(TestCase):
    """S3: las vistas de empresas deniegan por defecto según la matriz de roles."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.admin = preparar_staff(self.organizacion)
        self.empresa = crear_empresa(
            organizacion=self.organizacion, razon_social="Constructora Andina", ruc=RUC, actor=self.admin
        )
        self.datos = {"razon_social": "Nueva SA", "ruc": "1790099999001", "contacto_email": "nueva@empresa.com"}

    def _conteos(self):
        return (EmpresaContratista.objects.count(), Usuario.objects.count(), EventoAuditoria.objects.count())

    def test_garita_no_ve_el_listado_ni_crea_ni_edita_ni_envia_credenciales(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "seguridad_garita"))
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 403)
        self.assertEqual(self.client.get(reverse("empresas:crear"), **HTMX).status_code, 403)
        self.assertEqual(self.client.post(reverse("empresas:crear"), self.datos, **HTMX).status_code, 403)
        editar = reverse("empresas:editar", args=[self.empresa.pk])
        self.assertEqual(self.client.post(editar, self.datos, **HTMX).status_code, 403)
        enviar = reverse("empresas:enviar_acceso", args=[self.empresa.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(enviar, **HTMX).status_code, 403)

        self.assertEqual(self._conteos(), antes)
        self.assertEqual(len(mail.outbox), 0)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.razon_social, "Constructora Andina")

    def test_revisor_documental_ve_el_listado_pero_no_crea_ni_edita_ni_envia_credenciales(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "revisor_documental"))
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 200)
        self.assertEqual(self.client.post(reverse("empresas:crear"), self.datos, **HTMX).status_code, 403)
        editar = reverse("empresas:editar", args=[self.empresa.pk])
        self.assertEqual(self.client.post(editar, self.datos, **HTMX).status_code, 403)
        enviar = reverse("empresas:enviar_acceso", args=[self.empresa.pk])
        self.assertEqual(self.client.post(enviar, **HTMX).status_code, 403)

        self.assertEqual(self._conteos(), antes)

    def test_gestor_crea_edita_y_envia_credenciales(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "gestor_contratista"))

        with self.captureOnCommitCallbacks(execute=True):
            creada = self.client.post(reverse("empresas:crear"), self.datos, **HTMX)
        self.assertEqual(creada.headers["HX-Redirect"], reverse("empresas:lista"))
        self.assertTrue(EmpresaContratista.objects.filter(ruc=self.datos["ruc"], organizacion=self.organizacion).exists())

        editar = reverse("empresas:editar", args=[self.empresa.pk])
        editada = self.client.post(editar, {**self.datos, "ruc": RUC, "razon_social": "Andina SA"}, **HTMX)
        self.assertEqual(editada.headers["HX-Redirect"], reverse("empresas:lista"))

        enviar = reverse("empresas:enviar_acceso", args=[self.empresa.pk])
        self.assertEqual(self.client.get(enviar, **HTMX).status_code, 200)

    def test_rol_inactivo_no_otorga_nada(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "gestor_contratista", activo=False))
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 403)
        self.assertEqual(self.client.post(reverse("empresas:crear"), self.datos, **HTMX).status_code, 403)

        self.assertEqual(self._conteos(), antes)

    def test_objeto_de_una_organizacion_donde_falta_la_accion_es_404(self):
        # Gestor en la organización A y solo garita en la B: no puede editar empresas de B.
        otra = Organizacion.objects.create(nombre="Otra minera")
        ajena = crear_empresa(organizacion=otra, razon_social="Ajena SA", ruc="1790088888001", actor=self.admin)
        gestor = usuario_con_rol(self.organizacion, "gestor_contratista")
        garita, _ = Rol.objects.get_or_create(codigo="seguridad_garita", defaults={"nombre": "Garita"})
        RolAsignado.objects.create(usuario=gestor, organizacion=otra, rol=garita)
        self.client.force_login(gestor)
        antes = self._conteos()

        respuesta = self.client.post(reverse("empresas:editar", args=[ajena.pk]), self.datos, **HTMX)

        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(self._conteos(), antes)

    def test_admin_cliente_conserva_todo_el_acceso(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(reverse("empresas:lista")).status_code, 200)
        with self.captureOnCommitCallbacks(execute=True):
            creada = self.client.post(reverse("empresas:crear"), self.datos, **HTMX)
        self.assertEqual(creada.headers["HX-Redirect"], reverse("empresas:lista"))
        enviar = reverse("empresas:enviar_acceso", args=[self.empresa.pk])
        self.assertEqual(self.client.get(enviar, **HTMX).status_code, 200)
