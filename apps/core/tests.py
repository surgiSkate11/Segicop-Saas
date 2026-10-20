from io import BytesIO
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.core.models import AlertaNotificacion, EventoAuditoria, ExportacionAuditoria
from apps.core.services import crear_alerta, exportar_alertas_csv, exportar_alertas_xlsx, marcar_alerta_leida
from apps.empresas.models import EmpresaContratista
from apps.empresas.services import crear_empresa
from apps.identidad.models import Organizacion, Usuario


class AuditoriaTests(TestCase):
    def test_mutacion_de_servicio_persiste_evento_de_auditoria(self):
        organizacion = Organizacion.objects.create(nombre="Organización auditada")
        actor = Usuario.objects.create_user(email="auditor@example.com", nombre_completo="Actor auditado")

        empresa = crear_empresa(
            organizacion=organizacion,
            razon_social="Empresa auditada",
            ruc="1790000000005",
            actor=actor,
        )

        evento = EventoAuditoria.objects.get(accion="CREAR_EMPRESA")
        self.assertEqual(evento.actor_id, actor.id)
        self.assertEqual(evento.organizacion_id, organizacion.id)
        self.assertEqual(evento.recurso_tipo, EmpresaContratista._meta.label)
        self.assertEqual(evento.recurso_id, str(empresa.id))


class NotificacionTests(TestCase):
    def test_crear_alerta_es_idempotente_por_clave(self):
        organizacion = Organizacion.objects.create(nombre="Organización de alertas")

        primera, creada = crear_alerta(
            organizacion=organizacion,
            clave_idempotencia="DOCUMENTO:abc123:VENCIDO",
            tipo="VENCIMIENTO_DOCUMENTO",
            agrupador="DOCUMENTOS_VENCIDOS",
            titulo="Documento vencido",
            mensaje="El documento requiere renovación.",
            severidad="ALTA",
        )
        segunda, repetida = crear_alerta(
            organizacion=organizacion,
            clave_idempotencia="DOCUMENTO:abc123:VENCIDO",
            tipo="VENCIMIENTO_DOCUMENTO",
            agrupador="DOCUMENTOS_VENCIDOS",
            titulo="Mensaje duplicado",
            mensaje="No debe crear otra alerta.",
            severidad="ALTA",
        )

        self.assertTrue(creada)
        self.assertFalse(repetida)
        self.assertEqual(primera.id, segunda.id)
        self.assertEqual(AlertaNotificacion.objects.count(), 1)

    def test_marcar_alerta_leida_persiste_estado_y_auditoria(self):
        organizacion = Organizacion.objects.create(nombre="Organización lectora")
        actor = Usuario.objects.create_user(email="lector@example.com", nombre_completo="Lector de alertas")
        alerta, _ = crear_alerta(
            organizacion=organizacion,
            clave_idempotencia="ALERTA:LECTURA:1",
            tipo="PRUEBA",
            agrupador="PRUEBAS",
            titulo="Alerta de prueba",
            mensaje="Debe marcarse como leída.",
        )

        marcar_alerta_leida(alerta=alerta, actor=actor)

        alerta.refresh_from_db()
        self.assertEqual(alerta.estado, "LEIDA")
        self.assertTrue(EventoAuditoria.objects.filter(accion="MARCAR_ALERTA_LEIDA").exists())

    def test_exportacion_csv_es_auditada_y_filtra_por_organizacion(self):
        organizacion = Organizacion.objects.create(nombre="Organización exportable")
        otra_organizacion = Organizacion.objects.create(nombre="Organización excluida")
        actor = Usuario.objects.create_user(email="exportador@example.com", nombre_completo="Exportador")
        crear_alerta(
            organizacion=organizacion,
            clave_idempotencia="ALERTA:EXPORTABLE",
            tipo="PRUEBA",
            agrupador="PRUEBAS",
            titulo="Incluida",
            mensaje="Debe aparecer.",
        )
        crear_alerta(
            organizacion=otra_organizacion,
            clave_idempotencia="ALERTA:EXCLUIDA",
            tipo="PRUEBA",
            agrupador="PRUEBAS",
            titulo="Excluida",
            mensaje="No debe aparecer.",
        )

        contenido, exportacion = exportar_alertas_csv(
            organizaciones_ids=[organizacion.id],
            actor=actor,
        )

        self.assertIn("Incluida", contenido)
        self.assertNotIn("Excluida", contenido)
        self.assertEqual(exportacion.filas, 1)
        self.assertEqual(len(exportacion.hash_sha256), 64)
        self.assertTrue(ExportacionAuditoria.objects.filter(pk=exportacion.pk).exists())

    def test_exportacion_xlsx_contiene_encabezados_y_es_auditada(self):
        organizacion = Organizacion.objects.create(nombre="Organización XLSX")
        actor = Usuario.objects.create_user(email="xlsx@example.com", nombre_completo="Exportador XLSX")
        crear_alerta(
            organizacion=organizacion,
            clave_idempotencia="ALERTA:XLSX",
            tipo="PRUEBA",
            agrupador="PRUEBAS",
            titulo="Alerta XLSX",
            mensaje="Debe aparecer en la hoja.",
        )

        contenido, exportacion = exportar_alertas_xlsx(
            organizaciones_ids=[organizacion.id],
            actor=actor,
        )
        hoja = load_workbook(BytesIO(contenido), read_only=True).active

        self.assertEqual(tuple(hoja.iter_rows(min_row=1, max_row=1, values_only=True))[0][0], "Organización")
        self.assertEqual(exportacion.formato, "XLSX")
        self.assertEqual(exportacion.filas, 1)


class ConfiguracionCorreoTests(TestCase):
    """S11: el remitente/servidor SMTP se configura desde el panel administrativo."""

    def _configurar(self, **extra):
        from apps.core.models import ConfiguracionCorreo

        datos = {
            "activa": True,
            "host": "smtp.minera.com",
            "puerto": 587,
            "usuario": "notificaciones@minera.com",
            "password": "clave-smtp",
            "usar_tls": True,
            "remitente_nombre": "SIGECOP Minera",
            "remitente_email": "no-responder@minera.com",
            **extra,
        }
        return ConfiguracionCorreo.objects.create(**datos)

    def test_sin_configuracion_usa_los_settings(self):
        from django.conf import settings
        from django.core.mail.backends.locmem import EmailBackend as LocmemBackend

        from apps.core.correo import obtener_conexion, remitente_por_defecto

        self.assertEqual(remitente_por_defecto(), settings.DEFAULT_FROM_EMAIL)
        self.assertIsInstance(obtener_conexion(), LocmemBackend)

    def test_configuracion_activa_define_servidor_y_remitente(self):
        from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

        from apps.core.correo import obtener_conexion, remitente_por_defecto

        self._configurar()

        conexion = obtener_conexion()
        self.assertIsInstance(conexion, SMTPBackend)
        self.assertEqual(
            (conexion.host, conexion.port, conexion.username, conexion.password, conexion.use_tls, conexion.use_ssl),
            ("smtp.minera.com", 587, "notificaciones@minera.com", "clave-smtp", True, False),
        )
        self.assertEqual(remitente_por_defecto(), "SIGECOP Minera <no-responder@minera.com>")

    def test_configuracion_inactiva_se_ignora(self):
        from django.conf import settings

        from apps.core.correo import remitente_por_defecto

        self._configurar(activa=False)

        self.assertEqual(remitente_por_defecto(), settings.DEFAULT_FROM_EMAIL)

    def test_solo_puede_haber_una_configuracion_activa(self):
        from django.db import IntegrityError, transaction

        self._configurar()

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._configurar(host="otro.smtp.com")

    def test_tls_y_ssl_a_la_vez_no_es_valido(self):
        from django.core.exceptions import ValidationError

        from apps.core.models import ConfiguracionCorreo

        configuracion = ConfiguracionCorreo(
            host="smtp.x.com", puerto=465, usar_tls=True, usar_ssl=True, remitente_email="a@x.com"
        )

        with self.assertRaisesMessage(ValidationError, "Usa TLS o SSL, no ambos."):
            configuracion.full_clean()

    def test_enviar_correo_sale_con_el_remitente_configurado(self):
        from django.core import mail

        from apps.core.correo import enviar_correo

        self._configurar()
        with mock.patch("apps.core.correo.obtener_conexion", return_value=mail.get_connection()):
            enviar_correo(asunto="Prueba", destinatarios=["a@x.com"], texto="Hola")

        self.assertEqual(mail.outbox[0].from_email, "SIGECOP Minera <no-responder@minera.com>")


class ConfiguracionCorreoAdminTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_superuser(
            email="root@example.com", nombre_completo="Root", password="UnaClaveSegura123!"
        )
        self.client.force_login(self.admin)

    def _datos(self, **extra):
        return {
            "activa": "on",
            "host": "smtp.minera.com",
            "puerto": "587",
            "usuario": "notificaciones@minera.com",
            "password": "",
            "usar_tls": "on",
            "remitente_nombre": "SIGECOP Minera",
            "remitente_email": "no-responder@minera.com",
            "timeout": "10",
            **extra,
        }

    def test_crear_desde_el_admin_guarda_la_clave(self):
        from apps.core.models import ConfiguracionCorreo

        self.client.post(reverse("admin:core_configuracioncorreo_add"), self._datos(password="clave-smtp"))

        self.assertEqual(ConfiguracionCorreo.objects.get().password, "clave-smtp")

    def test_la_clave_no_se_muestra_y_vacia_conserva_la_anterior(self):
        from apps.core.models import ConfiguracionCorreo

        configuracion = ConfiguracionCorreo.objects.create(
            host="smtp.minera.com", puerto=587, password="clave-smtp", remitente_email="no-responder@minera.com"
        )
        url = reverse("admin:core_configuracioncorreo_change", args=[configuracion.pk])

        self.assertNotContains(self.client.get(url), "clave-smtp")
        self.client.post(url, self._datos(host="smtp2.minera.com"))

        configuracion.refresh_from_db()
        self.assertEqual(configuracion.host, "smtp2.minera.com")
        self.assertEqual(configuracion.password, "clave-smtp")

    def test_accion_enviar_correo_de_prueba_al_administrador(self):
        from django.core import mail

        from apps.core.models import ConfiguracionCorreo

        configuracion = ConfiguracionCorreo.objects.create(
            activa=False, host="smtp.minera.com", puerto=587, remitente_email="no-responder@minera.com"
        )
        with mock.patch("apps.core.correo.obtener_conexion", return_value=mail.get_connection()):
            self.client.post(
                reverse("admin:core_configuracioncorreo_changelist"),
                {"action": "enviar_correo_de_prueba", "_selected_action": [configuracion.pk]},
            )

        self.assertEqual(mail.outbox[0].to, ["root@example.com"])
        self.assertEqual(mail.outbox[0].subject, "SIGECOP · Correo de prueba")
