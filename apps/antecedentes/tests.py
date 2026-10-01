from datetime import date, timedelta

from django.test import TestCase

from apps.antecedentes import maquina_estados, services
from apps.antecedentes.models import (
    EstadoVerificacion,
    EjecucionConector,
    ModoConector,
    ResultadoAntecedentes,
    VerificacionAntecedentes,
)
from apps.core.exceptions import DomainError, TransicionEstadoInvalida
from apps.identidad.models import Organizacion, Usuario
from apps.personas.models import Persona, TipoDocumento


class AntecedentesTests(TestCase):
    def setUp(self):
        self.organizacion = Organizacion.objects.create(nombre="Organización de antecedentes")
        self.usuario = Usuario.objects.create_user(
            email="revisor@example.com", nombre_completo="Revisor de antecedentes"
        )
        self.persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="1111111111",
            nombres="Persona",
            apellidos="Evaluada",
        )
        self.verificacion = VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            modo_conector=ModoConector.MANUAL,
        )

    def test_transicionar_registra_bitacora(self):
        maquina_estados.transicionar(
            verificacion=self.verificacion,
            nuevo_estado=EstadoVerificacion.CONTINGENCIA_MANUAL,
            actor=self.usuario,
            nota_bitacora="Conector manual seleccionado.",
        )

        self.verificacion.refresh_from_db()
        ejecucion = EjecucionConector.objects.get(verificacion=self.verificacion)
        self.assertEqual(self.verificacion.estado, EstadoVerificacion.CONTINGENCIA_MANUAL)
        self.assertEqual(ejecucion.estado_resultante, EstadoVerificacion.CONTINGENCIA_MANUAL)

    def test_transicion_invalida_lanza_error(self):
        with self.assertRaises(TransicionEstadoInvalida):
            maquina_estados.transicionar(
                verificacion=self.verificacion,
                nuevo_estado=EstadoVerificacion.VALIDADA,
                actor=self.usuario,
            )

    def test_registrar_resultado_manual_deja_verificacion_en_revision(self):
        maquina_estados.transicionar(
            verificacion=self.verificacion,
            nuevo_estado=EstadoVerificacion.CONTINGENCIA_MANUAL,
            actor=self.usuario,
        )

        services.registrar_resultado_manual(
            verificacion=self.verificacion,
            actor=self.usuario,
            resultado=ResultadoAntecedentes.NO,
            numero_certificado="CERT-001",
        )

        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, EstadoVerificacion.EN_REVISION_MANUAL)
        self.assertEqual(self.verificacion.resultado, ResultadoAntecedentes.NO)

    def test_validar_requiere_vigencia_para_decision_favorable(self):
        maquina_estados.transicionar(
            verificacion=self.verificacion,
            nuevo_estado=EstadoVerificacion.CONTINGENCIA_MANUAL,
            actor=self.usuario,
        )
        services.registrar_resultado_manual(
            verificacion=self.verificacion,
            actor=self.usuario,
            resultado=ResultadoAntecedentes.NO,
        )

        with self.assertRaises(DomainError):
            services.validar_verificacion(
                verificacion=self.verificacion,
                revisor=self.usuario,
                decision=EstadoVerificacion.VALIDADA,
            )

    def test_validar_guarda_resultado_y_vigencia(self):
        maquina_estados.transicionar(
            verificacion=self.verificacion,
            nuevo_estado=EstadoVerificacion.CONTINGENCIA_MANUAL,
            actor=self.usuario,
        )
        services.registrar_resultado_manual(
            verificacion=self.verificacion,
            actor=self.usuario,
            resultado=ResultadoAntecedentes.NO,
        )
        vigencia = date.today() + timedelta(days=30)

        services.validar_verificacion(
            verificacion=self.verificacion,
            revisor=self.usuario,
            decision=EstadoVerificacion.VALIDADA,
            vigente_hasta=vigencia,
        )

        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, EstadoVerificacion.VALIDADA)
        self.assertEqual(self.verificacion.vigente_hasta, vigencia)
