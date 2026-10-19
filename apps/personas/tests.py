from datetime import date

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.core.exceptions import DomainError
from apps.core.models import EventoAuditoria
from apps.empresas.services import crear_empresa
from apps.identidad.models import Organizacion, Rol, RolAsignado, TipoCuenta, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento
from apps.personas.services import actualizar_persona, asignar_persona_a_empresa, registrar_persona

CEDULA = "0912345678"
HTMX = {"HTTP_HX_REQUEST": "true"}


def preparar_staff(organizacion, email="admin@cliente.com"):
    staff = Usuario.objects.create_user(email=email, nombre_completo="Admin Cliente", password="UnaClaveSegura123!")
    rol, _ = Rol.objects.get_or_create(codigo="admin_cliente", defaults={"nombre": "Administrador del cliente"})
    RolAsignado.objects.create(usuario=staff, organizacion=organizacion, rol=rol)
    return staff


class RegistrarPersonaConCuentaTests(TestCase):
    """S1/S2: al registrar una persona se crea su cuenta (usuario = cédula) y se envía el enlace."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.actor = preparar_staff(self.organizacion)

    def _registrar(self, **extra):
        datos = {"contacto_email": "ana@correo.com", **extra}
        with self.captureOnCommitCallbacks(execute=True):
            return registrar_persona(
                organizacion=self.organizacion,
                tipo_documento=TipoDocumento.CEDULA,
                numero_documento=CEDULA,
                nombres="Ana",
                apellidos="Pérez",
                actor=self.actor,
                **datos,
            )

    def test_crea_cuenta_con_cedula_como_usuario_y_rol_trabajador(self):
        persona = self._registrar()

        usuario = persona.usuario
        self.assertEqual(usuario.nombre_usuario, CEDULA)
        self.assertEqual(usuario.nombre_completo, "Ana Pérez")
        self.assertEqual(usuario.tipo_cuenta, TipoCuenta.PERSONA)
        self.assertFalse(usuario.has_usable_password())
        self.assertTrue(
            RolAsignado.objects.filter(usuario=usuario, organizacion=self.organizacion, rol__codigo="trabajador").exists()
        )

    def test_envia_enlace_con_la_cedula(self):
        self._registrar()

        self.assertEqual(mail.outbox[0].to, ["ana@correo.com"])
        self.assertIn(f"Tu usuario es: {CEDULA}", mail.outbox[0].body)
        self.assertIn("/activar/", mail.outbox[0].body)

    def test_sin_correo_no_crea_cuenta(self):
        persona = self._registrar(contacto_email="")

        self.assertIsNone(persona.usuario)
        self.assertEqual(len(mail.outbox), 0)

    def test_cedula_de_una_cuenta_de_empresa_es_rechazada(self):
        Usuario.objects.create_user(
            email="e@x.com", nombre_completo="E", nombre_usuario=CEDULA, tipo_cuenta=TipoCuenta.EMPRESA
        )

        with self.assertRaisesMessage(DomainError, "ya existe con otro tipo de cuenta"):
            self._registrar()

        self.assertFalse(Persona.objects.exists())

    def test_cambiar_documento_actualiza_el_usuario(self):
        persona = self._registrar()

        actualizar_persona(
            persona=persona, actor=self.actor, tipo_documento=TipoDocumento.CEDULA, numero_documento="0999999999",
            nombres="Ana María", apellidos="Pérez",
        )

        persona.usuario.refresh_from_db()
        self.assertEqual(persona.usuario.nombre_usuario, "0999999999")
        self.assertEqual(persona.usuario.nombre_completo, "Ana María Pérez")

    def test_asignar_a_empresa_sigue_funcionando(self):
        persona = self._registrar()
        empresa = crear_empresa(organizacion=self.organizacion, razon_social="Andina", ruc="1790012345001",
                                actor=self.actor)

        asignar_persona_a_empresa(
            persona=persona, empresa=empresa, cargo_o_actividad="Soldadora", fecha_inicio=date(2026, 1, 1),
            actor=self.actor,
        )

        self.assertTrue(Asignacion.objects.filter(persona=persona, empresa=empresa).exists())


class PersonaVistasTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.staff = preparar_staff(self.organizacion)
        self.client.force_login(self.staff)
        self.datos = {
            "tipo_documento": TipoDocumento.CEDULA,
            "numero_documento": CEDULA,
            "nombres": "Ana",
            "apellidos": "Pérez",
        }

    def test_registrar_desde_el_modal_exige_correo(self):
        respuesta = self.client.post(reverse("personas:crear"), self.datos, **HTMX)

        self.assertContains(respuesta, "Este campo es requerido.")
        self.assertFalse(Persona.objects.exists())

    def test_registrar_desde_el_modal_crea_cuenta_y_envia(self):
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(
                reverse("personas:crear"), {**self.datos, "contacto_email": "ana@correo.com"}, **HTMX
            )

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("personas:lista"))
        self.assertEqual(Persona.objects.get().usuario.nombre_usuario, CEDULA)
        self.assertEqual(mail.outbox[0].to, ["ana@correo.com"])

    def test_editar_sigue_permitiendo_correo_vacio(self):
        persona = registrar_persona(organizacion=self.organizacion, actor=self.staff, **self.datos)

        respuesta = self.client.post(
            reverse("personas:editar", args=[persona.pk]), {**self.datos, "nombres": "Ana Lucía"}, **HTMX
        )

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("personas:lista"))

    def test_lista_muestra_estado_del_acceso_y_accion_de_envio(self):
        persona = registrar_persona(
            organizacion=self.organizacion, actor=self.staff, contacto_email="ana@correo.com", **self.datos
        )

        respuesta = self.client.get(reverse("personas:lista"))

        self.assertContains(respuesta, "Pendiente de activación")
        self.assertContains(respuesta, reverse("personas:enviar_acceso", args=[persona.pk]))

    def test_enviar_acceso_reenvia_el_enlace(self):
        persona = registrar_persona(
            organizacion=self.organizacion, actor=self.staff, contacto_email="ana@correo.com", **self.datos
        )

        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.client.post(reverse("personas:enviar_acceso", args=[persona.pk]), **HTMX)

        self.assertEqual(respuesta.headers["HX-Redirect"], reverse("personas:lista"))
        self.assertEqual(mail.outbox[-1].to, ["ana@correo.com"])
        self.assertTrue(EventoAuditoria.objects.filter(accion="ENVIAR_ACCESO").exists())

    def test_enviar_acceso_a_persona_de_otra_organizacion_da_404(self):
        ajena = registrar_persona(
            organizacion=Organizacion.objects.create(nombre="Ajena"), actor=self.staff,
            contacto_email="ajena@x.com", **self.datos,
        )

        respuesta = self.client.post(reverse("personas:enviar_acceso", args=[ajena.pk]), **HTMX)

        self.assertEqual(respuesta.status_code, 404)
