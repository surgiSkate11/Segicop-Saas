from io import BytesIO

from django.test import TestCase
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
