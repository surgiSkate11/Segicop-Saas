from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accesos.models import EstadoPermiso, EventoAcceso, TipoEvento
from apps.accesos.services import calcular_habilitacion, registrar_evento_acceso, solicitar_permiso_ingreso
from apps.antecedentes.models import EstadoVerificacion, ModoConector, ResultadoAntecedentes, VerificacionAntecedentes
from apps.core.choices import EstadoCumplimiento
from apps.core.exceptions import DomainError
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Instalacion, Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento


class HabilitacionTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Cliente principal")
        self.usuario = Usuario.objects.create_user(email="operador@example.com", nombre_completo="Operador de pruebas")
        self.instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Planta principal")
        self.otra_instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Planta secundaria")
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Contratista de pruebas",
            ruc="1790000000001",
            estado=EstadoCumplimiento.HABILITADO,
        )
        self.persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="0102030405",
            nombres="Ana",
            apellidos="Pruebas",
            estado=EstadoCumplimiento.HABILITADO,
        )
        Asignacion.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            empresa=self.empresa,
            cargo_o_actividad="Técnica",
            fecha_inicio=date.today(),
        )

    def crear_verificacion(self, *, resultado=ResultadoAntecedentes.NO, vigente_hasta=None):
        return VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            estado=EstadoVerificacion.VALIDADA,
            modo_conector=ModoConector.MANUAL,
            resultado=resultado,
            vigente_hasta=vigente_hasta or date.today() + timedelta(days=30),
            validado_por=self.usuario,
        )

    def test_habilita_persona_con_documentacion_empresa_y_antecedentes_validos(self):
        self.crear_verificacion()

        resultado = calcular_habilitacion(self.persona)

        self.assertTrue(resultado.habilitado)
        self.assertIn("Cumple", resultado.motivo)

    # S8: garita, solicitantes y trabajos no deben poder deducir el resultado de antecedentes por el motivo.
    MOTIVO_ANTECEDENTES = (
        "Antecedentes penales sin habilitación vigente; el área de antecedentes debe revisarlo."
    )

    def test_rechaza_persona_sin_verificacion_validada(self):
        resultado = calcular_habilitacion(self.persona)

        self.assertEqual((resultado.habilitado, resultado.motivo), (False, self.MOTIVO_ANTECEDENTES))

    def test_rechaza_resultado_de_antecedentes_con_hallazgos_sin_revelarlo(self):
        self.crear_verificacion(resultado=ResultadoAntecedentes.SI)

        resultado = calcular_habilitacion(self.persona)

        self.assertEqual((resultado.habilitado, resultado.motivo), (False, self.MOTIVO_ANTECEDENTES))

    def test_rechaza_verificacion_vencida(self):
        self.crear_verificacion(vigente_hasta=date.today() - timedelta(days=1))

        resultado = calcular_habilitacion(self.persona)

        self.assertEqual((resultado.habilitado, resultado.motivo), (False, self.MOTIVO_ANTECEDENTES))

    def test_una_autorizacion_revocada_deja_de_habilitar_sin_borrar_la_verificacion(self):
        from apps.antecedentes.models import AutorizacionDatos, MedioAutorizacion

        autorizacion = AutorizacionDatos.objects.create(
            organizacion=self.organizacion, persona=self.persona, finalidad="HABILITACION_INGRESO",
            texto="Autorizo.", version="1.0", medio=MedioAutorizacion.PORTAL, otorgada_en=date.today(),
            registrada_por=self.usuario,
        )
        verificacion = self.crear_verificacion()
        verificacion.autorizacion = autorizacion
        verificacion.save(update_fields=["autorizacion"])
        self.assertTrue(calcular_habilitacion(self.persona).habilitado)

        autorizacion.revocada_en = timezone.now()
        autorizacion.save(update_fields=["revocada_en"])

        resultado = calcular_habilitacion(self.persona)
        self.assertEqual((resultado.habilitado, resultado.motivo), (False, self.MOTIVO_ANTECEDENTES))
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, EstadoVerificacion.VALIDADA)

    def test_una_renovacion_en_curso_no_quita_la_habilitacion_vigente(self):
        self.crear_verificacion()
        VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion, persona=self.persona, estado=EstadoVerificacion.REQUIERE_REVISION,
            modo_conector=ModoConector.MANUAL, resultado=ResultadoAntecedentes.SI, finalidad="RENOVACION",
        )

        self.assertTrue(calcular_habilitacion(self.persona).habilitado)

    def test_el_permiso_rechazado_no_revela_el_resultado(self):
        self.crear_verificacion(resultado=ResultadoAntecedentes.SI)

        permiso = solicitar_permiso_ingreso(
            persona=self.persona, instalacion=self.instalacion, actor=self.usuario,
            fecha_inicio_vigencia=date.today(), fecha_fin_vigencia=date.today() + timedelta(days=1),
        )

        self.assertEqual(permiso.motivo, self.MOTIVO_ANTECEDENTES)
        self.assertNotIn("hallazgos", permiso.motivo)

    def test_solicitar_permiso_guarda_resultado_de_habilitacion(self):
        self.crear_verificacion()

        permiso = solicitar_permiso_ingreso(
            persona=self.persona,
            instalacion=self.instalacion,
            actor=self.usuario,
            fecha_inicio_vigencia=date.today(),
            fecha_fin_vigencia=date.today() + timedelta(days=1),
        )

        self.assertEqual(permiso.estado, EstadoPermiso.APROBADO)
        self.assertEqual(permiso.persona_id, self.persona.id)

    def test_calcular_habilitacion_no_modifica_entidades(self):
        self.crear_verificacion()
        estado_persona = self.persona.estado
        estado_empresa = self.empresa.estado

        calcular_habilitacion(self.persona)

        self.persona.refresh_from_db()
        self.empresa.refresh_from_db()
        self.assertEqual(self.persona.estado, estado_persona)
        self.assertEqual(self.empresa.estado, estado_empresa)


class GaritaTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Cliente de garita")
        rol = Rol.objects.create(codigo="operador-garita", nombre="Operador de garita")
        self.usuario = Usuario.objects.create_user(email="garita@example.com", nombre_completo="Operador de garita")
        RolAsignado.objects.create(usuario=self.usuario, organizacion=self.organizacion, rol=rol)
        self.instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Garita norte")
        self.otra_instalacion = Instalacion.objects.create(organizacion=self.organizacion, nombre="Garita sur")
        empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Empresa garita",
            ruc="1790000000002",
            estado=EstadoCumplimiento.HABILITADO,
        )
        persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="0607080910",
            nombres="Carlos",
            apellidos="Garita",
            estado=EstadoCumplimiento.HABILITADO,
        )
        Asignacion.objects.create(
            organizacion=self.organizacion,
            persona=persona,
            empresa=empresa,
            cargo_o_actividad="Guardia",
            fecha_inicio=date.today(),
        )
        VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=persona,
            estado=EstadoVerificacion.VALIDADA,
            modo_conector=ModoConector.MANUAL,
            resultado=ResultadoAntecedentes.NO,
            vigente_hasta=date.today() + timedelta(days=30),
            validado_por=self.usuario,
        )
        self.permiso = solicitar_permiso_ingreso(
            persona=persona,
            instalacion=self.instalacion,
            actor=self.usuario,
            fecha_inicio_vigencia=date.today(),
            fecha_fin_vigencia=date.today() + timedelta(days=1),
        )

    def test_registra_entrada_en_instalacion_correcta(self):
        evento = registrar_evento_acceso(
            codigo=self.permiso.codigo,
            instalacion=self.instalacion,
            tipo=TipoEvento.ENTRADA,
            actor=self.usuario,
        )

        self.assertEqual(evento.tipo, TipoEvento.ENTRADA)
        self.assertEqual(EventoAcceso.objects.count(), 1)

    def test_registra_denegacion_si_se_usa_en_otra_instalacion(self):
        evento = registrar_evento_acceso(
            codigo=self.permiso.codigo,
            instalacion=self.otra_instalacion,
            tipo=TipoEvento.ENTRADA,
            actor=self.usuario,
        )

        self.assertEqual(evento.tipo, TipoEvento.DENEGADO)
        self.assertIn("instalación", evento.motivo_denegacion)

    def test_rechaza_operador_de_otra_organizacion(self):
        otra_organizacion = Organizacion.objects.create(nombre="Otro cliente")
        otro_rol = Rol.objects.create(codigo="otra-garita", nombre="Otra garita")
        otro_usuario = Usuario.objects.create_user(email="otra-garita@example.com", nombre_completo="Otra garita")
        RolAsignado.objects.create(usuario=otro_usuario, organizacion=otra_organizacion, rol=otro_rol)

        with self.assertRaisesMessage(DomainError, "no tiene acceso a esta organización"):
            registrar_evento_acceso(
                codigo=self.permiso.codigo,
                instalacion=self.instalacion,
                tipo=TipoEvento.ENTRADA,
                actor=otro_usuario,
            )
