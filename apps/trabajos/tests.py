from datetime import date, timedelta

from django.test import TestCase

from apps.antecedentes.models import EstadoVerificacion, ModoConector, ResultadoAntecedentes, VerificacionAntecedentes
from apps.core.choices import EstadoCumplimiento
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Instalacion, Organizacion, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento
from apps.trabajos.models import EstadoTrabajo, NivelRiesgo
from apps.trabajos.services import abrir_permiso_trabajo, crear_permiso_trabajo


class TrabajosTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Organización de trabajos")
        self.usuario = Usuario.objects.create_user(
            email="supervisor@example.com", nombre_completo="Supervisor de trabajos"
        )
        self.instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Instalación de trabajo")
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Empresa de trabajo",
            ruc="1790000000004",
            estado=EstadoCumplimiento.HABILITADO,
        )
        self.persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="1212121212",
            nombres="Trabajador",
            apellidos="Habilitado",
            estado=EstadoCumplimiento.HABILITADO,
        )
        Asignacion.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            empresa=self.empresa,
            cargo_o_actividad="Técnico",
            fecha_inicio=date.today(),
        )

    def crear_antecedentes(self, resultado=ResultadoAntecedentes.NO):
        return VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            estado=EstadoVerificacion.VALIDADA,
            modo_conector=ModoConector.MANUAL,
            resultado=resultado,
            vigente_hasta=date.today() + timedelta(days=30),
            validado_por=self.usuario,
        )

    def crear_permiso(self):
        return crear_permiso_trabajo(
            empresa=self.empresa,
            instalacion=self.instalacion,
            actividad="Mantenimiento preventivo",
            nivel_riesgo=NivelRiesgo.MEDIO,
            integrantes=[self.persona],
            actor=self.usuario,
            fecha_inicio_programada=date.today(),
            fecha_fin_programada=date.today() + timedelta(days=1),
        )

    def test_rechaza_permiso_si_la_cuadrilla_no_esta_habilitada(self):
        permiso = self.crear_permiso()

        self.assertEqual(permiso.estado, EstadoTrabajo.RECHAZADO)
        self.assertIn("antecedentes", permiso.motivo)

    def test_abre_permiso_si_la_cuadrilla_esta_habilitada(self):
        self.crear_antecedentes()
        permiso = self.crear_permiso()

        abrir_permiso_trabajo(permiso=permiso, actor=self.usuario)

        permiso.refresh_from_db()
        self.assertEqual(permiso.estado, EstadoTrabajo.ACTIVO)
        self.assertEqual(permiso.aprobado_por_id, self.usuario.id)
