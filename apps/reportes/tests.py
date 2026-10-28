import io
from datetime import date, timedelta

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.antecedentes.models import EstadoVerificacion, ModoConector, ResultadoAntecedentes, VerificacionAntecedentes
from apps.core.choices import EstadoCumplimiento
from apps.core.models import EventoAuditoria, ExportacionAuditoria
from apps.cumplimiento import services as cumplimiento
from apps.cumplimiento.models import AplicaA, CatalogoRequisito, Revision
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento

COLUMNAS_INFORMACION = {
    "cedula", "trabajador", "empresa", "ruc", "cargo", "estado_documental", "habilitado",
    "fecha_nacimiento", "correo", "telefono",
}


def usuario_con_rol(organizacion, codigo):
    usuario = Usuario.objects.create_user(
        email=f"{codigo}@cliente.com", nombre_completo=codigo, password="UnaClaveSegura123!"
    )
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
    RolAsignado.objects.create(usuario=usuario, organizacion=organizacion, rol=rol)
    return usuario


def crear_trabajador(organizacion, empresa, documento, nombres, estado=EstadoCumplimiento.BORRADOR):
    persona = Persona.objects.create(
        organizacion=organizacion,
        tipo_documento=TipoDocumento.CEDULA,
        numero_documento=documento,
        nombres=nombres,
        apellidos="Pruebas",
        contacto_email=f"{documento}@correo.com",
        estado=estado,
    )
    Asignacion.objects.create(
        organizacion=organizacion,
        persona=persona,
        empresa=empresa,
        cargo_o_actividad="Supervisor",
        fecha_inicio=date.today(),
    )
    return persona


class ReportesTests(TestCase):
    """T8 / S10: informes del sistema anterior con filtros y exportación a Excel auditada (RF-063, RN-009)."""

    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Minera del Sur")
        self.otra = Organizacion.objects.create(nombre="Otra minera")
        self.admin = usuario_con_rol(self.organizacion, "admin_cliente")
        self.revisor = usuario_con_rol(self.organizacion, "revisor_documental")
        self.garita = usuario_con_rol(self.organizacion, "seguridad_garita")
        self.andinos = EmpresaContratista.objects.create(
            organizacion=self.organizacion, razon_social="Transportes Andinos", ruc="1790000000009"
        )
        self.pacifico = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Servicios Pacífico",
            ruc="1790000000011",
            estado=EstadoCumplimiento.HABILITADO,
        )
        self.ana = crear_trabajador(self.organizacion, self.andinos, "0102030405", "Ana")
        self.luis = crear_trabajador(
            self.organizacion, self.pacifico, "0911111111", "Luis", estado=EstadoCumplimiento.HABILITADO
        )
        VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.luis,
            estado=EstadoVerificacion.VALIDADA,
            modo_conector=ModoConector.MANUAL,
            resultado=ResultadoAntecedentes.NO,
            vigente_hasta=date.today() + timedelta(days=60),
            validado_por=self.admin,
        )
        ajena = EmpresaContratista.objects.create(organizacion=self.otra, razon_social="Ajena SA", ruc="1790000000010")
        crear_trabajador(self.otra, ajena, "0999999999", "Ajeno")

        catalogo = CatalogoRequisito.objects.create(
            organizacion=self.organizacion, codigo="cedula", nombre="Cédula de identidad", aplica_a=AplicaA.PERSONA
        )
        requisito = cumplimiento.asignar_requisito(catalogo=catalogo, persona=self.ana, actor=self.admin)
        self.vence = date.today() + timedelta(days=200)
        documento = cumplimiento.subir_documento(
            requisito_aplicado=requisito,
            archivo=ContentFile(b"cedula", name="cedula.pdf"),
            actor=self.admin,
            fecha_vencimiento=self.vence,
        )
        self.emision = date.today() - timedelta(days=5)
        cumplimiento.revisar_documento(
            documento_version=documento,
            revisor=self.revisor,
            decision=Revision.Decision.APROBADO,
            fecha_emision=self.emision,
        )

    def _get(self, nombre, usuario=None, **params):
        self.client.force_login(usuario or self.admin)
        return self.client.get(reverse(f"reportes:{nombre}"), params)

    def test_documentos_de_trabajadores_muestra_cada_documento_con_fechas(self):
        respuesta = self._get("documentos_trabajadores")

        self.assertEqual(respuesta.status_code, 200)
        filas = respuesta.context["filas"]
        self.assertEqual(len(filas), 1)
        fila = filas[0]
        self.assertEqual(
            (fila["cedula"], fila["empresa"], fila["documento"], fila["estado"]),
            ("0102030405", "Transportes Andinos", "Cédula de identidad", "Aprobado"),
        )
        self.assertEqual((fila["fecha_emision"], fila["fecha_caducidad"]), (self.emision, self.vence))
        self.assertNotContains(respuesta, "Ajeno")

    def test_informacion_de_trabajadores_muestra_habilitacion_sin_antecedentes(self):
        respuesta = self._get("informacion_trabajadores")

        filas = {fila["cedula"]: fila for fila in respuesta.context["filas"]}
        self.assertEqual(set(filas), {"0102030405", "0911111111"})
        self.assertEqual(set(filas["0911111111"]), COLUMNAS_INFORMACION)
        self.assertEqual((filas["0911111111"]["habilitado"], filas["0102030405"]["habilitado"]), ("Sí", "No"))
        self.assertEqual(filas["0911111111"]["ruc"], "1790000000011")
        self.assertNotContains(respuesta, "Antecedentes")

    def test_filtros_por_empresa_y_palabra_clave(self):
        filas = self._get("informacion_trabajadores", empresa=str(self.pacifico.pk)).context["filas"]
        self.assertEqual([fila["cedula"] for fila in filas], ["0911111111"])

        filas = self._get("informacion_trabajadores", q="ana").context["filas"]
        self.assertEqual([fila["cedula"] for fila in filas], ["0102030405"])

        filas = self._get("documentos_trabajadores", q="pasaporte").context["filas"]
        self.assertEqual(filas, [])

        respuesta = self._get("informacion_trabajadores", empresa="no-es-uuid")
        self.assertEqual(len(respuesta.context["filas"]), 2)

    def test_filtro_de_fechas_por_caducidad(self):
        manana = date.today() + timedelta(days=1)
        filas = self._get("documentos_trabajadores", desde=manana.isoformat(), hasta=self.vence.isoformat())
        self.assertEqual(len(filas.context["filas"]), 1)

        filas = self._get("documentos_trabajadores", hasta=manana.isoformat())
        self.assertEqual(filas.context["filas"], [])

    def test_garita_no_ve_ni_exporta_reportes(self):
        for nombre in ("indice", "documentos_trabajadores", "informacion_trabajadores", "documentos_trabajadores_excel"):
            with self.subTest(nombre=nombre):
                self.assertEqual(self._get(nombre, usuario=self.garita).status_code, 403)
        self.assertEqual(ExportacionAuditoria.objects.count(), 0)

    def test_quien_solo_ve_registros_no_exporta(self):
        self.assertEqual(self._get("documentos_trabajadores", usuario=self.revisor).status_code, 200)
        antes = (ExportacionAuditoria.objects.count(), EventoAuditoria.objects.count())

        respuesta = self._get("informacion_trabajadores_excel", usuario=self.revisor)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual((ExportacionAuditoria.objects.count(), EventoAuditoria.objects.count()), antes)
        self.assertNotContains(self._get("documentos_trabajadores", usuario=self.revisor), "Descargar a Excel")

    def test_exportar_a_excel_queda_auditado_con_filtros(self):
        respuesta = self._get("documentos_trabajadores_excel", empresa=str(self.andinos.pk))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(
            respuesta["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.assertIn("documentos_trabajadores.xlsx", respuesta["Content-Disposition"])
        hoja = load_workbook(io.BytesIO(respuesta.content)).active
        filas = list(hoja.iter_rows(values_only=True))
        self.assertEqual(filas[0][:4], ("Cédula", "Trabajador", "Empresa", "Documento"))
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[1][0], "0102030405")

        exportacion = ExportacionAuditoria.objects.get()
        self.assertEqual((exportacion.tipo, exportacion.formato, exportacion.filas), ("DOCUMENTOS_TRABAJADORES", "XLSX", 1))
        self.assertEqual(exportacion.actor, self.admin)
        self.assertEqual(exportacion.organizacion, self.organizacion)
        self.assertEqual(exportacion.filtros["empresa"], str(self.andinos.pk))
        self.assertEqual(len(exportacion.hash_sha256), 64)
        self.assertTrue(EventoAuditoria.objects.filter(accion="EXPORTAR_DOCUMENTOS_TRABAJADORES").exists())

    def test_excel_de_informacion_no_incluye_otra_organizacion(self):
        hoja = load_workbook(io.BytesIO(self._get("informacion_trabajadores_excel").content)).active
        cedulas = [fila[0] for fila in hoja.iter_rows(min_row=2, values_only=True)]

        self.assertEqual(sorted(cedulas), ["0102030405", "0911111111"])
        self.assertNotIn("Antecedentes", [celda.value for celda in hoja[1]])
