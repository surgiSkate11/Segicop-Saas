from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.test import TestCase

from apps.core.choices import EstadoCumplimiento
from apps.core.exceptions import DomainError
from apps.core.models import AlertaNotificacion, EventoAuditoria
from apps.cumplimiento import services
from apps.cumplimiento.jobs import procesar_vencimientos
from apps.cumplimiento.models import AplicaA, CatalogoRequisito, EstadoRequisito, Revision
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Organizacion, Usuario


class CumplimientoTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Organización documental")
        self.usuario = Usuario.objects.create_user(
            email="revisor-documental@example.com", nombre_completo="Revisor documental"
        )
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Empresa documental",
            ruc="1790000000003",
        )
        self.catalogo = CatalogoRequisito.objects.create(
            organizacion=self.organizacion,
            codigo="seguro-social",
            nombre="Certificado de seguro social",
            aplica_a=AplicaA.EMPRESA,
            obligatorio=True,
        )

    def test_asignar_requisito_obligatorio_deja_empresa_incompleta(self):
        requisito = services.asignar_requisito(
            catalogo=self.catalogo,
            empresa=self.empresa,
            actor=self.usuario,
        )

        self.empresa.refresh_from_db()
        self.assertEqual(requisito.estado, EstadoRequisito.PENDIENTE)
        self.assertEqual(self.empresa.estado, EstadoCumplimiento.INCOMPLETO)

    def test_crear_catalogo_pasa_por_servicio_y_rechaza_duplicados(self):
        segundo = services.crear_catalogo_requisito(
            organizacion=self.organizacion,
            actor=self.usuario,
            codigo="induccion",
            nombre="Inducción de seguridad",
            aplica_a=AplicaA.EMPRESA,
        )

        self.assertEqual(segundo.organizacion_id, self.organizacion.id)
        with self.assertRaises(DomainError):
            services.crear_catalogo_requisito(
                organizacion=self.organizacion,
                actor=self.usuario,
                codigo="induccion",
                nombre="Otro nombre",
                aplica_a=AplicaA.EMPRESA,
            )

    def test_documento_se_versiona_y_no_se_autoaprueba(self):
        requisito = services.asignar_requisito(
            catalogo=self.catalogo,
            empresa=self.empresa,
            actor=self.usuario,
        )

        documento = services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"contenido inicial", name="seguro.pdf"),
            actor=self.usuario,
            fecha_vencimiento=date.today() + timedelta(days=30),
        )

        requisito.refresh_from_db()
        self.assertEqual(documento.version, 1)
        self.assertEqual(len(documento.hash_sha256), 64)
        self.assertEqual(requisito.estado, EstadoRequisito.EN_REVISION)

    def test_revision_aprobada_habilita_empresa(self):
        requisito = services.asignar_requisito(
            catalogo=self.catalogo,
            empresa=self.empresa,
            actor=self.usuario,
        )
        documento = services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"contenido aprobado", name="seguro.pdf"),
            actor=self.usuario,
        )

        revision = services.revisar_documento(
            documento_version=documento,
            revisor=self.usuario,
            decision=Revision.Decision.APROBADO,
        )

        self.empresa.refresh_from_db()
        self.assertEqual(revision.decision, Revision.Decision.APROBADO)
        self.assertEqual(self.empresa.estado, EstadoCumplimiento.HABILITADO)

    def test_observacion_y_rechazo_requieren_comentario(self):
        requisito = services.asignar_requisito(
            catalogo=self.catalogo,
            empresa=self.empresa,
            actor=self.usuario,
        )
        documento = services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"contenido", name="seguro.pdf"),
            actor=self.usuario,
        )

        for decision in (Revision.Decision.OBSERVADO, Revision.Decision.RECHAZADO):
            with self.subTest(decision=decision), self.assertRaises(DomainError):
                services.revisar_documento(
                    documento_version=documento,
                    revisor=self.usuario,
                    decision=decision,
                )

    def test_job_de_vencimientos_crea_alerta_idempotente(self):
        requisito = services.asignar_requisito(
            catalogo=self.catalogo,
            empresa=self.empresa,
            actor=self.usuario,
        )
        documento = services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"contenido vencido", name="seguro.pdf"),
            actor=self.usuario,
            fecha_vencimiento=date.today() - timedelta(days=1),
        )
        services.revisar_documento(
            documento_version=documento,
            revisor=self.usuario,
            decision=Revision.Decision.APROBADO,
        )

        primera = procesar_vencimientos()
        segunda = procesar_vencimientos()

        requisito.refresh_from_db()
        self.assertEqual(requisito.estado, EstadoRequisito.VENCIDO)
        self.assertEqual(primera["documentos"], 1)
        self.assertEqual(segunda["documentos"], 0)
        self.assertEqual(AlertaNotificacion.objects.count(), 1)
        self.assertEqual(documento.revisiones.count(), 1)
        evento = EventoAuditoria.objects.get(accion="VENCER_REQUISITO", recurso_id=str(requisito.id))
        self.assertIn("APROBADO -> VENCIDO", evento.motivo)
