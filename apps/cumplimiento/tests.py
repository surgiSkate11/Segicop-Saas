from datetime import date, timedelta

from celery.schedules import crontab
from django.conf import settings
from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.choices import EstadoCumplimiento
from apps.core.exceptions import DomainError
from apps.core.models import AlertaNotificacion, EventoAuditoria
from apps.cumplimiento import services
from apps.cumplimiento.jobs import procesar_vencimientos
from apps.cumplimiento.models import (
    AplicaA,
    CatalogoRequisito,
    DocumentoVersion,
    EstadoRequisito,
    RequisitoAplicado,
    Revision,
)
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Organizacion, Rol, RolAsignado, Usuario


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


HTMX = {"HTTP_HX_REQUEST": "true"}


def usuario_con_rol(organizacion, codigo, activo=True):
    usuario = Usuario.objects.create_user(
        email=f"{codigo}@cliente.com", nombre_completo=codigo, password="UnaClaveSegura123!"
    )
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
    RolAsignado.objects.create(usuario=usuario, organizacion=organizacion, rol=rol, activo=activo)
    return usuario


class CumplimientoPermisosTests(TestCase):
    """S3: las vistas de cumplimiento deniegan por defecto según la matriz de roles."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Organización documental")
        self.admin = usuario_con_rol(self.organizacion, "admin_cliente")
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion, razon_social="Empresa documental", ruc="1790000000003"
        )
        self.catalogo = CatalogoRequisito.objects.create(
            organizacion=self.organizacion,
            codigo="seguro-social",
            nombre="Certificado de seguro social",
            aplica_a=AplicaA.EMPRESA,
            obligatorio=True,
        )
        self.requisito = services.asignar_requisito(catalogo=self.catalogo, empresa=self.empresa, actor=self.admin)
        services.subir_documento(
            requisito_aplicado=self.requisito, archivo=ContentFile(b"contenido", name="seguro.pdf"), actor=self.admin
        )
        self.url_detalle = reverse("cumplimiento:detalle", args=[self.requisito.pk])

    def _conteos(self):
        return (
            DocumentoVersion.objects.count(),
            Revision.objects.count(),
            RequisitoAplicado.objects.count(),
            CatalogoRequisito.objects.count(),
            EventoAuditoria.objects.count(),
        )

    def _revisar(self):
        return self.client.post(self.url_detalle, {"accion": "revisar", "decision": Revision.Decision.APROBADO})

    def _subir(self):
        archivo = SimpleUploadedFile("nuevo.pdf", b"otro contenido", content_type="application/pdf")
        return self.client.post(self.url_detalle, {"accion": "subir", "archivo": archivo})

    def _crear_catalogo(self):
        datos = {"codigo": "induccion", "nombre": "Inducción", "aplica_a": AplicaA.EMPRESA}
        return self.client.post(reverse("cumplimiento:catalogo_nuevo"), datos, **HTMX)

    def _asignar(self):
        url = reverse("cumplimiento:catalogo_asignar", args=[self.catalogo.pk])
        return self.client.post(url, {"empresa": self.empresa.pk}, **HTMX)

    def test_garita_no_accede_a_ninguna_vista_de_cumplimiento(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "seguridad_garita"))
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("cumplimiento:bandeja")).status_code, 403)
        self.assertEqual(self.client.get(self.url_detalle).status_code, 403)
        self.assertEqual(self._revisar().status_code, 403)
        self.assertEqual(self._subir().status_code, 403)
        self.assertEqual(self.client.get(reverse("cumplimiento:catalogo")).status_code, 403)
        self.assertEqual(self._crear_catalogo().status_code, 403)
        self.assertEqual(self._asignar().status_code, 403)

        self.assertEqual(self._conteos(), antes)

    def test_revisor_documental_revisa_pero_no_sube_ni_configura_ni_asigna(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "revisor_documental"))
        self.assertEqual(self.client.get(self.url_detalle).status_code, 200)
        self.assertEqual(self.client.get(reverse("cumplimiento:bandeja")).status_code, 200)

        respuesta = self._revisar()

        self.assertRedirects(respuesta, self.url_detalle, fetch_redirect_response=False)
        self.assertEqual(Revision.objects.count(), 1)
        antes = self._conteos()
        self.assertEqual(self._subir().status_code, 403)
        self.assertEqual(self._crear_catalogo().status_code, 403)
        self.assertEqual(self._asignar().status_code, 403)
        self.assertEqual(self._conteos(), antes)

    def test_gestor_sube_y_asigna_pero_no_revisa_ni_configura(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "gestor_contratista"))

        subida = self._subir()
        self.assertRedirects(subida, self.url_detalle, fetch_redirect_response=False)
        self.assertEqual(DocumentoVersion.objects.count(), 2)
        asignada = self._asignar()
        self.assertEqual(asignada.headers["HX-Redirect"], reverse("cumplimiento:catalogo"))
        antes = self._conteos()

        self.assertEqual(self._revisar().status_code, 403)
        self.assertEqual(self._crear_catalogo().status_code, 403)

        self.assertEqual(self._conteos(), antes)

    def test_rol_inactivo_no_otorga_nada(self):
        self.client.force_login(usuario_con_rol(self.organizacion, "revisor_documental", activo=False))
        antes = self._conteos()

        self.assertEqual(self.client.get(reverse("cumplimiento:bandeja")).status_code, 403)
        self.assertEqual(self._revisar().status_code, 403)

        self.assertEqual(self._conteos(), antes)

    def test_requisito_de_otra_organizacion_es_404(self):
        otra = Organizacion.objects.create(nombre="Otra organización")
        revisor = usuario_con_rol(self.organizacion, "revisor_documental")
        RolAsignado.objects.create(
            usuario=revisor, organizacion=otra, rol=Rol.objects.get_or_create(codigo="seguridad_garita")[0]
        )
        ajeno_catalogo = CatalogoRequisito.objects.create(
            organizacion=otra, codigo="x", nombre="X", aplica_a=AplicaA.EMPRESA
        )
        ajena = EmpresaContratista.objects.create(organizacion=otra, razon_social="Ajena", ruc="1790000000099")
        ajeno = services.asignar_requisito(catalogo=ajeno_catalogo, empresa=ajena, actor=self.admin)
        self.client.force_login(revisor)

        respuesta = self.client.get(reverse("cumplimiento:detalle", args=[ajeno.pk]))

        self.assertEqual(respuesta.status_code, 404)

    def test_admin_cliente_conserva_todo_el_acceso(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(reverse("cumplimiento:catalogo")).status_code, 200)
        self.assertEqual(self._crear_catalogo().headers["HX-Redirect"], reverse("cumplimiento:catalogo"))
        self.assertRedirects(self._subir(), self.url_detalle, fetch_redirect_response=False)
        self.assertRedirects(self._revisar(), self.url_detalle, fetch_redirect_response=False)
        self.assertEqual(Revision.objects.count(), 1)
        self.assertEqual(self._asignar().headers["HX-Redirect"], reverse("cumplimiento:catalogo"))


class JobDiarioYNotificacionesTests(TestCase):
    """T5 / S8: aviso previo al vencimiento (RF-033), hora fija y resumen diario por correo (RF-060/061)."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.otra = Organizacion.objects.create(nombre="Otra minera")
        self.admin = usuario_con_rol(self.organizacion, "admin_cliente")
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion, razon_social="Transportes Andinos", ruc="1790000000009"
        )
        self.catalogo = CatalogoRequisito.objects.create(
            organizacion=self.organizacion, codigo="poliza", nombre="Póliza de responsabilidad", aplica_a=AplicaA.EMPRESA
        )

    def _documento_aprobado(self, dias_para_vencer):
        requisito = services.asignar_requisito(catalogo=self.catalogo, empresa=self.empresa, actor=self.admin)
        documento = services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"poliza", name="poliza.pdf"),
            actor=self.admin,
            fecha_vencimiento=date.today() + timedelta(days=dias_para_vencer),
        )
        services.revisar_documento(documento_version=documento, revisor=self.admin, decision=Revision.Decision.APROBADO)
        return documento

    def _avisos(self):
        return AlertaNotificacion.objects.filter(tipo="DOCUMENTO_POR_VENCER")

    def test_avisa_una_sola_vez_por_umbral_antes_del_vencimiento(self):
        documento = self._documento_aprobado(dias_para_vencer=20)

        primera = procesar_vencimientos()
        segunda = procesar_vencimientos()

        self.assertEqual(primera["avisos_previos"], 1)
        self.assertEqual(segunda["avisos_previos"], 0)
        aviso = self._avisos().get()
        self.assertIn("Póliza de responsabilidad", aviso.titulo)
        self.assertEqual(aviso.fecha_referencia, documento.fecha_vencimiento)
        self.assertEqual(aviso.organizacion, self.organizacion)

        # Pasa el tiempo: ahora faltan 5 días y se cruza el umbral de 7.
        DocumentoVersion.objects.filter(pk=documento.pk).update(fecha_vencimiento=date.today() + timedelta(days=5))
        self.assertEqual(procesar_vencimientos()["avisos_previos"], 1)
        self.assertEqual(self._avisos().count(), 2)

    def test_documento_que_vence_en_cinco_dias_recibe_solo_el_aviso_del_umbral_menor(self):
        self._documento_aprobado(dias_para_vencer=5)

        procesar_vencimientos()

        self.assertEqual(self._avisos().count(), 1)
        self.assertIn("5 días", self._avisos().get().mensaje)

    def test_no_avisa_fuera_del_umbral_ni_documentos_sin_aprobar(self):
        self._documento_aprobado(dias_para_vencer=40)
        requisito = services.asignar_requisito(
            catalogo=CatalogoRequisito.objects.create(
                organizacion=self.organizacion, codigo="ruc", nombre="RUC", aplica_a=AplicaA.EMPRESA
            ),
            empresa=self.empresa,
            actor=self.admin,
        )
        services.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"ruc", name="ruc.pdf"),
            actor=self.admin,
            fecha_vencimiento=date.today() + timedelta(days=3),
        )

        self.assertEqual(procesar_vencimientos()["avisos_previos"], 0)
        self.assertEqual(self._avisos().count(), 0)

    @override_settings(SIGECOP_DIAS_AVISO_VENCIMIENTO=(15,))
    def test_los_umbrales_son_configurables(self):
        self._documento_aprobado(dias_para_vencer=20)

        self.assertEqual(procesar_vencimientos()["avisos_previos"], 0)

    def test_resumen_diario_llega_solo_al_administrador_de_la_organizacion(self):
        revisor = usuario_con_rol(self.organizacion, "revisor_documental")
        garita = usuario_con_rol(self.organizacion, "seguridad_garita")
        admin_otra = Usuario.objects.create_user(
            email="admin@otra.com", nombre_completo="Admin otra", password="UnaClaveSegura123!"
        )
        RolAsignado.objects.create(
            usuario=admin_otra, organizacion=self.otra, rol=Rol.objects.get(codigo="admin_cliente")
        )
        self._documento_aprobado(dias_para_vencer=20)

        with self.captureOnCommitCallbacks(execute=True):
            resultado = procesar_vencimientos()

        self.assertEqual(resultado["resumenes_enviados"], 1)
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, [self.admin.email])
        self.assertNotIn(revisor.email, correo.to)
        self.assertNotIn(garita.email, correo.to)
        self.assertIn("Póliza de responsabilidad", correo.body)
        self.assertIn("Minera del Sur", correo.subject)
        self.assertTrue(
            EventoAuditoria.objects.filter(accion="ENVIAR_RESUMEN_ALERTAS", organizacion=self.organizacion).exists()
        )

    def test_el_resumen_no_se_repite_el_mismo_dia_ni_sale_sin_alertas(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(procesar_vencimientos()["resumenes_enviados"], 0)
        self.assertEqual(len(mail.outbox), 0)

        self._documento_aprobado(dias_para_vencer=20)
        with self.captureOnCommitCallbacks(execute=True):
            procesar_vencimientos()
            procesar_vencimientos()

        self.assertEqual(len(mail.outbox), 1)

    def test_el_job_corre_a_hora_fija_en_la_zona_de_ecuador(self):
        programacion = settings.CELERY_BEAT_SCHEDULE["procesar-vencimientos-diario"]["schedule"]

        self.assertIsInstance(programacion, crontab)
        self.assertEqual(programacion.hour, {settings.SIGECOP_JOB_DIARIO_HORA})
        self.assertEqual(programacion.minute, {0})
        self.assertEqual(settings.CELERY_TIMEZONE, "America/Guayaquil")
