import hashlib
import importlib
import json
import re
import os
import shutil
import tempfile
import urllib.error
from email.message import Message
from datetime import date, datetime, timedelta
from itertools import count, product
from unittest import mock

from django.apps import apps as django_apps
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accesos.services import calcular_habilitacion
from apps.antecedentes import certificado, maquina_estados, services
from apps.antecedentes.pdf import MAX_DOCUMENT_BYTES, validar_pdf_seguro
from apps.antecedentes.models import (
    AutorizacionDatos,
    ConsultaProveedor,
    DocumentoAntecedentes,
    EjecucionConector,
    EstadoConsulta,
    EstadoVerificacion,
    FinalidadConsulta,
    IntentoConsulta,
    MedioAutorizacion,
    ModoConector,
    ProveedorAntecedentes,
    ResultadoAntecedentes,
    VerificacionAntecedentes,
)
from apps.antecedentes.conectores.asistido import ConectorAsistido
from apps.antecedentes.conectores.base import ClaseRespuesta, ResultadoSondeo
from apps.antecedentes.conectores.ministerio import PORTAL_URL, sondear_portal
from apps.antecedentes.models import SaludFuente
from apps.antecedentes.tasks import ejecutar_consulta_asistida
from apps.core.choices import EstadoCumplimiento
from apps.core.exceptions import DomainError, TransicionEstadoInvalida
from apps.core.models import EventoAuditoria
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento

E = EstadoVerificacion
F = FinalidadConsulta
_numeros = count(9000000000)

# Tabla esperada (S5 + extensiones documentadas); se fija aquí para detectar cambios accidentales.
TRANSICIONES_ESPERADAS = {
    E.CREADA: {E.AUTORIZACION_PENDIENTE, E.LISTA, E.CANCELADA},
    E.AUTORIZACION_PENDIENTE: {E.LISTA, E.CANCELADA},
    E.LISTA: {E.EN_CONSULTA, E.PDF_RECIBIDO, E.CANCELADA},
    E.EN_CONSULTA: {E.CAPTCHA_REQUERIDO, E.RESULTADO_OBTENIDO, E.FALLIDA, E.PDF_RECIBIDO, E.CANCELADA},
    E.CAPTCHA_REQUERIDO: {E.EN_CONSULTA, E.PDF_RECIBIDO, E.CANCELADA},
    E.RESULTADO_OBTENIDO: {E.PDF_RECIBIDO, E.CANCELADA},
    E.PDF_RECIBIDO: {E.DATOS_COINCIDENTES, E.REQUIERE_REVISION, E.CANCELADA},
    E.DATOS_COINCIDENTES: {E.AUTENTICIDAD_PENDIENTE, E.CANCELADA},
    E.AUTENTICIDAD_PENDIENTE: {E.VALIDADA, E.REQUIERE_REVISION, E.CANCELADA},
    E.REQUIERE_REVISION: {E.VALIDADA, E.LISTA, E.CANCELADA},
    E.FALLIDA: {E.LISTA, E.PDF_RECIBIDO, E.CANCELADA},
    E.VALIDADA: {E.VENCIDA},
    E.VENCIDA: set(),
    E.CANCELADA: set(),
}


DISPONIBLE = ResultadoSondeo(ClaseRespuesta.DISPONIBLE, codigo_http=200, latencia_ms=120, evidencia="HTTP 200 · 120 ms")


def caido(codigo=503):
    return ResultadoSondeo(ClaseRespuesta.TRANSITORIA, codigo_http=codigo, latencia_ms=80, evidencia=f"HTTP {codigo} · 80 ms")


# El modo por defecto del proyecto es asistido; las pruebas fijan manual y cada una declara el modo que prueba.
@override_settings(ANTECEDENTES_CONECTOR_MODE="manual")
class BaseAntecedentesTests(TestCase):
    def setUp(self):
        # Ninguna prueba sale a la red: el portal "responde" salvo que la prueba diga otra cosa.
        self.sondeo = mock.patch(
            "apps.antecedentes.conectores.asistido.sondear_portal", return_value=DISPONIBLE
        ).start()
        self.esperar = mock.patch.object(services, "_esperar").start()
        self.addCleanup(mock.patch.stopall)
        self.organizacion = Organizacion.objects.create(nombre="Organización de antecedentes")
        self.usuario = Usuario.objects.create_user(
            email="revisor@example.com", nombre_completo="Revisor de antecedentes"
        )
        self.dar_rol(self.usuario, "revisor_antecedentes")
        self.persona = Persona.objects.create(
            organizacion=self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento="1111111111",
            nombres="Persona",
            apellidos="Evaluada",
        )

    def dar_rol(self, usuario, codigo, organizacion=None):
        rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": codigo})
        return RolAsignado.objects.create(usuario=usuario, organizacion=organizacion or self.organizacion, rol=rol)

    def crear_usuario(self, email, codigo=None, organizacion=None):
        usuario = Usuario.objects.create_user(email=email, nombre_completo=email)
        if codigo:
            self.dar_rol(usuario, codigo, organizacion)
        return usuario

    def crear_persona(self, organizacion=None):
        return Persona.objects.create(
            organizacion=organizacion or self.organizacion,
            tipo_documento=TipoDocumento.CEDULA,
            numero_documento=str(next(_numeros)),
            nombres="Otra",
            apellidos="Persona",
        )

    def crear_verificacion(self, estado=E.CREADA, persona=None, **campos):
        # Una persona nueva por defecto: solo puede haber una solicitud activa por persona y finalidad.
        persona = persona or self.crear_persona()
        campos.setdefault("modo_conector", ModoConector.MANUAL)
        return VerificacionAntecedentes.objects.create(
            organizacion=persona.organizacion, persona=persona, estado=estado, **campos
        )

    def autorizar(self, persona=None, finalidad=F.HABILITACION_INGRESO):
        return services.registrar_autorizacion(
            persona=persona or self.persona,
            finalidad=finalidad,
            texto="Autorizo la consulta de mis antecedentes penales.",
            version="1.0",
            medio=MedioAutorizacion.PORTAL,
            referencia_evidencia="",
            otorgada_en=date.today(),
            actor=self.usuario,
        )

    def iniciar(self, modo="manual", persona=None, **kwargs):
        with override_settings(ANTECEDENTES_CONECTOR_MODE=modo):
            return services.iniciar_verificacion(persona=persona or self.persona, actor=self.usuario, **kwargs)


class MaquinaEstadosTests(BaseAntecedentesTests):
    def test_estados_son_exactamente_los_de_la_especificacion(self):
        self.assertEqual(
            list(E.values),
            [
                "CREADA", "AUTORIZACION_PENDIENTE", "LISTA", "EN_CONSULTA", "CAPTCHA_REQUERIDO",
                "RESULTADO_OBTENIDO", "PDF_RECIBIDO", "DATOS_COINCIDENTES", "AUTENTICIDAD_PENDIENTE",
                "VALIDADA", "REQUIERE_REVISION", "FALLIDA", "VENCIDA", "CANCELADA",
            ],
        )

    def test_verificacion_nueva_nace_creada(self):
        verificacion = VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion, persona=self.persona, modo_conector=ModoConector.MANUAL
        )

        self.assertEqual(verificacion.estado, E.CREADA)

    def test_tabla_de_transiciones_coincide_con_la_esperada(self):
        self.assertEqual({k: set(v) for k, v in maquina_estados.TRANSICIONES.items()}, TRANSICIONES_ESPERADAS)

    def test_toda_transicion_permitida_funciona_y_deja_rastro(self):
        for origen, destinos in TRANSICIONES_ESPERADAS.items():
            for destino in destinos:
                with self.subTest(origen=origen, destino=destino):
                    verificacion = self.crear_verificacion(estado=origen)
                    eventos_antes = EventoAuditoria.objects.filter(accion="TRANSICION_ANTECEDENTES").count()
                    nota = "Nota de prueba."

                    maquina_estados.transicionar(
                        verificacion=verificacion,
                        nuevo_estado=destino,
                        actor=self.usuario,
                        nota_bitacora=nota,
                        motivo="Motivo de prueba." if destino == E.CANCELADA else "",
                    )

                    verificacion.refresh_from_db()
                    self.assertEqual(verificacion.estado, destino)
                    ejecucion = EjecucionConector.objects.get(verificacion=verificacion)
                    self.assertEqual(ejecucion.estado_resultante, destino)
                    self.assertEqual(ejecucion.exito, destino != E.FALLIDA)
                    self.assertIn(nota, ejecucion.detalle)
                    eventos = EventoAuditoria.objects.filter(accion="TRANSICION_ANTECEDENTES")
                    self.assertEqual(eventos.count(), eventos_antes + 1)
                    self.assertIn(f"{origen} -> {destino}", eventos.latest("created_at").motivo)

    def test_toda_transicion_no_permitida_se_rechaza_sin_cambios(self):
        for origen, destino in product(E.values, E.values):
            if destino in TRANSICIONES_ESPERADAS[origen]:
                continue
            with self.subTest(origen=origen, destino=destino):
                verificacion = self.crear_verificacion(estado=origen)
                eventos_antes = EventoAuditoria.objects.count()

                with self.assertRaises(TransicionEstadoInvalida):
                    maquina_estados.transicionar(
                        verificacion=verificacion, nuevo_estado=destino, actor=self.usuario, motivo="Motivo."
                    )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, origen)
                self.assertEqual(EjecucionConector.objects.filter(verificacion=verificacion).count(), 0)
                self.assertEqual(EventoAuditoria.objects.count(), eventos_antes)

    def test_estados_terminales(self):
        self.assertEqual(maquina_estados.TRANSICIONES[E.VALIDADA], {E.VENCIDA})
        self.assertEqual(maquina_estados.TRANSICIONES[E.VENCIDA], set())
        self.assertEqual(maquina_estados.TRANSICIONES[E.CANCELADA], set())

    def test_anular_sin_motivo_se_rechaza_sin_cambios(self):
        for motivo in ("", "   "):
            with self.subTest(motivo=motivo):
                verificacion = self.crear_verificacion(estado=E.LISTA)

                with self.assertRaises(DomainError):
                    maquina_estados.transicionar(
                        verificacion=verificacion, nuevo_estado=E.CANCELADA, actor=self.usuario, motivo=motivo
                    )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.LISTA)
                self.assertEqual(EjecucionConector.objects.filter(verificacion=verificacion).count(), 0)
                verificacion.delete()

    def test_anular_con_motivo_lo_deja_en_la_bitacora(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)

        maquina_estados.transicionar(
            verificacion=verificacion, nuevo_estado=E.CANCELADA, actor=self.usuario, motivo="Persona desistió."
        )

        ejecucion = EjecucionConector.objects.get(verificacion=verificacion)
        self.assertEqual(ejecucion.detalle, "La solicitud fue anulada con motivo: Persona desistió.")

    def test_transicionar_aplica_campos_del_modelo(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)

        maquina_estados.transicionar(
            verificacion=verificacion, nuevo_estado=E.PDF_RECIBIDO, actor=self.usuario, cargado_por=self.usuario
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.cargado_por, self.usuario)


class ConsultaProveedorTests(BaseAntecedentesTests):
    def test_estados_tecnicos_de_consulta(self):
        self.assertEqual(
            list(EstadoConsulta.values),
            ["PENDIENTE", "EN_CURSO", "CAPTCHA_REQUERIDO", "REINTENTO_PROGRAMADO", "COMPLETADA", "FALLIDA"],
        )

    def test_consulta_nueva_nace_pendiente(self):
        consulta = ConsultaProveedor.objects.create(
            verificacion=self.crear_verificacion(), proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        self.assertEqual(consulta.estado, EstadoConsulta.PENDIENTE)

    def test_cambiar_estado_consulta_registra_intento(self):
        consulta = ConsultaProveedor.objects.create(
            verificacion=self.crear_verificacion(), proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        services.cambiar_estado_consulta(
            consulta=consulta,
            nuevo_estado=EstadoConsulta.CAPTCHA_REQUERIDO,
            registrar_intento=True,
            detalle_error="Se requiere acción humana.",
        )

        consulta.refresh_from_db()
        self.assertEqual(consulta.estado, EstadoConsulta.CAPTCHA_REQUERIDO)
        self.assertEqual(consulta.intentos, 1)
        self.assertTrue(consulta.requiere_intervencion)
        self.assertIsNotNone(consulta.ultimo_intento_at)
        intento = IntentoConsulta.objects.get(consulta=consulta)
        self.assertEqual((intento.numero, intento.estado_resultante), (1, EstadoConsulta.CAPTCHA_REQUERIDO))

    def test_cambiar_estado_consulta_sin_intento_no_toca_la_bitacora_tecnica(self):
        consulta = ConsultaProveedor.objects.create(
            verificacion=self.crear_verificacion(), proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        services.cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.COMPLETADA)

        consulta.refresh_from_db()
        self.assertEqual((consulta.estado, consulta.intentos, consulta.requiere_intervencion), ("COMPLETADA", 0, False))
        self.assertEqual(IntentoConsulta.objects.count(), 0)

    def test_cambiar_estado_consulta_rechaza_estado_desconocido(self):
        consulta = ConsultaProveedor.objects.create(
            verificacion=self.crear_verificacion(), proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        with self.assertRaises(DomainError):
            services.cambiar_estado_consulta(consulta=consulta, nuevo_estado="EN_COLA")

        consulta.refresh_from_db()
        self.assertEqual(consulta.estado, EstadoConsulta.PENDIENTE)

    def test_tarea_asistida_pasa_por_el_servicio_y_es_idempotente(self):
        verificacion = self.crear_verificacion(estado=E.EN_CONSULTA, modo_conector=ModoConector.ASISTIDO)
        consulta = ConsultaProveedor.objects.create(
            verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        primera = ejecutar_consulta_asistida(str(consulta.pk))
        segunda = ejecutar_consulta_asistida(str(consulta.pk))

        consulta.refresh_from_db()
        verificacion.refresh_from_db()
        self.assertEqual((verificacion.estado, consulta.estado), (E.CAPTCHA_REQUERIDO, EstadoConsulta.CAPTCHA_REQUERIDO))
        self.assertEqual(consulta.intentos, 1)
        self.assertEqual(IntentoConsulta.objects.filter(consulta=consulta).count(), 1)
        self.assertEqual(self.sondeo.call_count, 1)
        self.assertEqual((primera["omitida"], segunda["omitida"]), (False, True))


class IniciarVerificacionTests(BaseAntecedentesTests):
    def test_modo_manual_deja_la_verificacion_lista_con_una_consulta(self):
        self.autorizar()

        verificacion, creada = self.iniciar("manual")

        verificacion.refresh_from_db()
        self.assertTrue(creada)
        self.assertEqual(verificacion.estado, E.LISTA)
        self.assertEqual(verificacion.modo_conector, ModoConector.MANUAL)
        self.assertEqual(
            list(verificacion.consultas_proveedor.values_list("proveedor", flat=True)),
            [ProveedorAntecedentes.MINISTERIO_INTERIOR],
        )
        self.assertEqual(
            list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
            [E.LISTA],
        )

    def test_modo_manual_no_encola_consulta_asistida(self):
        self.autorizar()

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.iniciar("manual")

        self.assertEqual(callbacks, [])

    def test_modo_asistido_llega_a_captcha_requerido(self):
        self.autorizar()

        verificacion, _ = self.iniciar("asistido")

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(
            list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
            [E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO],
        )

    def test_modo_asistido_aparece_en_la_bandeja(self):
        rol = Rol.objects.create(codigo="revisor-prueba", nombre="Revisor de prueba")
        RolAsignado.objects.create(usuario=self.usuario, organizacion=self.organizacion, rol=rol)
        self.autorizar()
        verificacion, _ = self.iniciar("asistido")
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual([v.pk for v in respuesta.context["verificaciones"]], [verificacion.pk])
        self.assertContains(respuesta, "Captcha requerido")

    def test_bandeja_lista_solo_estados_que_piden_accion_humana(self):
        rol = Rol.objects.create(codigo="revisor-prueba", nombre="Revisor de prueba")
        RolAsignado.objects.create(usuario=self.usuario, organizacion=self.organizacion, rol=rol)
        for estado in E.values:
            self.crear_verificacion(estado=estado)
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        self.assertEqual(
            {v.estado for v in respuesta.context["verificaciones"]},
            {
                E.AUTORIZACION_PENDIENTE, E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.PDF_RECIBIDO,
                E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE, E.FALLIDA,
            },
        )


class FlujoManualTests(BaseAntecedentesTests):
    def iniciar_manual(self):
        self.autorizar()
        return self.iniciar("manual")[0]

    def test_registrar_resultado_manual_pasa_por_pdf_recibido_y_queda_en_revision(self):
        verificacion = self.iniciar_manual()

        services.registrar_resultado_manual(
            verificacion=verificacion,
            actor=self.usuario,
            resultado=ResultadoAntecedentes.NO,
            numero_certificado="CERT-001",
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertEqual(verificacion.resultado, ResultadoAntecedentes.NO)
        self.assertEqual(verificacion.numero_certificado, "CERT-001")
        self.assertEqual(verificacion.cargado_por, self.usuario)
        self.assertEqual(
            list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
            [E.LISTA, E.PDF_RECIBIDO, E.REQUIERE_REVISION],
        )

    def test_registrar_resultado_manual_esta_disponible_tras_fallo_o_captcha(self):
        for estado in (E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.FALLIDA):
            with self.subTest(estado=estado):
                verificacion = self.crear_verificacion(estado=estado)

                services.registrar_resultado_manual(
                    verificacion=verificacion, actor=self.usuario, resultado=ResultadoAntecedentes.NO
                )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)

    def test_registrar_resultado_manual_rechaza_resultado_invalido_o_estado_incorrecto(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)
        with self.assertRaises(DomainError):
            services.registrar_resultado_manual(verificacion=verificacion, actor=self.usuario, resultado="TAL_VEZ")
        validada = self.crear_verificacion(estado=E.VALIDADA)
        with self.assertRaises(TransicionEstadoInvalida):
            services.registrar_resultado_manual(
                verificacion=validada, actor=self.usuario, resultado=ResultadoAntecedentes.NO
            )

        verificacion.refresh_from_db()
        validada.refresh_from_db()
        self.assertEqual((verificacion.estado, validada.estado), (E.LISTA, E.VALIDADA))
        self.assertEqual(EjecucionConector.objects.count(), 0)

    def test_validar_requiere_vigencia_para_decision_favorable(self):
        verificacion = self.crear_verificacion(estado=E.REQUIERE_REVISION, resultado=ResultadoAntecedentes.NO)

        with self.assertRaises(DomainError):
            services.validar_verificacion(verificacion=verificacion, revisor=self.usuario, decision=E.VALIDADA)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)

    def test_validar_desde_revision_o_autenticidad_pendiente(self):
        vigencia = date.today() + timedelta(days=30)
        for origen in (E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE):
            with self.subTest(origen=origen):
                verificacion = self.crear_verificacion(estado=origen, resultado=ResultadoAntecedentes.NO)

                services.validar_verificacion(
                    verificacion=verificacion, revisor=self.usuario, decision=E.VALIDADA, vigente_hasta=vigencia
                )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.VALIDADA)
                self.assertEqual(verificacion.vigente_hasta, vigencia)
                self.assertEqual(verificacion.validado_por, self.usuario)

    def test_validar_rechaza_estado_que_no_espera_decision(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)

        with self.assertRaises(DomainError):
            services.validar_verificacion(
                verificacion=verificacion,
                revisor=self.usuario,
                decision=E.VALIDADA,
                vigente_hasta=date.today() + timedelta(days=1),
            )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.LISTA)

    def test_devolver_a_lista_o_anular_exigen_comentario(self):
        for decision in (E.LISTA, E.CANCELADA):
            with self.subTest(decision=decision):
                verificacion = self.crear_verificacion(estado=E.REQUIERE_REVISION)

                with self.assertRaises(DomainError):
                    services.validar_verificacion(verificacion=verificacion, revisor=self.usuario, decision=decision)

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)

    def test_devolver_a_lista_o_anular_con_comentario(self):
        for decision in (E.LISTA, E.CANCELADA):
            with self.subTest(decision=decision):
                verificacion = self.crear_verificacion(estado=E.REQUIERE_REVISION)

                services.validar_verificacion(
                    verificacion=verificacion, revisor=self.usuario, decision=decision, comentario="Certificado ilegible."
                )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, decision)
                self.assertEqual(verificacion.comentario, "Certificado ilegible.")

    def test_decision_invalida_se_rechaza(self):
        verificacion = self.crear_verificacion(estado=E.REQUIERE_REVISION)

        with self.assertRaises(DomainError):
            services.validar_verificacion(
                verificacion=verificacion, revisor=self.usuario, decision="OBSERVADA", comentario="x"
            )

    def test_marcar_vencidas(self):
        vencida = self.crear_verificacion(estado=E.VALIDADA, vigente_hasta=date.today() - timedelta(days=1))
        vigente = self.crear_verificacion(estado=E.VALIDADA, vigente_hasta=date.today() + timedelta(days=1))

        total = services.marcar_vencidas(actor=self.usuario)

        vencida.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual((total, vencida.estado, vigente.estado), (1, E.VENCIDA, E.VALIDADA))

    def test_flujo_manual_completo_habilita_con_resultado_no(self):
        empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion,
            razon_social="Contratista de pruebas",
            ruc="1790000000001",
            estado=EstadoCumplimiento.HABILITADO,
        )
        self.persona.estado = EstadoCumplimiento.HABILITADO
        self.persona.save()
        Asignacion.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            empresa=empresa,
            cargo_o_actividad="Técnica",
            fecha_inicio=date.today(),
        )
        self.assertFalse(calcular_habilitacion(self.persona).habilitado)

        verificacion = self.iniciar_manual()
        self.assertEqual(verificacion.estado, E.LISTA)
        services.registrar_resultado_manual(
            verificacion=verificacion, actor=self.usuario, resultado=ResultadoAntecedentes.NO
        )
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertFalse(calcular_habilitacion(self.persona).habilitado)
        services.validar_verificacion(
            verificacion=verificacion,
            revisor=self.crear_usuario("segundo@example.com", "revisor_antecedentes"),
            decision=E.VALIDADA,
            vigente_hasta=date.today() + timedelta(days=30),
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.VALIDADA)
        self.assertTrue(calcular_habilitacion(self.persona).habilitado)

    def test_detalle_muestra_acciones_segun_estado(self):
        self.client.force_login(self.usuario)
        esperado = {
            E.LISTA: ('value="cargar_resultado"', 'value="validar"'),
            E.CAPTCHA_REQUERIDO: ('value="cargar_resultado"', 'value="validar"'),
            E.FALLIDA: ('value="cargar_resultado"', 'value="validar"'),
            E.REQUIERE_REVISION: ('value="validar"', 'value="cargar_resultado"'),
            E.AUTENTICIDAD_PENDIENTE: ('value="validar"', 'value="cargar_resultado"'),
            E.VALIDADA: (None, 'value="cargar_resultado"'),
        }
        for estado, (presente, ausente) in esperado.items():
            with self.subTest(estado=estado):
                verificacion = self.crear_verificacion(estado=estado)

                respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

                self.assertEqual(respuesta.status_code, 200)
                if presente:
                    self.assertContains(respuesta, presente)
                self.assertNotContains(respuesta, ausente)


class AutorizacionGatingTests(BaseAntecedentesTests):
    def test_sin_autorizacion_queda_pendiente_y_el_conector_no_arranca(self):
        for modo in ("manual", "asistido"):
            with self.subTest(modo=modo):
                persona = self.crear_persona()

                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    verificacion, creada = self.iniciar(modo, persona=persona)

                verificacion.refresh_from_db()
                self.assertTrue(creada)
                self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)
                self.assertIsNone(verificacion.autorizacion)
                self.assertEqual(verificacion.finalidad, F.HABILITACION_INGRESO)
                self.assertEqual(verificacion.solicitante, self.usuario)
                self.assertEqual(callbacks, [])
                self.assertEqual(
                    list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
                    [E.AUTORIZACION_PENDIENTE],
                )
                consulta = verificacion.consultas_proveedor.get()
                self.assertEqual((consulta.estado, consulta.intentos), (EstadoConsulta.PENDIENTE, 0))

    def test_con_autorizacion_valida_avanza_segun_el_conector(self):
        autorizacion = self.autorizar()

        manual, _ = self.iniciar("manual", persona=self.persona)
        asistido_persona = self.crear_persona()
        self.autorizar(persona=asistido_persona)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            asistido, _ = self.iniciar("asistido", persona=asistido_persona)

        manual.refresh_from_db()
        asistido.refresh_from_db()
        self.assertEqual((manual.estado, manual.autorizacion), (E.LISTA, autorizacion))
        self.assertEqual(asistido.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(asistido.autorizacion.persona, asistido_persona)
        self.assertEqual(callbacks, [])  # síncrono por defecto: nada queda encolado
        self.assertEqual(self.sondeo.call_count, 1)

    def test_autorizacion_revocada_otra_organizacion_u_otra_finalidad_no_cuentan(self):
        otra_organizacion = Organizacion.objects.create(nombre="Otra organización")
        base = dict(
            texto="Texto", version="1.0", medio=MedioAutorizacion.PORTAL, otorgada_en=date.today(),
            registrada_por=self.usuario,
        )
        persona_revocada = self.crear_persona()
        persona_otra_org = self.crear_persona()
        persona_otra_finalidad = self.crear_persona()
        AutorizacionDatos.objects.create(
            organizacion=self.organizacion, persona=persona_revocada, finalidad=F.HABILITACION_INGRESO,
            revocada_en=timezone.now(), motivo_revocacion="Retiró su consentimiento.", **base,
        )
        AutorizacionDatos.objects.create(
            organizacion=otra_organizacion, persona=persona_otra_org, finalidad=F.HABILITACION_INGRESO, **base
        )
        AutorizacionDatos.objects.create(
            organizacion=self.organizacion, persona=persona_otra_finalidad, finalidad=F.RENOVACION, **base
        )

        for persona in (persona_revocada, persona_otra_org, persona_otra_finalidad):
            with self.subTest(persona=persona.numero_documento):
                verificacion, _ = self.iniciar("manual", persona=persona)

                self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)
                self.assertIsNone(verificacion.autorizacion)

    def test_finalidad_invalida_se_rechaza_sin_cambios(self):
        with self.assertRaises(DomainError):
            self.iniciar("manual", finalidad="OTRA")

        self.assertEqual(VerificacionAntecedentes.objects.count(), 0)


class RegistrarAutorizacionTests(BaseAntecedentesTests):
    def datos(self, **cambios):
        datos = dict(
            persona=self.persona, finalidad=F.HABILITACION_INGRESO, texto="Autorizo la consulta.", version="1.0",
            medio=MedioAutorizacion.PORTAL, referencia_evidencia="", otorgada_en=date.today(), actor=self.usuario,
        )
        datos.update(cambios)
        return datos

    def test_registra_con_todos_sus_datos_y_audita(self):
        autorizacion = services.registrar_autorizacion(
            **self.datos(medio=MedioAutorizacion.DOCUMENTO_FIRMADO, referencia_evidencia="Formulario F-12")
        )

        autorizacion.refresh_from_db()
        self.assertEqual(autorizacion.organizacion, self.organizacion)
        self.assertEqual(autorizacion.persona, self.persona)
        self.assertEqual(
            (autorizacion.finalidad, autorizacion.texto, autorizacion.version, autorizacion.medio),
            (F.HABILITACION_INGRESO, "Autorizo la consulta.", "1.0", MedioAutorizacion.DOCUMENTO_FIRMADO),
        )
        self.assertEqual(autorizacion.referencia_evidencia, "Formulario F-12")
        self.assertEqual((autorizacion.otorgada_en, autorizacion.registrada_por), (date.today(), self.usuario))
        self.assertIsNone(autorizacion.revocada_en)
        evento = EventoAuditoria.objects.get(accion="AUTORIZACION_REGISTRADA")
        self.assertEqual(
            (evento.recurso_id, evento.actor, evento.organizacion), (str(autorizacion.pk), self.usuario, self.organizacion)
        )

    def test_mueve_a_lista_las_pendientes_de_la_misma_persona_y_finalidad(self):
        pendiente, _ = self.iniciar("manual")
        renovacion, _ = self.iniciar("manual", finalidad=F.RENOVACION)
        otra_persona, _ = self.iniciar("manual", persona=self.crear_persona())

        autorizacion = self.autorizar()

        for v in (pendiente, renovacion, otra_persona):
            v.refresh_from_db()
        self.assertEqual((pendiente.estado, pendiente.autorizacion), (E.LISTA, autorizacion))
        self.assertEqual(
            list(pendiente.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
            [E.AUTORIZACION_PENDIENTE, E.LISTA],
        )
        self.assertEqual(renovacion.estado, E.AUTORIZACION_PENDIENTE)
        self.assertEqual(otra_persona.estado, E.AUTORIZACION_PENDIENTE)

    def test_en_modo_asistido_la_pendiente_continua_hasta_captcha(self):
        pendiente, _ = self.iniciar("asistido")

        with override_settings(ANTECEDENTES_CONECTOR_MODE="asistido"):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.autorizar()

        # Síncrona por defecto: el portal se comprueba en el acto, sin esperar a una cola.
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(pendiente.modo_conector, ModoConector.ASISTIDO)
        self.assertEqual(callbacks, [])
        self.assertEqual(self.sondeo.call_count, 1)

    def test_referencia_es_obligatoria_para_documento_firmado_y_otro(self):
        for medio in (MedioAutorizacion.DOCUMENTO_FIRMADO, MedioAutorizacion.OTRO):
            for referencia in ("", "   "):
                with self.subTest(medio=medio, referencia=referencia):
                    with self.assertRaises(DomainError):
                        services.registrar_autorizacion(**self.datos(medio=medio, referencia_evidencia=referencia))

        self.assertEqual(AutorizacionDatos.objects.count(), 0)
        self.assertEqual(EventoAuditoria.objects.filter(accion="AUTORIZACION_REGISTRADA").count(), 0)

    def test_portal_no_exige_referencia(self):
        autorizacion = services.registrar_autorizacion(**self.datos(medio=MedioAutorizacion.PORTAL))

        self.assertEqual(autorizacion.referencia_evidencia, "")

    def test_datos_invalidos_se_rechazan_sin_cambios(self):
        pendiente, _ = self.iniciar("manual")
        invalidos = [
            dict(texto="  "), dict(version=""), dict(finalidad="OTRA"), dict(medio="TELEPATIA"), dict(otorgada_en=None),
        ]
        for cambios in invalidos:
            with self.subTest(cambios=cambios):
                with self.assertRaises(DomainError):
                    services.registrar_autorizacion(**self.datos(**cambios))

        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, E.AUTORIZACION_PENDIENTE)
        self.assertEqual(AutorizacionDatos.objects.count(), 0)


class RevocarAutorizacionTests(BaseAntecedentesTests):
    def test_cancela_las_no_terminales_con_el_motivo_y_respeta_las_terminales(self):
        autorizacion = self.autorizar()
        vivas = {
            estado: self.crear_verificacion(estado=estado, autorizacion=autorizacion)
            for estado in (E.LISTA, E.CAPTCHA_REQUERIDO, E.REQUIERE_REVISION)
        }
        validada = self.crear_verificacion(estado=E.VALIDADA, autorizacion=autorizacion)
        ajena = self.crear_verificacion(estado=E.LISTA)

        services.revocar_autorizacion(autorizacion=autorizacion, motivo="Retiró su consentimiento.", actor=self.usuario)

        autorizacion.refresh_from_db()
        self.assertIsNotNone(autorizacion.revocada_en)
        self.assertEqual(autorizacion.motivo_revocacion, "Retiró su consentimiento.")
        for estado, verificacion in vivas.items():
            with self.subTest(estado=estado):
                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.CANCELADA)
                ultima = verificacion.ejecuciones.order_by("-created_at").first()
                self.assertIn("Autorización revocada: Retiró su consentimiento.", ultima.detalle)
        validada.refresh_from_db()
        ajena.refresh_from_db()
        self.assertEqual((validada.estado, ajena.estado), (E.VALIDADA, E.LISTA))
        evento = EventoAuditoria.objects.get(accion="AUTORIZACION_REVOCADA")
        self.assertEqual((evento.recurso_id, evento.actor), (str(autorizacion.pk), self.usuario))
        self.assertIn("Retiró su consentimiento.", evento.motivo)

    def test_tras_revocar_una_nueva_solicitud_vuelve_a_pedir_autorizacion(self):
        autorizacion = self.autorizar()
        services.revocar_autorizacion(autorizacion=autorizacion, motivo="Retiró su consentimiento.", actor=self.usuario)

        verificacion, _ = self.iniciar("manual")

        self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)

    def test_revocar_dos_veces_o_sin_motivo_se_rechaza_sin_cambios(self):
        autorizacion = self.autorizar()
        viva = self.crear_verificacion(estado=E.LISTA, autorizacion=autorizacion)
        eventos = EventoAuditoria.objects.count()
        for motivo in ("", "   "):
            with self.subTest(motivo=motivo):
                with self.assertRaises(DomainError):
                    services.revocar_autorizacion(autorizacion=autorizacion, motivo=motivo, actor=self.usuario)
        autorizacion.refresh_from_db()
        viva.refresh_from_db()
        self.assertIsNone(autorizacion.revocada_en)
        self.assertEqual(viva.estado, E.LISTA)
        self.assertEqual(EventoAuditoria.objects.count(), eventos)

        services.revocar_autorizacion(autorizacion=autorizacion, motivo="Primera vez.", actor=self.usuario)
        autorizacion.refresh_from_db()
        revocada_en = autorizacion.revocada_en
        eventos = EventoAuditoria.objects.count()

        with self.assertRaises(DomainError):
            services.revocar_autorizacion(autorizacion=autorizacion, motivo="Segunda vez.", actor=self.usuario)

        autorizacion.refresh_from_db()
        self.assertEqual((autorizacion.revocada_en, autorizacion.motivo_revocacion), (revocada_en, "Primera vez."))
        self.assertEqual(EventoAuditoria.objects.count(), eventos)


class IdempotenciaTests(BaseAntecedentesTests):
    def conteos(self):
        return (
            VerificacionAntecedentes.objects.count(),
            ConsultaProveedor.objects.count(),
            EjecucionConector.objects.count(),
            EventoAuditoria.objects.count(),
        )

    def test_se_genera_una_clave_unica_por_verificacion(self):
        primera, _ = self.iniciar("manual")
        segunda, _ = self.iniciar("manual", persona=self.crear_persona())

        self.assertRegex(primera.clave_idempotencia, r"^[0-9a-f]{32}$")
        self.assertNotEqual(primera.clave_idempotencia, segunda.clave_idempotencia)

    def test_misma_clave_devuelve_la_operacion_existente_sin_repetir_nada(self):
        self.autorizar()
        original, creada = self.iniciar("asistido", clave_idempotencia="clave-123")
        self.assertTrue(creada)
        antes = self.conteos()

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            repetida, creada = self.iniciar("asistido", clave_idempotencia="clave-123")

        self.assertEqual((repetida.pk, creada), (original.pk, False))
        self.assertEqual(self.conteos(), antes)
        self.assertEqual(callbacks, [])

    def test_clave_usada_por_otra_persona_o_finalidad_se_rechaza(self):
        self.iniciar("manual", clave_idempotencia="clave-123")
        antes = self.conteos()

        with self.assertRaises(DomainError):
            self.iniciar("manual", persona=self.crear_persona(), clave_idempotencia="clave-123")
        with self.assertRaises(DomainError):
            self.iniciar("manual", finalidad=F.RENOVACION, clave_idempotencia="clave-123")

        self.assertEqual(self.conteos(), antes)

    def test_segunda_solicitud_sin_clave_con_una_activa_devuelve_la_existente(self):
        for autorizada in (False, True):
            with self.subTest(autorizada=autorizada):
                persona = self.crear_persona()
                if autorizada:
                    self.autorizar(persona=persona)
                original, _ = self.iniciar("manual", persona=persona)
                antes = self.conteos()

                repetida, creada = self.iniciar("manual", persona=persona)

                self.assertEqual((repetida.pk, creada), (original.pk, False))
                self.assertEqual(self.conteos(), antes)

    def test_otra_finalidad_no_es_duplicado(self):
        primera, _ = self.iniciar("manual")

        segunda, creada = self.iniciar("manual", finalidad=F.RENOVACION)

        self.assertTrue(creada)
        self.assertNotEqual(primera.pk, segunda.pk)

    def test_tras_cerrarse_la_activa_se_puede_crear_una_nueva(self):
        for estado_final in (E.CANCELADA, E.VALIDADA, E.VENCIDA, E.FALLIDA):
            with self.subTest(estado=estado_final):
                persona = self.crear_persona()
                activa, _ = self.iniciar("manual", persona=persona)
                VerificacionAntecedentes.objects.filter(pk=activa.pk).update(estado=estado_final)

                nueva, creada = self.iniciar("manual", persona=persona)

                self.assertTrue(creada)
                self.assertNotEqual(nueva.pk, activa.pk)

    def test_la_base_impide_dos_activas_para_la_misma_persona_y_finalidad(self):
        self.crear_verificacion(estado=E.LISTA, persona=self.persona)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.crear_verificacion(estado=E.REQUIERE_REVISION, persona=self.persona)

        self.crear_verificacion(estado=E.CANCELADA, persona=self.persona)
        self.crear_verificacion(estado=E.LISTA, persona=self.persona, finalidad=F.RENOVACION)

    def test_carrera_entre_dos_solicitudes_devuelve_la_existente(self):
        original, _ = self.iniciar("manual")
        buscar = services._solicitud_existente
        llamadas = []

        def primera_vez_no_ve_nada(*args, **kwargs):
            llamadas.append(1)
            return None if len(llamadas) == 1 else buscar(*args, **kwargs)

        with mock.patch.object(services, "_solicitud_existente", side_effect=primera_vez_no_ve_nada):
            repetida, creada = self.iniciar("manual")

        self.assertEqual((repetida.pk, creada), (original.pk, False))
        self.assertEqual(VerificacionAntecedentes.objects.count(), 1)


class AutorizacionUITests(BaseAntecedentesTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def iniciar_por_vista(self, persona=None):
        return self.client.post(reverse("antecedentes:iniciar", args=[(persona or self.persona).pk]), follow=True)

    def datos_modal(self, **cambios):
        datos = dict(
            medio=MedioAutorizacion.PORTAL, texto="Autorizo la consulta de mis antecedentes.", version="1.0",
            referencia_evidencia="", otorgada_en=date.today().isoformat(),
        )
        datos.update(cambios)
        return datos

    def test_iniciar_dos_veces_desde_la_vista_deja_una_sola_verificacion(self):
        primera = self.iniciar_por_vista()
        segunda = self.iniciar_por_vista()

        self.assertEqual(VerificacionAntecedentes.objects.count(), 1)
        verificacion = VerificacionAntecedentes.objects.get()
        self.assertEqual(primera.redirect_chain[-1][0], reverse("antecedentes:detalle", args=[verificacion.pk]))
        self.assertEqual(segunda.redirect_chain[-1][0], reverse("antecedentes:detalle", args=[verificacion.pk]))
        self.assertContains(segunda, "Ya existía una solicitud activa")

    def test_detalle_pendiente_muestra_el_aviso_y_el_boton_de_autorizacion(self):
        self.iniciar_por_vista()
        verificacion = VerificacionAntecedentes.objects.get()

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(respuesta, "Autorización pendiente")
        self.assertContains(respuesta, reverse("antecedentes:autorizar", args=[verificacion.pk]))
        self.assertContains(respuesta, "Habilitación de ingreso a instalaciones")

    def test_detalle_con_autorizacion_muestra_sus_datos_y_no_el_boton(self):
        autorizacion = self.autorizar()
        verificacion, _ = self.iniciar("manual")

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(respuesta, "Portal")
        self.assertContains(respuesta, autorizacion.version)
        self.assertNotContains(respuesta, reverse("antecedentes:autorizar", args=[verificacion.pk]))

    def test_modal_registra_la_autorizacion_y_el_caso_pasa_a_lista(self):
        self.iniciar_por_vista()
        verificacion = VerificacionAntecedentes.objects.get()
        url = reverse("antecedentes:autorizar", args=[verificacion.pk])

        modal = self.client.get(url, HTTP_HX_REQUEST="true")
        respuesta = self.client.post(url, self.datos_modal(), HTTP_HX_REQUEST="true")

        self.assertEqual(modal.status_code, 200)
        self.assertContains(modal, "Autorización")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["HX-Redirect"], reverse("antecedentes:detalle", args=[verificacion.pk]))
        verificacion.refresh_from_db()
        autorizacion = AutorizacionDatos.objects.get()
        self.assertEqual(verificacion.estado, E.LISTA)
        self.assertEqual(verificacion.autorizacion, autorizacion)
        self.assertEqual((autorizacion.persona, autorizacion.registrada_por), (self.persona, self.usuario))
        self.assertEqual(autorizacion.finalidad, verificacion.finalidad)

    def test_modal_sin_referencia_para_documento_firmado_muestra_el_error_y_no_cambia_nada(self):
        self.iniciar_por_vista()
        verificacion = VerificacionAntecedentes.objects.get()
        url = reverse("antecedentes:autorizar", args=[verificacion.pk])

        respuesta = self.client.post(
            url, self.datos_modal(medio=MedioAutorizacion.DOCUMENTO_FIRMADO), HTTP_HX_REQUEST="true"
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("HX-Redirect", respuesta)
        self.assertContains(respuesta, "referencia")
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)
        self.assertEqual(AutorizacionDatos.objects.count(), 0)

    def test_modal_solo_para_verificaciones_que_esperan_autorizacion(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)

        respuesta = self.client.post(
            reverse("antecedentes:autorizar", args=[verificacion.pk]), self.datos_modal(), HTTP_HX_REQUEST="true"
        )

        self.assertNotIn("HX-Redirect", respuesta)
        self.assertEqual(AutorizacionDatos.objects.count(), 0)

    def test_modal_respeta_la_organizacion_y_exige_htmx(self):
        otra = Organizacion.objects.create(nombre="Otra organización")
        ajena = self.crear_verificacion(estado=E.AUTORIZACION_PENDIENTE, persona=self.crear_persona(otra))
        propia = self.crear_verificacion(estado=E.AUTORIZACION_PENDIENTE)

        ajena_resp = self.client.get(reverse("antecedentes:autorizar", args=[ajena.pk]), HTTP_HX_REQUEST="true")
        sin_htmx = self.client.get(reverse("antecedentes:autorizar", args=[propia.pk]))

        self.assertEqual(ajena_resp.status_code, 403)
        self.assertRedirects(sin_htmx, reverse("antecedentes:detalle", args=[propia.pk]))

    def test_api_repetida_devuelve_la_misma_operacion(self):
        url = reverse("antecedentes:api-iniciar", args=[self.persona.pk])

        primera = self.client.post(url)
        segunda = self.client.post(url)

        self.assertEqual((primera.status_code, segunda.status_code), (202, 200))
        self.assertEqual(primera.json()["id"], segunda.json()["id"])
        self.assertEqual(primera.json()["finalidad"], F.HABILITACION_INGRESO)
        self.assertEqual(VerificacionAntecedentes.objects.count(), 1)


class PermisosAntecedentesTests(BaseAntecedentesTests):
    """S8: rol dedicado + cuatro ojos; ANT-011 auditoría de lecturas."""

    def setUp(self):
        super().setUp()
        self.operador = self.crear_usuario("operador@example.com", "operador_antecedentes")
        self.revisor2 = self.crear_usuario("revisor2@example.com", "revisor_antecedentes")
        self.otra_org = Organizacion.objects.create(nombre="Otra organización")
        self.garita = self.crear_usuario("garita@example.com", "seguridad_garita")
        self.ajeno = self.crear_usuario("ajeno@example.com", "revisor_antecedentes", self.otra_org)
        self.sin_roles = self.crear_usuario("sinroles@example.com")
        self.superusuario = Usuario.objects.create_superuser(email="super@example.com", password="x")

    def no_autorizados(self):
        return {
            "garita": self.garita,
            "revisor de otra organización": self.ajeno,
            "sin roles": self.sin_roles,
            "superusuario sin roles": self.superusuario,
        }

    def verificacion_en_revision(self, cargado_por=None, **campos):
        return self.crear_verificacion(
            estado=E.REQUIERE_REVISION,
            resultado=ResultadoAntecedentes.SI,
            numero_certificado="CERT-77-RESERVADO",
            cargado_por=cargado_por,
            **campos,
        )

    def lecturas(self, verificacion=None):
        eventos = EventoAuditoria.objects.filter(accion="LEER_ANTECEDENTES")
        return eventos.filter(recurso_id=str(verificacion.pk)) if verificacion else eventos

    def datos_validar(self, **cambios):
        datos = {
            "accion": "validar",
            "decision": E.VALIDADA,
            "vigente_hasta": (date.today() + timedelta(days=30)).isoformat(),
            "comentario": "",
        }
        datos.update(cambios)
        return datos

    # --- roles -----------------------------------------------------------

    def test_asignar_rol_crea_el_rol_bajo_demanda_y_es_idempotente(self):
        RolAsignado.objects.filter(rol__codigo="operador_antecedentes").delete()
        Rol.objects.filter(codigo="operador_antecedentes").delete()
        usuario = self.crear_usuario("nuevo@example.com")

        for _ in range(2):
            services.asignar_rol_antecedentes(
                usuario=usuario, organizacion=self.organizacion, codigo="operador_antecedentes", actor=self.usuario
            )

        rol = Rol.objects.get(codigo="operador_antecedentes")
        self.assertEqual(rol.nombre, "Operador de antecedentes")
        asignacion = RolAsignado.objects.get(usuario=usuario, organizacion=self.organizacion, rol=rol)
        self.assertTrue(asignacion.activo)
        self.assertEqual(EventoAuditoria.objects.filter(accion="ROL_ANTECEDENTES_ASIGNADO").count(), 1)

    def test_asignar_rol_reactiva_una_asignacion_inactiva_y_rechaza_codigos_ajenos(self):
        asignacion = RolAsignado.objects.get(usuario=self.operador)
        asignacion.activo = False
        asignacion.save()

        services.asignar_rol_antecedentes(
            usuario=self.operador, organizacion=self.organizacion, codigo="operador_antecedentes", actor=self.usuario
        )
        with self.assertRaises(DomainError):
            services.asignar_rol_antecedentes(
                usuario=self.operador, organizacion=self.organizacion, codigo="seguridad_garita", actor=self.usuario
            )

        asignacion.refresh_from_db()
        self.assertTrue(asignacion.activo)
        self.assertEqual(RolAsignado.objects.filter(usuario=self.operador).count(), 1)

    def test_la_migracion_crea_los_dos_roles_sin_duplicarlos_ni_pisar_los_existentes(self):
        migracion = importlib.import_module("apps.antecedentes.migrations.0005_roles_antecedentes")
        codigos = ["operador_antecedentes", "revisor_antecedentes"]
        Rol.objects.filter(codigo="revisor_antecedentes").update(nombre="Nombre ajustado")
        RolAsignado.objects.filter(rol__codigo="operador_antecedentes").delete()
        Rol.objects.filter(codigo="operador_antecedentes").delete()

        for _ in range(2):
            migracion.crear_roles(django_apps, None)

        self.assertEqual(Rol.objects.filter(codigo__in=codigos).count(), 2)
        self.assertEqual(Rol.objects.get(codigo="operador_antecedentes").nombre, "Operador de antecedentes")
        self.assertEqual(Rol.objects.get(codigo="revisor_antecedentes").nombre, "Nombre ajustado")

    # --- operador --------------------------------------------------------

    def test_operador_inicia_autoriza_y_carga_pero_no_ve_el_resultado(self):
        self.client.force_login(self.operador)

        self.client.post(reverse("antecedentes:iniciar", args=[self.persona.pk]))
        verificacion = VerificacionAntecedentes.objects.get(persona=self.persona)
        self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)
        autorizada = self.client.post(
            reverse("antecedentes:autorizar", args=[verificacion.pk]),
            dict(
                medio=MedioAutorizacion.PORTAL, texto="Autorizo.", version="1.0", referencia_evidencia="",
                otorgada_en=date.today().isoformat(),
            ),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(autorizada.status_code, 200)
        self.client.post(
            reverse("antecedentes:detalle", args=[verificacion.pk]),
            {
                "accion": "cargar_resultado",
                "resultado": ResultadoAntecedentes.SI,
                "numero_certificado": "CERT-77-RESERVADO",
                "proveedor": "Ministerio del Interior",
                "comentario": "",
            },
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertEqual(verificacion.cargado_por, self.operador)
        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))
        self.assertEqual(respuesta.status_code, 200)
        for reservado in ("hallazgos", "CERT-77-RESERVADO", "Registra antecedentes"):
            self.assertNotContains(respuesta, reservado)
        self.assertNotContains(respuesta, 'value="validar"')

    def test_api_del_operador_oculta_resultado_y_datos_del_certificado(self):
        verificacion = self.verificacion_en_revision()
        consulta = ConsultaProveedor.objects.create(
            verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )
        consulta.documentos.create(
            nombre_original="cert.pdf", numero_certificado="CERT-77-RESERVADO", nombre_extraido="NOMBRE EXTRAIDO",
            identificacion_extraida="1799999999", detalle_validacion="Coincide: NOMBRE EXTRAIDO",
        )
        url = reverse("antecedentes:api-detalle", args=[verificacion.pk])

        self.client.force_login(self.operador)
        operador = self.client.get(url)
        self.client.force_login(self.revisor2)
        revisor = self.client.get(url)

        self.assertEqual(operador.status_code, 200)
        self.assertEqual(operador.json()["estado"], E.REQUIERE_REVISION)
        self.assertNotIn("resultado", operador.json())
        for reservado in ("CERT-77-RESERVADO", "NOMBRE EXTRAIDO", "1799999999"):
            self.assertNotIn(reservado, operador.content.decode())
        self.assertEqual(revisor.json()["resultado"], ResultadoAntecedentes.SI)
        documento = revisor.json()["consultas_proveedor"][0]["documentos"][0]
        self.assertEqual(documento["numero_certificado"], "CERT-77-RESERVADO")

    def test_operador_no_puede_validar_ni_por_vista_ni_por_servicio(self):
        verificacion = self.verificacion_en_revision()
        bitacora = verificacion.ejecuciones.count()
        self.client.force_login(self.operador)

        respuesta = self.client.post(reverse("antecedentes:detalle", args=[verificacion.pk]), self.datos_validar())
        with self.assertRaises(DomainError):
            services.validar_verificacion(
                verificacion=verificacion, revisor=self.operador, decision=E.VALIDADA,
                vigente_hasta=date.today() + timedelta(days=30),
            )

        self.assertEqual(respuesta.status_code, 403)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertIsNone(verificacion.validado_por)
        self.assertEqual(verificacion.ejecuciones.count(), bitacora)

    def test_la_bandeja_no_muestra_resultados_a_nadie(self):
        verificacion = self.verificacion_en_revision()
        for usuario in (self.operador, self.revisor2):
            with self.subTest(usuario=usuario.email):
                self.client.force_login(usuario)

                respuesta = self.client.get(reverse("antecedentes:bandeja"))

                self.assertContains(respuesta, verificacion.persona.nombre_completo)
                for reservado in ("hallazgos", "CERT-77-RESERVADO"):
                    self.assertNotContains(respuesta, reservado)
        self.assertEqual(self.lecturas().count(), 0)

    # --- revisor y auditoría de lectura -----------------------------------

    def test_revisor_ve_el_resultado_y_cada_lectura_del_detalle_queda_auditada(self):
        verificacion = self.verificacion_en_revision()
        self.client.force_login(self.revisor2)

        primera = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))
        self.assertEqual(self.lecturas(verificacion).count(), 1)
        self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(primera, "hallazgos")
        self.assertContains(primera, "CERT-77-RESERVADO")
        self.assertContains(primera, 'value="validar"')
        eventos = self.lecturas(verificacion)
        self.assertEqual(eventos.count(), 2)
        evento = eventos.first()
        self.assertEqual((evento.actor, evento.organizacion), (self.revisor2, self.organizacion))
        self.assertEqual(evento.recurso_tipo, "antecedentes.VerificacionAntecedentes")
        self.assertEqual(evento.metadata, {"resultado_visible": True})

    def test_lectura_del_operador_tambien_queda_auditada_sin_resultado_visible(self):
        verificacion = self.verificacion_en_revision()
        self.client.force_login(self.operador)

        self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        evento = self.lecturas(verificacion).get()
        self.assertEqual((evento.actor, evento.metadata), (self.operador, {"resultado_visible": False}))

    def test_cada_get_de_la_api_audita_y_las_lecturas_denegadas_no(self):
        verificacion = self.verificacion_en_revision()
        url = reverse("antecedentes:api-detalle", args=[verificacion.pk])

        self.client.force_login(self.revisor2)
        self.client.get(url)
        self.client.get(url)
        self.client.force_login(self.garita)
        self.client.get(url)
        self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertEqual(self.lecturas(verificacion).count(), 2)
        self.assertEqual(set(self.lecturas(verificacion).values_list("actor", flat=True)), {self.revisor2.pk})

    # --- cuatro ojos -------------------------------------------------------

    def test_quien_cargo_el_resultado_no_puede_validarlo(self):
        self.autorizar()
        verificacion = self.iniciar("manual")[0]
        services.registrar_resultado_manual(
            verificacion=verificacion, actor=self.usuario, resultado=ResultadoAntecedentes.NO
        )
        bitacora = verificacion.ejecuciones.count()
        eventos = EventoAuditoria.objects.count()

        with self.assertRaisesMessage(DomainError, "Quien cargó el documento no puede validarlo."):
            services.validar_verificacion(
                verificacion=verificacion, revisor=self.usuario, decision=E.VALIDADA,
                vigente_hasta=date.today() + timedelta(days=30),
            )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertIsNone(verificacion.validado_por)
        self.assertEqual(verificacion.ejecuciones.count(), bitacora)
        self.assertEqual(EventoAuditoria.objects.count(), eventos)

    def test_cuatro_ojos_tambien_aplica_a_devolver_y_anular(self):
        for decision in (E.LISTA, E.CANCELADA):
            with self.subTest(decision=decision):
                verificacion = self.verificacion_en_revision(cargado_por=self.usuario)

                with self.assertRaisesMessage(DomainError, "Quien cargó el documento no puede validarlo."):
                    services.validar_verificacion(
                        verificacion=verificacion, revisor=self.usuario, decision=decision, comentario="motivo"
                    )

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)

    def test_la_vista_rechaza_al_que_cargo_y_permite_a_otro_revisor(self):
        verificacion = self.verificacion_en_revision(cargado_por=self.usuario)
        url = reverse("antecedentes:detalle", args=[verificacion.pk])
        bitacora = verificacion.ejecuciones.count()

        self.client.force_login(self.usuario)
        rechazada = self.client.post(url, self.datos_validar(), follow=True)
        verificacion.refresh_from_db()
        self.assertContains(rechazada, "Quien cargó el documento no puede validarlo.")
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertEqual(verificacion.ejecuciones.count(), bitacora)

        self.client.force_login(self.revisor2)
        aceptada = self.client.post(url, self.datos_validar(), follow=True)
        verificacion.refresh_from_db()
        self.assertContains(aceptada, "Decisión registrada.")
        self.assertEqual((verificacion.estado, verificacion.validado_por), (E.VALIDADA, self.revisor2))

    def test_el_detalle_no_ofrece_validar_a_quien_cargo(self):
        verificacion = self.verificacion_en_revision(cargado_por=self.usuario)
        self.client.force_login(self.usuario)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertNotContains(respuesta, 'value="validar"')
        self.assertContains(respuesta, "Quien cargó el documento no puede validarlo.")

    # --- sin acceso ----------------------------------------------------------

    def test_sin_rol_el_detalle_es_404_en_vista_y_api_y_no_se_audita(self):
        verificacion = self.verificacion_en_revision()
        for nombre, usuario in self.no_autorizados().items():
            with self.subTest(usuario=nombre):
                self.client.force_login(usuario)

                vista = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))
                api = self.client.get(reverse("antecedentes:api-detalle", args=[verificacion.pk]))

                self.assertEqual((vista.status_code, api.status_code), (404, 404))
        self.assertEqual(self.lecturas().count(), 0)

    def test_sin_rol_la_bandeja_y_las_acciones_son_403_sin_efectos(self):
        verificacion = self.crear_verificacion(estado=E.AUTORIZACION_PENDIENTE)
        consulta = ConsultaProveedor.objects.create(
            verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )
        persona = self.crear_persona()
        for nombre, usuario in self.no_autorizados().items():
            with self.subTest(usuario=nombre):
                self.client.force_login(usuario)
                pdf = SimpleUploadedFile("c.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf")
                respuestas = {
                    "iniciar": self.client.post(reverse("antecedentes:iniciar", args=[persona.pk])),
                    "detalle": self.client.post(
                        reverse("antecedentes:detalle", args=[verificacion.pk]), self.datos_validar()
                    ),
                    "autorizar": self.client.get(
                        reverse("antecedentes:autorizar", args=[verificacion.pk]), HTTP_HX_REQUEST="true"
                    ),
                    "api-iniciar": self.client.post(reverse("antecedentes:api-iniciar", args=[persona.pk])),
                    "api-documento": self.client.post(
                        reverse("antecedentes:api-cargar-documento", args=[consulta.pk]), {"archivo": pdf}
                    ),
                }
                if usuario != self.ajeno:  # el de otra organización sí tiene bandeja, pero de la suya
                    respuestas["bandeja"] = self.client.get(reverse("antecedentes:bandeja"))
                for clave, respuesta in respuestas.items():
                    self.assertEqual(respuesta.status_code, 403, clave)
        self.assertEqual(VerificacionAntecedentes.objects.count(), 1)
        self.assertEqual(consulta.documentos.count(), 0)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTORIZACION_PENDIENTE)

    def test_la_bandeja_de_otra_organizacion_no_muestra_estos_casos(self):
        verificacion = self.verificacion_en_revision()
        self.client.force_login(self.ajeno)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, verificacion.persona.nombre_completo)

    # --- ficha de personas --------------------------------------------------

    def test_la_lista_de_personas_ofrece_antecedentes_solo_a_quien_puede_operar(self):
        url_iniciar = reverse("antecedentes:iniciar", args=[self.persona.pk])
        esperado = {self.operador: True, self.revisor2: True, self.garita: False}
        for usuario, ve_boton in esperado.items():
            with self.subTest(usuario=usuario.email):
                self.client.force_login(usuario)

                respuesta = self.client.get(reverse("personas:lista"))

                self.assertEqual(respuesta.status_code, 200)
                if ve_boton:
                    self.assertContains(respuesta, url_iniciar)
                else:
                    self.assertNotContains(respuesta, url_iniciar)


class MigracionEstadosTests(TestCase):
    ESTADOS_ANTIGUOS = [
        "SOLICITADA", "EN_COLA", "EN_PROCESO", "REINTENTANDO", "FALLIDA_TECNICA", "RESULTADO_SI_OBTENIDO",
        "RESULTADO_NO_OBTENIDO", "RESULTADO_NO_DETERMINADO", "CONTINGENCIA_MANUAL", "EN_REVISION_MANUAL",
        "VALIDADA", "OBSERVADA", "RECHAZADA", "VENCIDA", "REQUIERE_INTERVENCION", "PORTAL_NO_DISPONIBLE",
        "CARGA_MANUAL_PENDIENTE", "CARGA_MANUAL_COMPLETADA", "ERROR_DEFINITIVO",
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migracion = importlib.import_module("apps.antecedentes.migrations.0003_nueva_maquina_estados")

    def test_mapa_de_verificacion_cubre_todo_estado_antiguo(self):
        esperado = {
            "SOLICITADA": "CREADA", "EN_COLA": "LISTA", "EN_PROCESO": "EN_CONSULTA",
            "REINTENTANDO": "EN_CONSULTA", "FALLIDA_TECNICA": "FALLIDA",
            "RESULTADO_SI_OBTENIDO": "RESULTADO_OBTENIDO", "RESULTADO_NO_OBTENIDO": "RESULTADO_OBTENIDO",
            "RESULTADO_NO_DETERMINADO": "RESULTADO_OBTENIDO", "CONTINGENCIA_MANUAL": "LISTA",
            "EN_REVISION_MANUAL": "REQUIERE_REVISION", "VALIDADA": "VALIDADA", "OBSERVADA": "REQUIERE_REVISION",
            "RECHAZADA": "CANCELADA", "VENCIDA": "VENCIDA", "REQUIERE_INTERVENCION": "CAPTCHA_REQUERIDO",
            "PORTAL_NO_DISPONIBLE": "FALLIDA", "CARGA_MANUAL_PENDIENTE": "LISTA",
            "CARGA_MANUAL_COMPLETADA": "PDF_RECIBIDO", "ERROR_DEFINITIVO": "FALLIDA",
        }
        self.assertEqual(set(self.ESTADOS_ANTIGUOS), set(esperado))
        for antiguo in self.ESTADOS_ANTIGUOS:
            with self.subTest(antiguo=antiguo):
                self.assertEqual(self.migracion.estado_verificacion_nuevo(antiguo), esperado[antiguo])
                self.assertIn(esperado[antiguo], E.values)

    def test_mapa_de_consulta_cubre_todo_estado_antiguo_con_estados_tecnicos(self):
        for antiguo in self.ESTADOS_ANTIGUOS:
            with self.subTest(antiguo=antiguo):
                self.assertIn(self.migracion.estado_consulta_nuevo(antiguo), EstadoConsulta.values)
        self.assertEqual(self.migracion.estado_consulta_nuevo("EN_COLA"), "PENDIENTE")
        self.assertEqual(self.migracion.estado_consulta_nuevo("REQUIERE_INTERVENCION"), "CAPTCHA_REQUERIDO")
        self.assertEqual(self.migracion.estado_consulta_nuevo("CARGA_MANUAL_COMPLETADA"), "COMPLETADA")

    def test_estado_desconocido_se_deja_igual(self):
        self.assertEqual(self.migracion.estado_verificacion_nuevo("LISTA"), "LISTA")

    def test_reverso_devuelve_un_estado_antiguo_para_cada_estado_nuevo(self):
        for nuevo in E.values:
            with self.subTest(nuevo=nuevo):
                self.assertIn(self.migracion.estado_verificacion_antiguo(nuevo), self.ESTADOS_ANTIGUOS)
        for nuevo in EstadoConsulta.values:
            with self.subTest(nuevo=nuevo):
                self.assertIn(self.migracion.estado_consulta_antiguo(nuevo), self.ESTADOS_ANTIGUOS)

    def test_etiqueta_de_bitacora_tolera_estados_historicos(self):
        organizacion = Organizacion.objects.create(nombre="Org histórica")
        persona = Persona.objects.create(
            organizacion=organizacion, tipo_documento=TipoDocumento.CEDULA, numero_documento="2222222222",
            nombres="Ana", apellidos="Vieja",
        )
        verificacion = VerificacionAntecedentes.objects.create(
            organizacion=organizacion, persona=persona, modo_conector=ModoConector.MANUAL
        )
        historica = EjecucionConector.objects.create(verificacion=verificacion, estado_resultante="EN_REVISION_MANUAL")
        actual = EjecucionConector.objects.create(verificacion=verificacion, estado_resultante="LISTA")

        self.assertEqual(historica.get_estado_resultante_display(), "En revisión manual")
        self.assertEqual(actual.get_estado_resultante_display(), "Lista")


class MigracionDatosTests(TransactionTestCase):
    ANTERIOR = [("antecedentes", "0002_alter_ejecucionconector_estado_resultante_and_more")]
    NUEVA = [("antecedentes", "0003_nueva_maquina_estados")]
    AUTORIZACIONES = [("antecedentes", "0004_autorizacion_finalidad_idempotencia")]

    def migrar(self, destino):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(destino)
        return ejecutor.loader.project_state(destino).apps

    def tearDown(self):
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())

    def test_migra_datos_existentes_y_anexa_bitacora_sin_reescribirla(self):
        apps_antes = self.migrar(self.ANTERIOR)
        Org = apps_antes.get_model("identidad", "Organizacion")
        Per = apps_antes.get_model("personas", "Persona")
        Ver = apps_antes.get_model("antecedentes", "VerificacionAntecedentes")
        Con = apps_antes.get_model("antecedentes", "ConsultaProveedor")
        Eje = apps_antes.get_model("antecedentes", "EjecucionConector")
        org = Org.objects.create(nombre="Org migración")
        persona = Per.objects.create(
            organizacion=org, tipo_documento="CEDULA", numero_documento="3333333333", nombres="Ana", apellidos="Prueba"
        )
        ver = Ver.objects.create(organizacion=org, persona=persona, modo_conector="ASISTIDO", estado="REQUIERE_INTERVENCION")
        Con.objects.create(verificacion=ver, proveedor="MINISTERIO_INTERIOR", estado="REQUIERE_INTERVENCION")
        Eje.objects.create(verificacion=ver, estado_resultante="REQUIERE_INTERVENCION", detalle="historia")

        apps_despues = self.migrar(self.NUEVA)
        Ver = apps_despues.get_model("antecedentes", "VerificacionAntecedentes")
        Con = apps_despues.get_model("antecedentes", "ConsultaProveedor")
        Eje = apps_despues.get_model("antecedentes", "EjecucionConector")
        self.assertEqual(Ver.objects.get(pk=ver.pk).estado, "CAPTCHA_REQUERIDO")
        self.assertEqual(Con.objects.get().estado, "CAPTCHA_REQUERIDO")
        self.assertEqual(Eje.objects.get(detalle="historia").estado_resultante, "REQUIERE_INTERVENCION")
        migracion = Eje.objects.get(detalle__startswith="Migración de estados:")
        self.assertEqual(migracion.detalle, "Migración de estados: REQUIERE_INTERVENCION -> CAPTCHA_REQUERIDO.")
        self.assertEqual(Eje.objects.count(), 2)

        apps_reverso = self.migrar(self.ANTERIOR)
        self.assertEqual(apps_reverso.get_model("antecedentes", "VerificacionAntecedentes").objects.get().estado, "REQUIERE_INTERVENCION")
        self.assertEqual(apps_reverso.get_model("antecedentes", "ConsultaProveedor").objects.get().estado, "REQUIERE_INTERVENCION")
        self.assertEqual(apps_reverso.get_model("antecedentes", "EjecucionConector").objects.count(), 1)


    def test_autorizaciones_asigna_claves_y_deja_una_sola_solicitud_activa_por_persona(self):
        apps_antes = self.migrar(self.NUEVA)
        Org = apps_antes.get_model("identidad", "Organizacion")
        Per = apps_antes.get_model("personas", "Persona")
        Ver = apps_antes.get_model("antecedentes", "VerificacionAntecedentes")
        org = Org.objects.create(nombre="Org autorizaciones")
        persona = Per.objects.create(
            organizacion=org, tipo_documento="CEDULA", numero_documento="4444444444", nombres="Ana", apellidos="Doble"
        )
        vieja = Ver.objects.create(organizacion=org, persona=persona, modo_conector="MANUAL", estado="LISTA")
        nueva = Ver.objects.create(organizacion=org, persona=persona, modo_conector="MANUAL", estado="LISTA")
        validada = Ver.objects.create(organizacion=org, persona=persona, modo_conector="MANUAL", estado="VALIDADA")
        Ver.objects.filter(pk=vieja.pk).update(created_at=timezone.now() - timedelta(days=1))

        apps_despues = self.migrar(self.AUTORIZACIONES)
        Ver = apps_despues.get_model("antecedentes", "VerificacionAntecedentes")
        Eje = apps_despues.get_model("antecedentes", "EjecucionConector")
        estados = {v.pk: v for v in Ver.objects.all()}
        self.assertEqual(
            (estados[vieja.pk].estado, estados[nueva.pk].estado, estados[validada.pk].estado),
            ("CANCELADA", "LISTA", "VALIDADA"),
        )
        self.assertEqual({v.finalidad for v in estados.values()}, {"HABILITACION_INGRESO"})
        claves = [v.clave_idempotencia for v in estados.values()]
        self.assertTrue(all(claves))
        self.assertEqual(len(set(claves)), 3)
        self.assertEqual(Eje.objects.filter(verificacion_id=vieja.pk).count(), 1)
        self.assertEqual(Eje.objects.filter(verificacion_id=nueva.pk).count(), 0)


class PdfSeguroTests(TestCase):
    def test_rechaza_archivo_no_pdf_aunque_use_extension_pdf(self):
        archivo = SimpleUploadedFile("documento.pdf", b"<script>alert(1)</script>")

        self.assertEqual(validar_pdf_seguro(archivo), "El archivo no tiene una estructura PDF válida.")

    def test_rechaza_pdf_con_contenido_activo(self):
        archivo = SimpleUploadedFile(
            "documento.pdf",
            b"%PDF-1.7\n1 0 obj << /OpenAction 2 0 R >> endobj\n%%EOF",
        )

        self.assertEqual(
            validar_pdf_seguro(archivo),
            "El PDF contiene acciones o archivos incrustados no permitidos.",
        )

    def test_acepta_pdf_minimo_sin_contenido_activo(self):
        archivo = SimpleUploadedFile("documento.pdf", b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF")

        self.assertIsNone(validar_pdf_seguro(archivo))

    def test_rechaza_nombre_de_archivo_con_traversal_o_control(self):
        archivo = SimpleUploadedFile("../documento\x00.pdf", b"%PDF-1.7\n%%EOF")

        self.assertEqual(validar_pdf_seguro(archivo), "El nombre del archivo no es válido.")


@override_settings(ANTECEDENTES_CONECTOR_MODE="asistido")
class ConsultaAsistidaTests(BaseAntecedentesTests):
    """S2-S4: comprobación del portal con reintentos limitados, disyuntor y carga manual siempre disponible."""

    def setUp(self):
        super().setUp()
        self.operador = self.crear_usuario("operador@example.com", "operador_antecedentes")
        self.autorizar()

    def consulta(self, verificacion):
        return verificacion.consultas_proveedor.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)

    def bitacora(self, verificacion):
        return list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True))

    def hora(self, momento):
        return timezone.localtime(momento).strftime("%H:%M")

    def fallar_portal(self, persona=None):
        """Deja una verificación FALLIDA por portal caído (3 intentos) y devuelve (verificacion, consulta)."""
        if persona:
            self.autorizar(persona=persona)
        self.sondeo.return_value = caido()
        verificacion, _ = self.iniciar("asistido", persona=persona)
        verificacion.refresh_from_db()
        return verificacion, self.consulta(verificacion)

    def portal_disponible(self):
        self.sondeo.reset_mock()
        self.sondeo.side_effect = None
        self.sondeo.return_value = DISPONIBLE

    def test_portal_disponible_pide_intervencion_humana_sin_reintentar(self):
        verificacion, _ = self.iniciar("asistido")

        consulta = self.consulta(verificacion)
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(
            (consulta.estado, consulta.codigo_error, consulta.intentos, consulta.requiere_intervencion),
            (EstadoConsulta.CAPTCHA_REQUERIDO, "ANT_CAPTCHA_REQUIRED", 1, True),
        )
        intento = consulta.intentos_detalle.get()
        self.assertEqual((intento.latencia_ms, intento.detalle), (120, "HTTP 200 · 120 ms"))
        self.sondeo.assert_called_once_with(PORTAL_URL, 5)
        self.esperar.assert_not_called()

    def test_tiempo_agotado_o_5xx_reintenta_dos_veces_con_espera_incremental_y_luego_falla(self):
        antes = timezone.now()
        self.sondeo.side_effect = [caido(503), caido(504), caido(502)]

        verificacion, _ = self.iniciar("asistido")

        consulta = self.consulta(verificacion)
        self.assertEqual(self.sondeo.call_count, 3)
        self.assertEqual([c.args for c in self.esperar.call_args_list], [(2,), (5,)])
        self.assertEqual(verificacion.estado, E.FALLIDA)
        self.assertEqual(self.bitacora(verificacion), [E.LISTA, E.EN_CONSULTA, E.FALLIDA])
        self.assertEqual(
            (consulta.estado, consulta.codigo_error, consulta.intentos),
            (EstadoConsulta.FALLIDA, "ANT_SOURCE_UNAVAILABLE", 3),
        )
        self.assertEqual(
            list(consulta.intentos_detalle.order_by("numero").values_list("estado_resultante", flat=True)),
            [EstadoConsulta.REINTENTO_PROGRAMADO, EstadoConsulta.REINTENTO_PROGRAMADO, EstadoConsulta.FALLIDA],
        )
        # El disyuntor se abrió (3 fallos seguidos): esa es la hora a partir de la cual reintentar.
        salud = SaludFuente.objects.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)
        self.assertEqual(salud.fallos_consecutivos, 3)
        self.assertGreaterEqual(salud.abierto_hasta, antes + timedelta(seconds=600))
        self.assertEqual(consulta.proximo_intento_at, salud.abierto_hasta)
        nota = verificacion.ejecuciones.get(estado_resultante=E.FALLIDA).detalle
        self.assertIn(f"Vuelve a intentarlo después de las {self.hora(salud.abierto_hasta)}", nota)
        self.assertIn("carga el certificado manualmente", nota)
        self.assertIn("no equivale a ausencia de antecedentes", nota)

    def test_un_reintento_exitoso_pide_intervencion_y_cierra_el_disyuntor(self):
        self.sondeo.side_effect = [caido(), DISPONIBLE]

        verificacion, _ = self.iniciar("asistido")

        consulta = self.consulta(verificacion)
        self.assertEqual((verificacion.estado, consulta.intentos), (E.CAPTCHA_REQUERIDO, 2))
        self.assertEqual([c.args for c in self.esperar.call_args_list], [(2,)])
        salud = SaludFuente.objects.get()
        self.assertEqual((salud.fallos_consecutivos, salud.abierto_hasta), (0, None))

    def test_4xx_no_se_reintenta_registra_evidencia_minima_y_pide_intervencion(self):
        self.sondeo.return_value = ResultadoSondeo(
            ClaseRespuesta.DENEGADA, codigo_http=403, latencia_ms=90, evidencia="HTTP 403 · 90 ms"
        )

        verificacion, _ = self.iniciar("asistido")

        consulta = self.consulta(verificacion)
        self.assertEqual(self.sondeo.call_count, 1)
        self.esperar.assert_not_called()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual((consulta.codigo_error, consulta.detalle_error), ("ANT_CAPTCHA_REQUIRED", "HTTP 403 · 90 ms"))
        self.assertIn("no se reintenta", verificacion.ejecuciones.get(estado_resultante=E.CAPTCHA_REQUERIDO).detalle)
        self.assertFalse(SaludFuente.objects.filter(fallos_consecutivos__gt=0).exists())

    def test_429_no_se_reintenta_y_pausa_la_fuente_respetando_retry_after(self):
        antes = timezone.now()
        self.sondeo.return_value = ResultadoSondeo(
            ClaseRespuesta.LIMITADA, codigo_http=429, latencia_ms=50, evidencia="HTTP 429 · 50 ms", reintentar_tras=3600
        )

        verificacion, _ = self.iniciar("asistido")

        consulta = self.consulta(verificacion)
        self.assertEqual(self.sondeo.call_count, 1)
        self.esperar.assert_not_called()
        self.assertEqual((verificacion.estado, consulta.codigo_error), (E.FALLIDA, "ANT_RATE_LIMITED"))
        salud = SaludFuente.objects.get()
        self.assertGreaterEqual(salud.abierto_hasta, antes + timedelta(seconds=3600))
        self.assertEqual(consulta.proximo_intento_at, salud.abierto_hasta)

    def test_con_el_disyuntor_abierto_otra_verificacion_no_consulta_el_portal(self):
        self.fallar_portal()
        self.portal_disponible()
        otra = self.crear_persona()
        self.autorizar(persona=otra)

        verificacion, _ = self.iniciar("asistido", persona=otra)

        self.sondeo.assert_not_called()
        consulta = self.consulta(verificacion)
        salud = SaludFuente.objects.get()
        self.assertEqual((verificacion.estado, consulta.codigo_error), (E.FALLIDA, "ANT_SOURCE_UNAVAILABLE"))
        self.assertEqual(consulta.proximo_intento_at, salud.abierto_hasta)
        self.assertIn(self.hora(salud.abierto_hasta), verificacion.ejecuciones.get(estado_resultante=E.FALLIDA).detalle)

    def test_vencida_la_pausa_el_portal_se_vuelve_a_consultar(self):
        self.fallar_portal()
        SaludFuente.objects.update(abierto_hasta=timezone.now() - timedelta(seconds=1))
        self.portal_disponible()
        otra = self.crear_persona()
        self.autorizar(persona=otra)

        verificacion, _ = self.iniciar("asistido", persona=otra)

        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        salud = SaludFuente.objects.get()
        self.assertEqual((salud.fallos_consecutivos, salud.abierto_hasta), (0, None))

    def test_si_el_caso_cambia_durante_la_espera_se_detiene_sin_consultar_de_nuevo(self):
        self.sondeo.return_value = caido()
        self.esperar.side_effect = lambda _segundos: VerificacionAntecedentes.objects.update(estado=E.CANCELADA)

        verificacion, _ = self.iniciar("asistido")

        self.assertEqual(self.sondeo.call_count, 1)
        self.assertEqual(verificacion.estado, E.CANCELADA)
        self.assertEqual(self.bitacora(verificacion), [E.LISTA, E.EN_CONSULTA])

    def test_reintentar_antes_de_la_hora_indicada_se_rechaza_sin_cambios(self):
        verificacion, consulta = self.fallar_portal()
        self.portal_disponible()
        intentos = IntentoConsulta.objects.count()

        with self.assertRaisesMessage(DomainError, f"después de las {self.hora(consulta.proximo_intento_at)}"):
            services.reintentar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.FALLIDA)
        self.assertEqual(IntentoConsulta.objects.count(), intentos)
        self.sondeo.assert_not_called()

    def test_reintentar_despues_de_la_hora_vuelve_a_consultar(self):
        verificacion, consulta = self.fallar_portal()
        pasado = timezone.now() - timedelta(seconds=1)
        ConsultaProveedor.objects.update(proximo_intento_at=pasado)
        SaludFuente.objects.update(abierto_hasta=pasado)
        self.portal_disponible()

        services.reintentar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        consulta.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(self.bitacora(verificacion)[-4:], [E.FALLIDA, E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO])
        self.assertEqual(
            (consulta.estado, consulta.intentos, consulta.proximo_intento_at),
            (EstadoConsulta.CAPTCHA_REQUERIDO, 4, None),
        )

    def test_reintentar_con_otra_solicitud_activa_se_rechaza_sin_cambios(self):
        verificacion, _ = self.fallar_portal()
        ConsultaProveedor.objects.update(proximo_intento_at=None)
        SaludFuente.objects.update(abierto_hasta=None)
        self.portal_disponible()
        _, creada = self.iniciar("asistido")
        self.assertTrue(creada)

        with self.assertRaisesMessage(DomainError, "otra solicitud en curso"):
            services.reintentar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.FALLIDA)
        self.assertEqual(self.bitacora(verificacion)[-1], E.FALLIDA)

    def test_reintentar_solo_aplica_a_verificaciones_fallidas(self):
        verificacion, _ = self.iniciar("asistido")

        with self.assertRaises(DomainError):
            services.reintentar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)

    def test_continuar_abre_el_portal_y_reanuda_la_consulta(self):
        verificacion, _ = self.iniciar("asistido")

        url = services.continuar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(url, PORTAL_URL)
        self.assertEqual(verificacion.estado, E.EN_CONSULTA)
        self.assertEqual(self.consulta(verificacion).estado, EstadoConsulta.EN_CURSO)
        # Volver a abrir la pestaña no duplica transiciones.
        self.assertEqual(services.continuar_consulta(verificacion=verificacion, actor=self.operador), PORTAL_URL)
        self.assertEqual(self.bitacora(verificacion), [E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.EN_CONSULTA])

    def test_continuar_fuera_de_la_consulta_asistida_se_rechaza(self):
        verificacion = self.crear_verificacion(estado=E.LISTA)

        with self.assertRaises(DomainError):
            services.continuar_consulta(verificacion=verificacion, actor=self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.LISTA)

    def test_la_carga_manual_sigue_disponible_tras_continuar_o_fallar(self):
        verificacion, _ = self.iniciar("asistido")
        services.continuar_consulta(verificacion=verificacion, actor=self.operador)
        fallida, _ = self.fallar_portal(persona=self.crear_persona())

        for caso in (verificacion, fallida):
            with self.subTest(estado=caso.estado):
                services.registrar_resultado_manual(verificacion=caso, actor=self.operador, resultado=ResultadoAntecedentes.NO)
                caso.refresh_from_db()
                self.assertEqual(caso.estado, E.REQUIERE_REVISION)

    @override_settings(ANTECEDENTES_CONSULTA_ASINCRONA=True)
    def test_en_modo_asincrono_la_consulta_se_encola_al_confirmar(self):
        with mock.patch("apps.antecedentes.tasks.ejecutar_consulta_asistida.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                verificacion, _ = self.iniciar("asistido")

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.EN_CONSULTA)
        delay.assert_called_once_with(str(self.consulta(verificacion).pk))
        self.sondeo.assert_not_called()

    # --- vistas ---

    def detalle(self, verificacion, usuario=None):
        self.client.force_login(usuario or self.operador)
        return self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

    def test_detalle_ofrece_abrir_el_portal_con_la_cedula_lista_para_copiar(self):
        verificacion, _ = self.iniciar("asistido")

        respuesta = self.detalle(verificacion)

        self.assertContains(respuesta, 'value="continuar"')
        self.assertContains(respuesta, 'target="_blank"')
        self.assertContains(respuesta, f'value="{self.persona.numero_documento}"')

    def test_post_continuar_redirige_al_portal_oficial(self):
        verificacion, _ = self.iniciar("asistido")
        self.client.force_login(self.operador)

        respuesta = self.client.post(reverse("antecedentes:detalle", args=[verificacion.pk]), {"accion": "continuar"})

        self.assertRedirects(respuesta, PORTAL_URL, fetch_redirect_response=False)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.EN_CONSULTA)

    def test_detalle_de_una_fallida_indica_la_hora_para_reintentar_y_ofrece_la_carga_manual(self):
        verificacion, consulta = self.fallar_portal()

        respuesta = self.detalle(verificacion)

        self.assertContains(respuesta, f"Podrás reintentar después de las {self.hora(consulta.proximo_intento_at)}")
        self.assertNotContains(respuesta, 'value="reintentar"')
        self.assertContains(respuesta, 'value="cargar_resultado"')

    def test_post_reintentar_vuelve_a_consultar_cuando_ya_se_puede(self):
        verificacion, _ = self.fallar_portal()
        ConsultaProveedor.objects.update(proximo_intento_at=None)
        SaludFuente.objects.update(abierto_hasta=None)
        self.portal_disponible()
        self.assertContains(self.detalle(verificacion), 'value="reintentar"')

        respuesta = self.client.post(reverse("antecedentes:detalle", args=[verificacion.pk]), {"accion": "reintentar"})

        self.assertRedirects(respuesta, reverse("antecedentes:detalle", args=[verificacion.pk]))
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)

    def test_sin_rol_no_se_puede_continuar_ni_reintentar(self):
        verificacion, _ = self.iniciar("asistido")
        self.client.force_login(self.crear_usuario("ajeno@example.com"))

        for accion in ("continuar", "reintentar"):
            with self.subTest(accion=accion):
                respuesta = self.client.post(reverse("antecedentes:detalle", args=[verificacion.pk]), {"accion": accion})
                self.assertEqual(respuesta.status_code, 403)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)


class SondeoPortalTests(TestCase):
    """Clasificación de la respuesta del portal: una sola petición, sin datos personales ni lectura del cuerpo."""

    def sondear(self, efecto):
        with mock.patch("apps.antecedentes.conectores.ministerio.urllib.request.urlopen", side_effect=efecto) as urlopen:
            resultado = sondear_portal(PORTAL_URL, 5)
        return resultado, urlopen

    def http_error(self, codigo, cabeceras=None):
        mensaje = Message()
        for nombre, valor in (cabeceras or {}).items():
            mensaje[nombre] = valor
        return urllib.error.HTTPError(PORTAL_URL, codigo, "error", mensaje, None)

    def test_respuesta_2xx_es_disponible_y_la_peticion_no_lleva_datos_personales(self):
        respuesta = mock.MagicMock(status=200)
        respuesta.__enter__.return_value = respuesta

        resultado, urlopen = self.sondear([respuesta])

        self.assertEqual((resultado.clase, resultado.codigo_http), (ClaseRespuesta.DISPONIBLE, 200))
        self.assertTrue(resultado.evidencia.startswith("HTTP 200 · "))
        peticion = urlopen.call_args.args[0]
        self.assertEqual((peticion.full_url, peticion.get_method(), peticion.data), (PORTAL_URL, "GET", None))
        self.assertIn("SIGECOP", peticion.get_header("User-agent"))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)
        respuesta.read.assert_not_called()

    def test_5xx_tiempo_agotado_y_error_de_red_son_transitorios(self):
        for efecto, evidencia in (
            (self.http_error(503), "HTTP 503 · "),
            (TimeoutError("timed out"), "Sin respuesta (TimeoutError) · "),
            (urllib.error.URLError(ConnectionRefusedError()), "Sin respuesta (ConnectionRefusedError) · "),
        ):
            with self.subTest(efecto=efecto):
                resultado, _ = self.sondear([efecto])
                self.assertEqual(resultado.clase, ClaseRespuesta.TRANSITORIA)
                self.assertTrue(resultado.evidencia.startswith(evidencia), resultado.evidencia)

    def test_4xx_es_denegado_y_429_es_limitado_con_retry_after(self):
        denegado, _ = self.sondear([self.http_error(403)])
        limitado, _ = self.sondear([self.http_error(429, {"Retry-After": "120"})])
        sin_cabecera, _ = self.sondear([self.http_error(429)])

        self.assertEqual((denegado.clase, denegado.codigo_http), (ClaseRespuesta.DENEGADA, 403))
        self.assertTrue(denegado.evidencia.startswith("HTTP 403 · "))
        self.assertEqual((limitado.clase, limitado.reintentar_tras), (ClaseRespuesta.LIMITADA, 120))
        self.assertIsNone(sin_cabecera.reintentar_tras)



# --- T5: pipeline del certificado PDF (S7) ---------------------------------------------------------------


def _escapar_pdf(texto):
    return texto.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def crear_pdf(lineas=(), *, firma=False):
    """PDF de una página, válido y con una línea de texto por elemento (sin fixtures binarios)."""
    flujo = b""
    if lineas:
        flujo = (
            b"BT /F1 11 Tf 16 TL 50 780 Td "
            + b"".join(b"(" + _escapar_pdf(linea) + b") Tj T* " for linea in lineas)
            + b"ET"
        )
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(flujo) + flujo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    if firma:
        objetos.append(
            b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
            b"/ByteRange [0 10 20 30] /Contents <00> >>"
        )
    salida = bytearray(b"%PDF-1.4\n")
    posiciones = []
    for numero, cuerpo in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n" % numero + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for posicion in posiciones:
        salida += b"%010d 00000 n \n" % posicion
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(salida)


def lineas_certificado(
    identificacion="1111111111", nombres="EVALUADA PERSONA", resultado="NO", numero="MI-2026-004455", firmado=True
):
    lineas = [
        "MINISTERIO DEL INTERIOR",
        "CERTIFICADO DE ANTECEDENTES PENALES",
        "Fecha de Emisión: 19 de octubre de 2026",
        f"Número de Certificado: {numero}",
        "Tipo de Documento: CÉDULA",
        f"No. de Identificación: {identificacion}",
        f"Apellidos y Nombres: {nombres}",
        f"Registra Antecedentes: {resultado}",
        "Válido hasta el 18 de ABRIL del 2027",
    ]
    if firmado:
        lineas.append("Documento firmado electrónicamente")
    return [linea for linea in lineas if not linea.endswith(": ")]


class CertificadoParserTests(SimpleTestCase):
    """Parser puro del diseño del certificado (S7); no hay PDF real de muestra, por eso es tolerante."""

    def test_extrae_los_campos_del_diseno_del_certificado(self):
        datos = certificado.analizar_texto("\n".join(lineas_certificado()))

        self.assertEqual(datos.fecha_emision, date(2026, 10, 19))
        self.assertEqual(datos.numero_certificado, "MI-2026-004455")
        self.assertEqual(datos.tipo_documento, "CÉDULA")
        self.assertEqual(datos.identificacion, "1111111111")
        self.assertEqual(datos.nombres, "EVALUADA PERSONA")
        self.assertEqual(datos.resultado, "NO")
        self.assertEqual(datos.vigente_hasta, date(2027, 4, 18))
        self.assertTrue(datos.marca_firma)

    def test_tolera_tildes_ausentes_mayusculas_saltos_de_linea_y_dos_puntos(self):
        texto = (
            "FECHA DE EMISION\n2026-10-19 08:31:22\n"
            "numero de certificado   MI-77\n"
            "NO. DE IDENTIFICACION\n  1111111111  \n"
            "apellidos y nombres\nEVALUADA\nPERSONA\n"
            "Registra Antecedentes  SÍ\n"
            "valido hasta el 3 de setiembre del 2027\n"
            "documento firmado electronicamente"
        )

        datos = certificado.analizar_texto(texto)

        self.assertEqual(datos.fecha_emision, date(2026, 10, 19))
        self.assertEqual(datos.numero_certificado, "MI-77")
        self.assertEqual(datos.identificacion, "1111111111")
        self.assertEqual(datos.nombres, "EVALUADA PERSONA")
        self.assertEqual(datos.resultado, "SI")
        self.assertEqual(datos.vigente_hasta, date(2027, 9, 3))
        self.assertTrue(datos.marca_firma)

    def test_texto_ajeno_o_incompleto_no_inventa_valores(self):
        textos = (
            "",
            "Factura 001-001 total 25.00",
            "Registra Antecedentes: TAL VEZ\nVálido hasta el 31 de febrero del 2027",
        )
        for texto in textos:
            with self.subTest(texto=texto):
                datos = certificado.analizar_texto(texto)

                self.assertIsNone(datos.resultado)
                self.assertIsNone(datos.vigente_hasta)
                self.assertIsNone(datos.fecha_emision)
                self.assertEqual((datos.identificacion, datos.nombres, datos.numero_certificado), ("", "", ""))
                self.assertFalse(datos.marca_firma)

    def test_nombres_equivalentes_ignoran_orden_tildes_mayusculas_y_espacios(self):
        self.assertTrue(certificado.nombres_equivalentes("  PÉREZ   gómez  juan ", "Juan Pérez Gomez"))
        self.assertFalse(certificado.nombres_equivalentes("JUAN PEREZ", "Juan Carlos Perez"))
        self.assertFalse(certificado.nombres_equivalentes("", ""))


class PipelineCertificadoBase(BaseAntecedentesTests):
    """Almacenamiento privado en un directorio temporal; opera el operador y decide otro revisor."""

    def setUp(self):
        super().setUp()
        self.raiz = tempfile.mkdtemp(prefix="antecedentes-pruebas-")
        self.addCleanup(shutil.rmtree, self.raiz, True)
        ajustes = self.settings(ANTECEDENTES_PRIVATE_ROOT=self.raiz)
        ajustes.enable()
        self.addCleanup(ajustes.disable)
        self.operador = self.crear_usuario("operador-pdf@example.com", "operador_antecedentes")
        self.revisor2 = self.crear_usuario("revisor2-pdf@example.com", "revisor_antecedentes")

    def verificacion_lista(self, estado=E.LISTA, persona=None):
        persona = persona or self.persona
        # Una sola solicitud activa por persona y finalidad: las subpruebas parten de cero.
        persona.verificaciones_antecedentes.all().delete()
        verificacion = self.crear_verificacion(estado=estado, persona=persona)
        consulta = ConsultaProveedor.objects.create(
            verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )
        return verificacion, consulta

    def pdf_subido(self, contenido, nombre="certificado.pdf"):
        return SimpleUploadedFile(nombre, contenido, content_type="application/pdf")

    def cargar(self, verificacion, contenido, actor=None, nombre="certificado.pdf"):
        return services.cargar_certificado(
            verificacion=verificacion, archivo=self.pdf_subido(contenido, nombre), actor=actor or self.operador
        )

    def estados_bitacora(self, verificacion):
        return list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True))

    def eventos(self, accion):
        return EventoAuditoria.objects.filter(accion=accion)

    def archivos_guardados(self):
        return [nombre for _, _, nombres in os.walk(self.raiz) for nombre in nombres]


class CargarCertificadoTests(PipelineCertificadoBase):
    def test_certificado_que_coincide_llega_a_autenticidad_pendiente_y_conserva_la_cadena_de_custodia(self):
        verificacion, consulta = self.verificacion_lista()
        contenido = crear_pdf(lineas_certificado())

        documento, creado = self.cargar(verificacion, contenido, nombre="Mi Certificado.pdf")

        verificacion.refresh_from_db()
        consulta.refresh_from_db()
        self.assertTrue(creado)
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(
            self.estados_bitacora(verificacion), [E.PDF_RECIBIDO, E.DATOS_COINCIDENTES, E.AUTENTICIDAD_PENDIENTE]
        )
        self.assertEqual(verificacion.cargado_por, self.operador)
        self.assertEqual(verificacion.resultado, ResultadoAntecedentes.NO)
        self.assertEqual(verificacion.numero_certificado, "MI-2026-004455")
        self.assertEqual(timezone.localtime(verificacion.fecha_resultado).date(), date(2026, 10, 19))
        self.assertEqual(verificacion.proveedor, "Ministerio del Interior")
        self.assertIsNone(verificacion.vigente_hasta)  # la vigencia la decide el revisor (T7)
        self.assertIsNone(verificacion.validado_por)
        self.assertEqual(consulta.estado, EstadoConsulta.COMPLETADA)

        self.assertEqual(documento.consulta, consulta)
        self.assertEqual(documento.sha256, hashlib.sha256(contenido).hexdigest())
        self.assertEqual(documento.nombre_original, "Mi Certificado.pdf")
        self.assertEqual(documento.tamano_bytes, len(contenido))
        self.assertEqual(documento.estado_validacion, "DATOS_COINCIDENTES")
        self.assertEqual(documento.codigo_error, "")
        self.assertEqual(documento.identificacion_extraida, "1111111111")
        self.assertEqual(documento.nombre_extraido, "EVALUADA PERSONA")
        self.assertEqual(documento.numero_certificado, "MI-2026-004455")
        self.assertEqual(documento.resultado_extraido, "NO")
        self.assertEqual(documento.fecha_emision, date(2026, 10, 19))
        self.assertEqual(documento.vigente_hasta_extraida, date(2027, 4, 18))
        self.assertFalse(documento.firma_detectada)
        self.assertIn("firmado electrónicamente", documento.detalle_validacion)
        self.assertIn("pendiente", documento.detalle_validacion.lower())

        # Original intacto, en almacenamiento privado y fuera de /media/.
        with documento.archivo.open("rb") as almacenado:
            self.assertEqual(almacenado.read(), contenido)
        ruta = os.path.abspath(documento.archivo.path)
        self.assertTrue(ruta.startswith(os.path.abspath(self.raiz)))
        self.assertFalse(ruta.startswith(os.path.abspath(settings.MEDIA_ROOT)))
        self.assertNotIn("Mi Certificado", documento.archivo.name)

        evento = self.eventos("DOCUMENTO_ANTECEDENTES_RECIBIDO").get()
        self.assertEqual(evento.recurso_id, str(documento.pk))
        self.assertEqual(evento.actor, self.operador)
        self.assertEqual(evento.organizacion, self.organizacion)
        self.assertEqual(evento.metadata["sha256"], documento.sha256)
        for personal in ("1111111111", "EVALUADA", "Persona"):
            self.assertNotIn(personal, evento.motivo)
            self.assertNotIn(personal, str(evento.metadata))

    def test_resultado_si_con_identidad_correcta_tambien_queda_para_autenticidad(self):
        verificacion, _ = self.verificacion_lista()

        documento, _ = self.cargar(verificacion, crear_pdf(lineas_certificado(resultado="SI")))

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(verificacion.resultado, ResultadoAntecedentes.SI)
        self.assertEqual(documento.resultado_extraido, "SI")

    def test_la_carga_esta_disponible_desde_todos_los_estados_de_carga_manual(self):
        for estado in maquina_estados.ESTADOS_CARGA_MANUAL:
            with self.subTest(estado=estado):
                verificacion, _ = self.verificacion_lista(estado)

                self.cargar(verificacion, crear_pdf(lineas_certificado()))

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)

    def test_firma_electronica_detectada_se_registra_sin_darla_por_valida(self):
        verificacion, _ = self.verificacion_lista()

        documento, _ = self.cargar(verificacion, crear_pdf(lineas_certificado(), firma=True))

        self.assertTrue(documento.firma_detectada)
        self.assertIn("validación criptográfica", documento.detalle_validacion)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)

    def test_nombres_en_otro_orden_con_tildes_y_mayusculas_coinciden(self):
        self.persona.nombres = "José Ángel"
        self.persona.apellidos = "Núñez Pérez"
        self.persona.save()
        verificacion, _ = self.verificacion_lista()

        self.cargar(verificacion, crear_pdf(lineas_certificado(nombres="PEREZ NUÑEZ  JOSE ANGEL")))

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)

    def test_identidad_distinta_exige_revision_conserva_el_original_y_bloquea_la_aceptacion(self):
        casos = {
            "otra cédula": lineas_certificado(identificacion="1111111112"),
            "cédula con un dígito de más": lineas_certificado(identificacion="11111111111"),
            "otro nombre": lineas_certificado(nombres="OTRA PERSONA DISTINTA"),
        }
        for nombre, lineas in casos.items():
            with self.subTest(caso=nombre):
                verificacion, _ = self.verificacion_lista()
                contenido = crear_pdf(lineas)

                documento, _ = self.cargar(verificacion, contenido)

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
                self.assertEqual(self.estados_bitacora(verificacion), [E.PDF_RECIBIDO, E.REQUIERE_REVISION])
                self.assertEqual(documento.estado_validacion, "REQUIERE_REVISION")
                self.assertEqual(documento.codigo_error, "ANT_IDENTITY_MISMATCH")
                self.assertIsNone(verificacion.resultado)  # no se copia el resultado de otra identidad
                self.assertEqual(verificacion.cargado_por, self.operador)
                with documento.archivo.open("rb") as almacenado:
                    self.assertEqual(almacenado.read(), contenido)

                bitacora = verificacion.ejecuciones.count()
                with self.assertRaises(DomainError):
                    services.validar_verificacion(
                        verificacion=verificacion,
                        revisor=self.revisor2,
                        decision=E.VALIDADA,
                        vigente_hasta=date.today() + timedelta(days=30),
                    )
                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
                self.assertEqual(verificacion.ejecuciones.count(), bitacora)
                # Escalar: el revisor sí puede devolver el caso para una nueva carga.
                services.validar_verificacion(
                    verificacion=verificacion, revisor=self.revisor2, decision=E.LISTA, comentario="Documento ajeno."
                )

    def test_resultado_o_campos_ausentes_son_ambiguos_y_van_a_revision(self):
        casos = {
            "sin resultado": lineas_certificado(resultado=""),
            "resultado no reconocido": lineas_certificado(resultado="TAL VEZ"),
            "sin identificación": lineas_certificado(identificacion=""),
            "sin nombres": lineas_certificado(nombres=""),
            "texto ajeno": ["Factura 001-001", "Total 25.00"],
        }
        for nombre, lineas in casos.items():
            with self.subTest(caso=nombre):
                verificacion, _ = self.verificacion_lista()

                documento, _ = self.cargar(verificacion, crear_pdf(lineas))

                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
                self.assertEqual(self.estados_bitacora(verificacion), [E.PDF_RECIBIDO, E.REQUIERE_REVISION])
                self.assertEqual(documento.codigo_error, "ANT_RESULT_AMBIGUOUS")
                self.assertEqual(documento.estado_validacion, "REQUIERE_REVISION")
                self.assertIsNone(verificacion.resultado)

    def test_pdf_ilegible_cifrado_danado_o_sin_texto_va_a_cuarentena_sin_cambiar_el_caso(self):
        valido = crear_pdf(lineas_certificado())
        casos = {
            "no es un PDF": b"<script>alert(1)</script>",
            "cifrado": valido.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R"),
            "contenido activo": valido.replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction 2 0 R"),
            "dañado": b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n" + valido[200:900] + b"\n%%EOF",
            "sin texto": crear_pdf(()),
        }
        for nombre, contenido in casos.items():
            with self.subTest(caso=nombre):
                verificacion, consulta = self.verificacion_lista(E.EN_CONSULTA)
                bitacora = verificacion.ejecuciones.count()

                with self.assertRaises(services.DocumentoInvalido) as contexto:
                    self.cargar(verificacion, contenido)

                self.assertEqual(contexto.exception.codigo, "ANT_DOCUMENT_INVALID")
                documento = DocumentoAntecedentes.objects.get(consulta=consulta)
                self.assertEqual(contexto.exception.documento, documento)
                self.assertEqual(documento.estado_validacion, "CUARENTENA")
                self.assertEqual(documento.codigo_error, "ANT_DOCUMENT_INVALID")
                self.assertEqual(documento.detalle_validacion.split(" Se conserva")[0], str(contexto.exception))
                self.assertEqual(documento.sha256, hashlib.sha256(contenido).hexdigest())
                self.assertEqual(documento.resultado_extraido, "")
                with documento.archivo.open("rb") as almacenado:
                    self.assertEqual(almacenado.read(), contenido)
                verificacion.refresh_from_db()
                consulta.refresh_from_db()
                self.assertEqual(verificacion.estado, E.EN_CONSULTA)
                self.assertIsNone(verificacion.cargado_por)
                self.assertIsNone(verificacion.resultado)
                self.assertEqual(verificacion.ejecuciones.count(), bitacora)
                self.assertEqual(consulta.estado, EstadoConsulta.PENDIENTE)
                evento = self.eventos("DOCUMENTO_ANTECEDENTES_RECIBIDO").get(recurso_id=str(documento.pk))
                self.assertEqual(evento.metadata["sha256"], documento.sha256)
                self.assertEqual(evento.metadata["estado_validacion"], "CUARENTENA")

                # Tras la cuarentena se puede cargar otro certificado.
                self.cargar(verificacion, crear_pdf(lineas_certificado(numero="MI-OTRO")))
                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)

    def test_el_mismo_archivo_no_se_duplica_ni_repite_efectos(self):
        verificacion, consulta = self.verificacion_lista()
        contenido = crear_pdf(lineas_certificado())
        primero, creado_primero = self.cargar(verificacion, contenido)
        verificacion.refresh_from_db()
        bitacora = verificacion.ejecuciones.count()
        eventos = EventoAuditoria.objects.count()

        segundo, creado_segundo = self.cargar(verificacion, contenido)

        self.assertEqual((creado_primero, creado_segundo), (True, False))
        self.assertEqual(segundo.pk, primero.pk)
        self.assertEqual(DocumentoAntecedentes.objects.filter(consulta=consulta).count(), 1)
        self.assertEqual(len(self.archivos_guardados()), 1)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(verificacion.ejecuciones.count(), bitacora)
        self.assertEqual(EventoAuditoria.objects.count(), eventos)

    def test_el_mismo_archivo_en_cuarentena_no_crea_otra_fila(self):
        verificacion, consulta = self.verificacion_lista()
        for _ in range(2):
            with self.assertRaises(services.DocumentoInvalido):
                self.cargar(verificacion, b"%PDF-1.4\nbasura\n%%EOF")

        self.assertEqual(DocumentoAntecedentes.objects.filter(consulta=consulta).count(), 1)
        self.assertEqual(self.eventos("DOCUMENTO_ANTECEDENTES_RECIBIDO").count(), 1)

    def test_estado_que_no_admite_carga_se_rechaza_sin_guardar_nada(self):
        for estado in [e for e in EstadoVerificacion if e not in maquina_estados.ESTADOS_CARGA_MANUAL]:
            with self.subTest(estado=estado):
                verificacion, _ = self.verificacion_lista(estado)

                with self.assertRaises(DomainError) as contexto:
                    self.cargar(verificacion, crear_pdf(lineas_certificado()))

                self.assertNotIsInstance(contexto.exception, services.DocumentoInvalido)
                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, estado)
                self.assertEqual(DocumentoAntecedentes.objects.count(), 0)
                self.assertEqual(self.eventos("DOCUMENTO_ANTECEDENTES_RECIBIDO").count(), 0)
        self.assertEqual(self.archivos_guardados(), [])

    def test_nombre_vacio_o_tamano_fuera_de_limite_se_rechazan_sin_guardar_nada(self):
        verificacion, _ = self.verificacion_lista()
        tamano = "El archivo debe tener entre 1 byte y 10 MB."
        nombre_invalido = "El nombre del archivo no es válido."
        casos = {
            "demasiado grande": (b"%PDF-1.4\n" + b"0" * MAX_DOCUMENT_BYTES + b"\n%%EOF", "c.pdf", tamano),
            "vacío": (b"", "c.pdf", tamano),
            "nombre con control": (crear_pdf(lineas_certificado()), "../c\x00.pdf", nombre_invalido),
            "sin extensión pdf": (crear_pdf(lineas_certificado()), "c.exe", nombre_invalido),
        }
        for nombre, (contenido, archivo, mensaje) in casos.items():
            with self.subTest(caso=nombre):
                with self.assertRaises(services.DocumentoInvalido) as contexto:
                    self.cargar(verificacion, contenido, nombre=archivo)

                self.assertEqual(str(contexto.exception), mensaje)
                self.assertIsNone(contexto.exception.documento)
                verificacion.refresh_from_db()
                self.assertEqual(verificacion.estado, E.LISTA)
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)
        self.assertEqual(self.archivos_guardados(), [])

    def test_validacion_estructural_conserva_sus_mensajes_al_vivir_en_el_servicio(self):
        cifrado = SimpleUploadedFile("c.pdf", b"%PDF-1.7\n/Encrypt 1 0 R\n%%EOF")
        sin_eof = SimpleUploadedFile("c.pdf", b"%PDF-1.7\n1 0 obj endobj")
        grande = SimpleUploadedFile("c.pdf", b"%PDF-" + b"0" * MAX_DOCUMENT_BYTES)

        self.assertEqual(validar_pdf_seguro(cifrado), "No se aceptan PDF cifrados.")
        self.assertEqual(validar_pdf_seguro(sin_eof), "El archivo no tiene una estructura PDF válida.")
        self.assertEqual(validar_pdf_seguro(grande), "El archivo debe tener entre 1 byte y 10 MB.")


class CargarCertificadoAPITests(PipelineCertificadoBase):
    def url(self, consulta):
        return reverse("antecedentes:api-cargar-documento", args=[consulta.pk])

    def test_carga_valida_responde_201_y_el_reintento_200_con_el_mismo_documento(self):
        verificacion, consulta = self.verificacion_lista()
        contenido = crear_pdf(lineas_certificado())
        self.client.force_login(self.operador)

        primera = self.client.post(self.url(consulta), {"archivo": self.pdf_subido(contenido)})
        repetida = self.client.post(self.url(consulta), {"archivo": self.pdf_subido(contenido)})

        documento = DocumentoAntecedentes.objects.get(consulta=consulta)
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(primera.json(), {"documento_id": str(documento.pk), "estado": "DATOS_COINCIDENTES"})
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(repetida.json(), primera.json())
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(verificacion.cargado_por, self.operador)
        self.assertEqual(self.eventos("DOCUMENTO_ANTECEDENTES_RECIBIDO").count(), 1)

    def test_pdf_invalido_responde_400_con_codigo_y_deja_el_documento_en_cuarentena(self):
        verificacion, consulta = self.verificacion_lista(E.CAPTCHA_REQUERIDO)
        self.client.force_login(self.operador)

        respuesta = self.client.post(self.url(consulta), {"archivo": self.pdf_subido(b"%PDF-1.4\nbasura\n%%EOF")})

        documento = DocumentoAntecedentes.objects.get(consulta=consulta)
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(
            respuesta.json(),
            {"code": "ANT_DOCUMENT_INVALID", "detail": documento.detalle_validacion.split(" Se conserva")[0]},
        )
        self.assertEqual(documento.estado_validacion, "CUARENTENA")
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)

    def test_estado_incorrecto_archivo_ausente_y_nombre_invalido_responden_400_sin_guardar(self):
        _, consulta_revision = self.verificacion_lista(E.REQUIERE_REVISION)
        _, consulta_lista = self.verificacion_lista(persona=self.crear_persona())
        contenido = crear_pdf(lineas_certificado())
        self.client.force_login(self.operador)

        estado = self.client.post(self.url(consulta_revision), {"archivo": self.pdf_subido(contenido)})
        ausente = self.client.post(self.url(consulta_lista), {})
        nombre = self.client.post(self.url(consulta_lista), {"archivo": self.pdf_subido(contenido, "c.exe")})

        self.assertEqual(estado.status_code, 400)
        self.assertNotIn("code", estado.json())
        self.assertEqual(ausente.status_code, 400)
        self.assertEqual(ausente.json(), {"detail": "El campo archivo es obligatorio."})
        self.assertEqual(nombre.status_code, 400)
        self.assertEqual(
            nombre.json(), {"code": "ANT_DOCUMENT_INVALID", "detail": "El nombre del archivo no es válido."}
        )
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)

    def test_el_operador_no_ve_lo_extraido_y_el_revisor_si(self):
        verificacion, _ = self.verificacion_lista()
        self.cargar(verificacion, crear_pdf(lineas_certificado(resultado="SI")))
        url = reverse("antecedentes:api-detalle", args=[verificacion.pk])

        self.client.force_login(self.operador)
        operador = self.client.get(url)
        self.client.force_login(self.revisor2)
        revisor = self.client.get(url)

        nuevos = {"resultado_extraido", "fecha_emision", "vigente_hasta_extraida", "firma_detectada", "codigo_error"}
        doc_operador = operador.json()["consultas_proveedor"][0]["documentos"][0]
        doc_revisor = revisor.json()["consultas_proveedor"][0]["documentos"][0]
        self.assertTrue(nuevos.isdisjoint(doc_operador))
        self.assertTrue(nuevos <= set(doc_revisor))
        self.assertEqual(doc_revisor["resultado_extraido"], "SI")
        self.assertEqual(doc_revisor["fecha_emision"], "2026-10-19")
        self.assertEqual(doc_revisor["vigente_hasta_extraida"], "2027-04-18")
        self.assertFalse(doc_revisor["firma_detectada"])
        for reservado in ("1111111111", "EVALUADA PERSONA", "MI-2026-004455"):
            self.assertNotIn(reservado, operador.content.decode())


class DescargarDocumentoTests(PipelineCertificadoBase):
    def setUp(self):
        super().setUp()
        self.otra_org = Organizacion.objects.create(nombre="Otra organización de PDF")
        self.verificacion, self.consulta = self.verificacion_lista()
        self.contenido = crear_pdf(lineas_certificado())
        self.documento, _ = self.cargar(self.verificacion, self.contenido, nombre="mi certificado.pdf")
        self.url = reverse("antecedentes:descargar-documento", args=[self.documento.pk])

    def leer(self, respuesta):
        try:
            return b"".join(respuesta.streaming_content)
        finally:
            respuesta.close()

    def lecturas(self):
        return self.eventos("DESCARGAR_DOCUMENTO_ANTECEDENTES")

    def test_el_revisor_descarga_el_original_como_adjunto_y_queda_auditado(self):
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(self.url)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.leer(respuesta), self.contenido)
        self.assertEqual(respuesta["Content-Type"], "application/pdf")
        self.assertIn("attachment", respuesta["Content-Disposition"])
        self.assertIn("mi certificado.pdf", respuesta["Content-Disposition"])
        self.assertEqual(respuesta["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", respuesta["Cache-Control"])
        evento = self.lecturas().get()
        self.assertEqual(evento.actor, self.revisor2)
        self.assertEqual(evento.recurso_id, str(self.documento.pk))
        self.assertEqual(evento.organizacion, self.organizacion)
        self.assertEqual(evento.metadata["sha256"], self.documento.sha256)

    def test_el_revisor_tambien_descarga_un_documento_en_cuarentena(self):
        verificacion, _ = self.verificacion_lista(persona=self.crear_persona())
        basura = b"%PDF-1.4\nbasura\n%%EOF"
        with self.assertRaises(services.DocumentoInvalido) as contexto:
            self.cargar(verificacion, basura)
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:descargar-documento", args=[contexto.exception.documento.pk]))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.leer(respuesta), basura)
        self.assertEqual(self.lecturas().count(), 1)

    def test_el_operador_recibe_403_y_quienes_no_tienen_rol_en_la_organizacion_404_sin_auditar(self):
        garita = self.crear_usuario("garita-pdf@example.com", "seguridad_garita")
        ajeno = self.crear_usuario("ajeno-pdf@example.com", "revisor_antecedentes", self.otra_org)
        sin_roles = self.crear_usuario("sinroles-pdf@example.com")
        superusuario = Usuario.objects.create_superuser(email="super-pdf@example.com", password="x")
        esperado = {self.operador: 403, garita: 404, ajeno: 404, sin_roles: 404, superusuario: 404}
        for usuario, codigo in esperado.items():
            with self.subTest(usuario=usuario.email):
                self.client.force_login(usuario)

                respuesta = self.client.get(self.url)

                self.assertEqual(respuesta.status_code, codigo)
                self.assertFalse(respuesta.streaming)
        self.assertEqual(self.lecturas().count(), 0)

    def test_sin_sesion_redirige_al_ingreso_y_un_documento_inexistente_es_404(self):
        anonimo = self.client.get(self.url)
        self.client.force_login(self.revisor2)
        inexistente = self.client.get(
            reverse("antecedentes:descargar-documento", args=["00000000-0000-0000-0000-000000000000"])
        )

        self.assertEqual(anonimo.status_code, 302)
        self.assertIn("login", anonimo["Location"])
        self.assertEqual(inexistente.status_code, 404)
        self.assertEqual(self.lecturas().count(), 0)

    def test_los_archivos_no_se_sirven_desde_media(self):
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(f"/{settings.MEDIA_URL}{self.documento.archivo.name}")

        self.assertEqual(respuesta.status_code, 404)


# --- T6: API pública y pantallas (S2, S4, S8, S10) -------------------------------------------------------


class UIyAPIBase(PipelineCertificadoBase):
    """Operador y revisores de la organización; usuarios sin acceso en otra organización."""

    def setUp(self):
        super().setUp()
        self.otra_org = Organizacion.objects.create(nombre="Otra organización de la API")
        self.garita = self.crear_usuario("garita-api@example.com", "seguridad_garita")
        self.ajeno = self.crear_usuario("ajeno-api@example.com", "revisor_antecedentes", self.otra_org)
        self.sin_roles = self.crear_usuario("sinroles-api@example.com")

    def con_certificado(self, persona=None, **lineas):
        """Verificación cargada por el operador con un certificado que coincide: AUTENTICIDAD_PENDIENTE."""
        verificacion, _ = self.verificacion_lista(persona=persona)
        self.cargar(verificacion, crear_pdf(lineas_certificado(**lineas)))
        verificacion.refresh_from_db()
        return verificacion

    def enviar_json(self, url, datos, usuario=None, **cabeceras):
        if usuario:
            self.client.force_login(usuario)
        return self.client.post(url, data=json.dumps(datos), content_type="application/json", **cabeceras)

    def lecturas(self, verificacion):
        return EventoAuditoria.objects.filter(accion="LEER_ANTECEDENTES", recurso_id=str(verificacion.pk))

    def bitacora_total(self):
        return EjecucionConector.objects.count()


class VerificacionesAPICrearTests(UIyAPIBase):
    url = "/api/v1/verificaciones-antecedentes"

    def test_crear_con_clave_de_idempotencia_responde_202_y_la_misma_clave_devuelve_la_operacion_con_200(self):
        datos = {"personaId": str(self.persona.pk), "finalidad": F.RENOVACION}
        self.autorizar(finalidad=F.RENOVACION)

        primera = self.enviar_json(self.url, datos, self.operador, HTTP_IDEMPOTENCY_KEY="clave-api-1")
        bitacora = self.bitacora_total()
        repetida = self.enviar_json(self.url, datos, HTTP_IDEMPOTENCY_KEY="clave-api-1")

        verificacion = VerificacionAntecedentes.objects.get()
        self.assertEqual(primera.status_code, 202)
        self.assertEqual(primera.json()["id"], str(verificacion.pk))
        self.assertEqual(primera.json()["estado"], E.LISTA)
        self.assertEqual(verificacion.clave_idempotencia, "clave-api-1")
        self.assertEqual(verificacion.finalidad, F.RENOVACION)
        self.assertEqual(verificacion.solicitante, self.operador)
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(repetida.json()["id"], primera.json()["id"])
        self.assertEqual(self.bitacora_total(), bitacora)

    def test_sin_finalidad_ni_clave_usa_la_habilitacion_de_ingreso_y_genera_la_clave(self):
        self.autorizar()

        respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)}, self.operador)

        verificacion = VerificacionAntecedentes.objects.get()
        self.assertEqual(respuesta.status_code, 202)
        self.assertEqual(verificacion.finalidad, F.HABILITACION_INGRESO)
        self.assertEqual(respuesta.json()["finalidad"], F.HABILITACION_INGRESO)
        self.assertTrue(verificacion.clave_idempotencia)

    def test_sin_autorizacion_responde_el_codigo_de_autorizacion_requerida(self):
        respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)}, self.operador)

        self.assertEqual(respuesta.status_code, 202)
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo["estado"], E.AUTORIZACION_PENDIENTE)
        self.assertEqual(cuerpo["code"], "ANT_AUTHORIZATION_REQUIRED")
        self.assertEqual(cuerpo["accionRequerida"], "REGISTRAR_AUTORIZACION")
        self.assertIsNone(cuerpo["reintentarDesde"])

    def test_en_modo_asistido_el_caso_espera_al_humano_con_el_documento_enmascarado(self):
        self.autorizar()

        with override_settings(ANTECEDENTES_CONECTOR_MODE="asistido"):
            respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)}, self.operador)

        cuerpo = respuesta.json()
        self.assertEqual(respuesta.status_code, 202)
        self.assertEqual(cuerpo["estado"], "CAPTCHA_REQUERIDO")
        self.assertEqual(cuerpo["code"], "ANT_CAPTCHA_REQUIRED")
        self.assertEqual(cuerpo["accionRequerida"], "INTERVENCION_HUMANA")
        self.assertEqual(cuerpo["numeroDocumentoEnmascarado"], "******1111")
        self.assertNotIn("1111111111", respuesta.content.decode())

    def test_entradas_invalidas_se_rechazan_con_400_y_no_crean_nada(self):
        casos = {
            "sin persona": ({}, {}),
            "persona que no es un uuid": ({"personaId": "abc"}, {}),
            "finalidad desconocida": ({"personaId": str(self.persona.pk), "finalidad": "OTRA"}, {}),
            "clave demasiado larga": ({"personaId": str(self.persona.pk)}, {"HTTP_IDEMPOTENCY_KEY": "x" * 65}),
        }
        self.client.force_login(self.operador)
        for nombre, (datos, cabeceras) in casos.items():
            with self.subTest(nombre):
                respuesta = self.enviar_json(self.url, datos, **cabeceras)

                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()["code"], "ANT_REQUEST_INVALID")
                self.assertTrue(respuesta.json()["detail"])
        self.assertEqual(VerificacionAntecedentes.objects.count(), 0)

    def test_clave_usada_por_otra_persona_responde_400_sin_crear_nada(self):
        otra = self.crear_persona()
        self.autorizar(persona=otra)
        self.autorizar()
        self.enviar_json(self.url, {"personaId": str(otra.pk)}, self.operador, HTTP_IDEMPOTENCY_KEY="compartida")

        respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)}, HTTP_IDEMPOTENCY_KEY="compartida")

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["code"], "ANT_REQUEST_INVALID")
        self.assertEqual(VerificacionAntecedentes.objects.count(), 1)

    def test_sin_rol_en_la_organizacion_la_persona_no_existe_y_no_se_crea_nada(self):
        usuarios = {"garita": self.garita, "revisor de otra organización": self.ajeno, "sin roles": self.sin_roles}
        for nombre, usuario in usuarios.items():
            with self.subTest(nombre):
                respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)}, usuario)

                self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(VerificacionAntecedentes.objects.count(), 0)

    def test_sin_sesion_no_hay_acceso(self):
        self.client.logout()

        respuesta = self.enviar_json(self.url, {"personaId": str(self.persona.pk)})

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(VerificacionAntecedentes.objects.count(), 0)


class VerificacionesAPIDetalleTests(UIyAPIBase):
    def url(self, verificacion):
        return f"/api/v1/verificaciones-antecedentes/{verificacion.pk}"

    def test_el_operador_no_recibe_el_resultado_y_el_revisor_si_y_cada_lectura_se_audita(self):
        verificacion = self.con_certificado(resultado="SI")

        self.client.force_login(self.operador)
        operador = self.client.get(self.url(verificacion))
        self.client.force_login(self.revisor2)
        revisor = self.client.get(self.url(verificacion))

        self.assertEqual((operador.status_code, revisor.status_code), (200, 200))
        self.assertNotIn("resultado", operador.json())
        self.assertEqual(revisor.json()["resultado"], ResultadoAntecedentes.SI)
        self.assertEqual(operador.json()["estado"], E.AUTENTICIDAD_PENDIENTE)
        lecturas = list(self.lecturas(verificacion).order_by("created_at"))
        self.assertEqual([lectura.actor_id for lectura in lecturas], [self.operador.pk, self.revisor2.pk])
        self.assertEqual([lectura.metadata["resultado_visible"] for lectura in lecturas], [False, True])

    def test_sin_rol_en_la_organizacion_responde_404_y_no_audita_ninguna_lectura(self):
        verificacion = self.con_certificado()
        for nombre, usuario in {"garita": self.garita, "ajeno": self.ajeno, "sin roles": self.sin_roles}.items():
            with self.subTest(nombre):
                self.client.force_login(usuario)

                respuesta = self.client.get(self.url(verificacion))

                self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(self.lecturas(verificacion).count(), 0)

    def test_fallida_informa_el_codigo_de_la_fuente_y_desde_cuando_reintentar(self):
        self.autorizar()
        self.sondeo.return_value = caido()
        verificacion, _ = self.iniciar("asistido")
        consulta = verificacion.consultas_proveedor.get()
        self.client.force_login(self.operador)

        respuesta = self.client.get(self.url(verificacion))

        cuerpo = respuesta.json()
        self.assertEqual((cuerpo["estado"], cuerpo["code"]), (E.FALLIDA, "ANT_SOURCE_UNAVAILABLE"))
        self.assertEqual(cuerpo["accionRequerida"], "CARGA_MANUAL")
        self.assertEqual(
            datetime.fromisoformat(cuerpo["reintentarDesde"]),
            max(consulta.proximo_intento_at, SaludFuente.objects.get().abierto_hasta),
        )

    def test_fallida_por_limite_de_consultas_informa_ant_rate_limited(self):
        self.autorizar()
        self.sondeo.return_value = ResultadoSondeo(
            ClaseRespuesta.LIMITADA, codigo_http=429, latencia_ms=50, evidencia="HTTP 429 · 50 ms", reintentar_tras=3600
        )
        verificacion, _ = self.iniciar("asistido")
        self.client.force_login(self.operador)

        cuerpo = self.client.get(self.url(verificacion)).json()

        self.assertEqual((cuerpo["estado"], cuerpo["code"]), (E.FALLIDA, "ANT_RATE_LIMITED"))
        self.assertTrue(cuerpo["reintentarDesde"])

    def test_fallida_sin_espera_pendiente_deja_reintentar_desde_en_null(self):
        verificacion, consulta = self.verificacion_lista(E.FALLIDA)
        consulta.codigo_error = "ANT_SOURCE_UNAVAILABLE"
        consulta.proximo_intento_at = timezone.now() - timedelta(minutes=1)
        consulta.save()
        self.client.force_login(self.operador)

        cuerpo = self.client.get(self.url(verificacion)).json()

        self.assertEqual(cuerpo["code"], "ANT_SOURCE_UNAVAILABLE")
        self.assertIsNone(cuerpo["reintentarDesde"])

    def test_un_caso_que_no_espera_a_nadie_no_trae_codigo_ni_accion(self):
        verificacion = self.con_certificado()
        self.client.force_login(self.operador)

        cuerpo = self.client.get(self.url(verificacion)).json()

        self.assertEqual((cuerpo["code"], cuerpo["accionRequerida"], cuerpo["reintentarDesde"]), (None, None, None))


class VerificacionesAPIContinuarTests(UIyAPIBase):
    def url(self, verificacion):
        return f"/api/v1/verificaciones-antecedentes/{verificacion.pk}/continuar"

    def test_continuar_devuelve_la_url_del_portal_y_pasa_a_en_consulta(self):
        verificacion, _ = self.verificacion_lista(E.CAPTCHA_REQUERIDO)
        self.client.force_login(self.operador)

        respuesta = self.client.post(self.url(verificacion))

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["urlPortal"], ConectorAsistido.url_portal)
        self.assertEqual(respuesta.json()["estado"], E.EN_CONSULTA)
        self.assertEqual(respuesta.json()["accionRequerida"], "INTERVENCION_HUMANA")
        self.assertEqual(verificacion.estado, E.EN_CONSULTA)

    def test_continuar_en_un_estado_que_no_lo_admite_responde_400_sin_cambios(self):
        verificacion, _ = self.verificacion_lista(E.LISTA)
        bitacora = self.bitacora_total()
        self.client.force_login(self.operador)

        respuesta = self.client.post(self.url(verificacion))

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["code"], "ANT_REQUEST_INVALID")
        self.assertEqual(verificacion.estado, E.LISTA)
        self.assertEqual(self.bitacora_total(), bitacora)

    def test_sin_rol_en_la_organizacion_continuar_responde_404_y_no_cambia_nada(self):
        verificacion, _ = self.verificacion_lista(E.CAPTCHA_REQUERIDO)
        self.client.force_login(self.garita)

        respuesta = self.client.post(self.url(verificacion))

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)


class VerificacionesAPIDocumentoTests(UIyAPIBase):
    def url(self, verificacion):
        return f"/api/v1/verificaciones-antecedentes/{verificacion.pk}/documento"

    def subir(self, verificacion, contenido, usuario=None, nombre="certificado.pdf"):
        if usuario:
            self.client.force_login(usuario)
        return self.client.post(self.url(verificacion), {"archivo": self.pdf_subido(contenido, nombre)})

    def test_carga_valida_responde_201_el_reintento_200_y_el_operador_no_ve_lo_extraido(self):
        verificacion, _ = self.verificacion_lista(E.CAPTCHA_REQUERIDO)
        contenido = crear_pdf(lineas_certificado(resultado="SI"))

        primera = self.subir(verificacion, contenido, self.operador)
        repetida = self.subir(verificacion, contenido)

        documento = DocumentoAntecedentes.objects.get()
        verificacion.refresh_from_db()
        self.assertEqual(primera.status_code, 201)
        self.assertEqual(primera.json()["documentoId"], str(documento.pk))
        self.assertEqual(primera.json()["estado"], E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(repetida.json()["documentoId"], str(documento.pk))
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(verificacion.cargado_por, self.operador)
        for reservado in ("MI-2026-004455", "EVALUADA PERSONA", '"resultado"'):
            self.assertNotIn(reservado, primera.content.decode())

    def test_pdf_invalido_responde_400_con_codigo_y_deja_el_caso_como_estaba(self):
        verificacion, _ = self.verificacion_lista(E.CAPTCHA_REQUERIDO)

        respuesta = self.subir(verificacion, b"%PDF-1.4\nbasura\n%%EOF", self.operador)

        documento = DocumentoAntecedentes.objects.get()
        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["code"], "ANT_DOCUMENT_INVALID")
        self.assertTrue(respuesta.json()["detail"])
        self.assertEqual(documento.estado_validacion, "CUARENTENA")
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)

    def test_identidad_distinta_responde_201_con_el_codigo_y_sin_revelar_lo_extraido(self):
        verificacion, _ = self.verificacion_lista()

        respuesta = self.subir(
            verificacion, crear_pdf(lineas_certificado(identificacion="1799999999", nombres="OTRA PERSONA")), self.operador
        )

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 201)
        self.assertEqual(respuesta.json()["code"], "ANT_IDENTITY_MISMATCH")
        self.assertEqual(respuesta.json()["estado"], E.REQUIERE_REVISION)
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        for reservado in ("1799999999", "OTRA PERSONA"):
            self.assertNotIn(reservado, respuesta.content.decode())

    def test_sin_archivo_o_en_un_estado_que_no_admite_carga_responde_400_sin_guardar(self):
        lista, _ = self.verificacion_lista()
        en_revision, _ = self.verificacion_lista(E.REQUIERE_REVISION, persona=self.crear_persona())
        self.client.force_login(self.operador)

        ausente = self.client.post(self.url(lista), {})
        estado = self.subir(en_revision, crear_pdf(lineas_certificado()))

        self.assertEqual(ausente.status_code, 400)
        self.assertEqual(ausente.json()["code"], "ANT_REQUEST_INVALID")
        self.assertEqual(estado.status_code, 400)
        self.assertEqual(estado.json()["code"], "ANT_REQUEST_INVALID")
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)

    def test_sin_rol_en_la_organizacion_la_carga_responde_404_sin_guardar(self):
        verificacion, _ = self.verificacion_lista()

        respuesta = self.subir(verificacion, crear_pdf(lineas_certificado()), self.garita)

        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)


class VerificacionesAPIRevisionTests(UIyAPIBase):
    def url(self, verificacion):
        return f"/api/v1/verificaciones-antecedentes/{verificacion.pk}/revision"

    def datos(self, **cambios):
        datos = {
            "decision": E.VALIDADA,
            "comentario": "Coincide con el original.",
            "vigenteHasta": (date.today() + timedelta(days=30)).isoformat(),
        }
        datos.update(cambios)
        return datos

    def test_el_revisor_valida_y_queda_registrada_la_decision(self):
        verificacion = self.con_certificado()

        respuesta = self.enviar_json(self.url(verificacion), self.datos(), self.revisor2)

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["estado"], E.VALIDADA)
        self.assertEqual(respuesta.json()["resultado"], ResultadoAntecedentes.NO)
        self.assertEqual(verificacion.estado, E.VALIDADA)
        self.assertEqual(verificacion.validado_por, self.revisor2)
        self.assertEqual(verificacion.vigente_hasta, date.today() + timedelta(days=30))
        self.assertEqual(verificacion.comentario, "Coincide con el original.")

    def test_el_operador_recibe_403_y_el_caso_no_cambia(self):
        verificacion = self.con_certificado()
        bitacora = self.bitacora_total()

        respuesta = self.enviar_json(self.url(verificacion), self.datos(), self.operador)

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(respuesta.json()["code"], "ANT_FORBIDDEN")
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(self.bitacora_total(), bitacora)

    def test_sin_rol_en_la_organizacion_responde_404_y_no_cambia_nada(self):
        verificacion = self.con_certificado()
        for nombre, usuario in {"garita": self.garita, "ajeno": self.ajeno}.items():
            with self.subTest(nombre):
                respuesta = self.enviar_json(self.url(verificacion), self.datos(), usuario)

                self.assertEqual(respuesta.status_code, 404)
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)

    def test_cuatro_ojos_quien_cargo_el_documento_no_puede_validarlo(self):
        verificacion, _ = self.verificacion_lista()
        self.cargar(verificacion, crear_pdf(lineas_certificado()), actor=self.revisor2)

        respuesta = self.enviar_json(self.url(verificacion), self.datos(), self.revisor2)

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["code"], "ANT_REQUEST_INVALID")
        self.assertEqual(respuesta.json()["detail"], "Quien cargó el documento no puede validarlo.")
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertIsNone(verificacion.validado_por)

    def test_decisiones_incompletas_o_invalidas_responden_400_sin_cambios(self):
        verificacion = self.con_certificado()
        casos = {
            # Sin vigenteHasta se usa la del certificado (S9); extenderla sí es inválido.
            "vigencia posterior a la del certificado": self.datos(vigenteHasta="2027-05-01"),
            "fecha mal escrita": self.datos(vigenteHasta="mañana"),
            "decisión desconocida": self.datos(decision="APROBADA"),
            "anular sin comentario": self.datos(decision=E.CANCELADA, comentario=""),
            "sin decisión": {"comentario": "x"},
        }
        for nombre, datos in casos.items():
            with self.subTest(nombre):
                respuesta = self.enviar_json(self.url(verificacion), datos, self.revisor2)

                self.assertEqual(respuesta.status_code, 400)
                self.assertEqual(respuesta.json()["code"], "ANT_REQUEST_INVALID")
        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertIsNone(verificacion.validado_por)

    def test_devolver_para_una_nueva_carga_con_motivo(self):
        verificacion = self.con_certificado()

        respuesta = self.enviar_json(
            self.url(verificacion), {"decision": E.LISTA, "comentario": "Ilegible, carga otro."}, self.revisor2
        )

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(verificacion.estado, E.LISTA)


class BandejaFiltrosTests(UIyAPIBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.operador)

    def caso(self, estado, nombres, **campos):
        persona = Persona.objects.create(
            organizacion=self.organizacion, tipo_documento=TipoDocumento.CEDULA,
            numero_documento=str(next(_numeros)), nombres=nombres, apellidos="Bandeja",
        )
        return self.crear_verificacion(estado=estado, persona=persona, **campos)

    def conteo(self, respuesta, estado):
        patron = rf'data-estado="{estado}"[^>]*data-conteo="(\d+)"'
        return int(re.search(patron, respuesta.content.decode()).group(1))

    def test_la_bandeja_filtra_por_estado_y_muestra_los_conteos_de_cada_pestana(self):
        self.caso(E.LISTA, "Lista1")
        self.caso(E.LISTA, "Lista2")
        self.caso(E.FALLIDA, "Fallida1")
        self.caso(E.VALIDADA, "Validada1")

        todas = self.client.get(reverse("antecedentes:bandeja"))
        fallidas = self.client.get(reverse("antecedentes:bandeja"), {"estado": "FALLIDA"})

        for nombre in ("Lista1", "Lista2", "Fallida1"):
            self.assertContains(todas, nombre)
        self.assertNotContains(todas, "Validada1")
        self.assertContains(fallidas, "Fallida1")
        for nombre in ("Lista1", "Lista2"):
            self.assertNotContains(fallidas, nombre)
        for respuesta in (todas, fallidas):
            self.assertEqual(self.conteo(respuesta, "LISTA"), 2)
            self.assertEqual(self.conteo(respuesta, "FALLIDA"), 1)
            self.assertEqual(self.conteo(respuesta, "REQUIERE_REVISION"), 0)
        self.assertContains(fallidas, 'aria-current="page" data-estado="FALLIDA"')

    def test_un_estado_desconocido_o_fuera_de_la_bandeja_muestra_todos_los_casos(self):
        self.caso(E.LISTA, "Lista1")
        self.caso(E.FALLIDA, "Fallida1")

        for valor in ("NO_EXISTE", "VALIDADA"):
            with self.subTest(valor):
                respuesta = self.client.get(reverse("antecedentes:bandeja"), {"estado": valor})

                self.assertContains(respuesta, "Lista1")
                self.assertContains(respuesta, "Fallida1")

    def test_los_conteos_solo_incluyen_las_organizaciones_del_usuario(self):
        self.caso(E.LISTA, "Propia")
        ajena = self.crear_persona(self.otra_org)
        self.crear_verificacion(estado=E.LISTA, persona=ajena)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        self.assertEqual(self.conteo(respuesta, "LISTA"), 1)

    def test_la_bandeja_nunca_muestra_resultados_ni_al_revisor(self):
        self.caso(
            E.REQUIERE_REVISION, "Reservado", resultado=ResultadoAntecedentes.SI, numero_certificado="CERT-BANDEJA-77"
        )
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        self.assertContains(respuesta, "Reservado")
        for reservado in ("CERT-BANDEJA-77", "hallazgos"):
            self.assertNotContains(respuesta, reservado)

    def test_una_fallida_indica_a_partir_de_que_hora_se_podra_reintentar(self):
        verificacion = self.caso(E.FALLIDA, "Esperando")
        consulta = ConsultaProveedor.objects.create(
            verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR,
            proximo_intento_at=timezone.now() + timedelta(minutes=30),
        )
        listo = self.caso(E.FALLIDA, "SinEspera")
        ConsultaProveedor.objects.create(verificacion=listo, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)

        respuesta = self.client.get(reverse("antecedentes:bandeja"))

        hora = timezone.localtime(consulta.proximo_intento_at).strftime("%H:%M")
        self.assertContains(respuesta, f"Podrás reintentar después de las {hora}")
        self.assertContains(respuesta, "Ya puedes reintentar")
        self.assertEqual(respuesta.content.decode().count("Podrás reintentar después de las"), 1)


class DetalleCargaCertificadoTests(UIyAPIBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.operador)
        self.verificacion, _ = self.verificacion_lista()
        self.url = reverse("antecedentes:detalle", args=[self.verificacion.pk])

    def subir(self, contenido, nombre="certificado.pdf"):
        return self.client.post(
            self.url, {"accion": "cargar_certificado", "archivo": self.pdf_subido(contenido, nombre)}, follow=True
        )

    def test_el_detalle_ofrece_la_zona_de_arrastre_con_el_selector_de_archivo_como_alternativa(self):
        respuesta = self.client.get(self.url)

        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertContains(respuesta, 'value="cargar_certificado"')
        self.assertContains(respuesta, 'type="file"')
        self.assertContains(respuesta, 'accept="application/pdf"')
        self.assertContains(respuesta, "data-zona-carga")
        self.assertContains(respuesta, "antecedentes/carga.js")
        # La contingencia manual sigue disponible.
        self.assertContains(respuesta, 'value="cargar_resultado"')

    def test_la_zona_de_carga_solo_aparece_en_los_estados_que_admiten_carga(self):
        for estado in maquina_estados.ESTADOS_CARGA_MANUAL:
            with self.subTest(estado):
                caso, _ = self.verificacion_lista(estado)

                respuesta = self.client.get(reverse("antecedentes:detalle", args=[caso.pk]))

                self.assertContains(respuesta, 'value="cargar_certificado"')
        for estado in (E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE, E.VALIDADA, E.CANCELADA):
            with self.subTest(estado):
                caso, _ = self.verificacion_lista(estado)

                respuesta = self.client.get(reverse("antecedentes:detalle", args=[caso.pk]))

                self.assertNotContains(respuesta, 'value="cargar_certificado"')

    def test_una_carga_valida_avanza_el_caso_sin_mostrar_el_resultado_al_operador(self):
        respuesta = self.subir(crear_pdf(lineas_certificado(resultado="SI")))

        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, E.AUTENTICIDAD_PENDIENTE)
        self.assertEqual(self.verificacion.cargado_por, self.operador)
        self.assertContains(respuesta, "Certificado recibido")
        for reservado in ("MI-2026-004455", "EVALUADA PERSONA", "hallazgos", "Registra antecedentes"):
            self.assertNotContains(respuesta, reservado)

    def test_un_pdf_invalido_muestra_el_motivo_y_deja_el_caso_como_estaba(self):
        respuesta = self.subir(b"%PDF-1.4\nbasura\n%%EOF")

        self.verificacion.refresh_from_db()
        documento = DocumentoAntecedentes.objects.get()
        self.assertEqual(self.verificacion.estado, E.LISTA)
        self.assertEqual(documento.estado_validacion, "CUARENTENA")
        self.assertContains(respuesta, documento.detalle_validacion.split(" Se conserva")[0])
        self.assertContains(respuesta, "cuarentena")

    def test_una_identidad_distinta_avisa_al_operador_sin_revelar_lo_extraido(self):
        respuesta = self.subir(crear_pdf(lineas_certificado(identificacion="1799999999", nombres="OTRA PERSONA")))

        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, E.REQUIERE_REVISION)
        self.assertContains(respuesta, "no coincide con la identidad de la persona")
        for reservado in ("1799999999", "OTRA PERSONA"):
            self.assertNotContains(respuesta, reservado)

    def test_un_resultado_ambiguo_avisa_que_pasa_a_revision(self):
        respuesta = self.subir(crear_pdf(lineas_certificado(resultado="")))

        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, E.REQUIERE_REVISION)
        self.assertContains(respuesta, "No se pudo reconocer el resultado")

    def test_repetir_el_mismo_archivo_avisa_que_ya_estaba_cargado(self):
        contenido = crear_pdf(lineas_certificado())
        self.subir(contenido)

        respuesta = self.subir(contenido)

        self.assertContains(respuesta, "ya estaba cargado")
        self.assertEqual(DocumentoAntecedentes.objects.count(), 1)

    def test_sin_archivo_o_con_otra_extension_muestra_el_error_sin_guardar(self):
        sin_archivo = self.client.post(self.url, {"accion": "cargar_certificado"}, follow=True)
        extension = self.subir(crear_pdf(lineas_certificado()), nombre="certificado.exe")

        self.assertContains(sin_archivo, "Selecciona el certificado en PDF")
        self.assertContains(extension, "El nombre del archivo no es válido.")
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)
        self.verificacion.refresh_from_db()
        self.assertEqual(self.verificacion.estado, E.LISTA)

    def test_quien_no_opera_antecedentes_recibe_403_y_no_se_guarda_nada(self):
        self.client.force_login(self.garita)

        respuesta = self.client.post(
            self.url, {"accion": "cargar_certificado", "archivo": self.pdf_subido(crear_pdf(lineas_certificado()))}
        )

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(DocumentoAntecedentes.objects.count(), 0)


class DetalleRevisorTests(UIyAPIBase):
    def test_el_revisor_ve_los_datos_extraidos_el_enlace_de_descarga_y_el_formulario_de_decision(self):
        verificacion = self.con_certificado(resultado="SI")
        documento = DocumentoAntecedentes.objects.get()
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        for dato in (
            "MI-2026-004455", "19/10/2026", "18/04/2027", "1111111111", "EVALUADA PERSONA",
            "Sí (hallazgos)", "Firma detectada",
        ):
            self.assertContains(respuesta, dato)
        self.assertContains(respuesta, reverse("antecedentes:descargar-documento", args=[documento.pk]))
        self.assertContains(respuesta, 'value="validar"')

    def test_el_revisor_ve_el_codigo_de_error_de_un_certificado_con_otra_identidad(self):
        verificacion, _ = self.verificacion_lista()
        self.cargar(verificacion, crear_pdf(lineas_certificado(identificacion="1799999999", nombres="OTRA PERSONA")))
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(respuesta, "ANT_IDENTITY_MISMATCH")
        self.assertContains(respuesta, "1799999999")

    def test_el_operador_no_ve_el_panel_del_certificado_ni_el_enlace_de_descarga(self):
        verificacion = self.con_certificado(resultado="SI")
        documento = DocumentoAntecedentes.objects.get()
        self.client.force_login(self.operador)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertNotContains(respuesta, reverse("antecedentes:descargar-documento", args=[documento.pk]))
        for reservado in ("MI-2026-004455", "EVALUADA PERSONA", "Firma detectada", "hallazgos"):
            self.assertNotContains(respuesta, reservado)

    def test_el_revisor_que_cargo_el_documento_ve_los_datos_pero_no_puede_decidir(self):
        verificacion, _ = self.verificacion_lista()
        self.cargar(verificacion, crear_pdf(lineas_certificado()), actor=self.revisor2)
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(respuesta, "MI-2026-004455")
        self.assertNotContains(respuesta, 'value="validar"')
        self.assertContains(respuesta, "Quien cargó el documento no puede validarlo")


class RevocarAutorizacionUITests(UIyAPIBase):
    def setUp(self):
        super().setUp()
        self.autorizacion = self.autorizar()
        self.verificacion, _ = self.iniciar("manual")
        self.url = reverse("antecedentes:revocar-autorizacion", args=[self.verificacion.pk])
        self.client.force_login(self.operador)

    def test_el_detalle_ofrece_revocar_mientras_la_autorizacion_siga_vigente(self):
        con_boton = self.client.get(reverse("antecedentes:detalle", args=[self.verificacion.pk]))
        services.revocar_autorizacion(
            autorizacion=self.autorizacion, motivo="La persona retiró su consentimiento.", actor=self.operador
        )
        sin_boton = self.client.get(reverse("antecedentes:detalle", args=[self.verificacion.pk]))

        self.assertContains(con_boton, self.url)
        self.assertNotContains(sin_boton, self.url)

    def test_el_modal_pide_el_motivo(self):
        respuesta = self.client.get(self.url, HTTP_HX_REQUEST="true")

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'name="motivo"')
        self.assertContains(respuesta, "Revocar autorización")

    def test_revocar_con_motivo_anula_el_caso_y_queda_auditado(self):
        respuesta = self.client.post(
            self.url, {"motivo": "La persona retiró su consentimiento."}, HTTP_HX_REQUEST="true"
        )

        self.autorizacion.refresh_from_db()
        self.verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["HX-Redirect"], reverse("antecedentes:detalle", args=[self.verificacion.pk]))
        self.assertIsNotNone(self.autorizacion.revocada_en)
        self.assertEqual(self.autorizacion.motivo_revocacion, "La persona retiró su consentimiento.")
        self.assertEqual(self.verificacion.estado, E.CANCELADA)
        evento = EventoAuditoria.objects.get(accion="AUTORIZACION_REVOCADA")
        self.assertEqual(evento.actor_id, self.operador.pk)

    def test_revocar_sin_motivo_muestra_el_error_y_no_cambia_nada(self):
        respuesta = self.client.post(self.url, {"motivo": "   "}, HTTP_HX_REQUEST="true")

        self.autorizacion.refresh_from_db()
        self.verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("HX-Redirect", respuesta)
        self.assertContains(respuesta, "motivo")
        self.assertIsNone(self.autorizacion.revocada_en)
        self.assertEqual(self.verificacion.estado, E.LISTA)
        self.assertEqual(EventoAuditoria.objects.filter(accion="AUTORIZACION_REVOCADA").count(), 0)

    def test_una_autorizacion_ya_revocada_se_rechaza_con_mensaje(self):
        services.revocar_autorizacion(autorizacion=self.autorizacion, motivo="Primera vez.", actor=self.operador)

        respuesta = self.client.post(self.url, {"motivo": "Otra vez."}, HTTP_HX_REQUEST="true")

        self.assertNotIn("HX-Redirect", respuesta)
        self.assertContains(respuesta, "ya fue revocada")
        self.autorizacion.refresh_from_db()
        self.assertEqual(self.autorizacion.motivo_revocacion, "Primera vez.")

    def test_sin_rol_en_la_organizacion_es_403_y_sin_htmx_redirige_al_detalle(self):
        self.client.force_login(self.garita)
        prohibido = self.client.post(self.url, {"motivo": "x"}, HTTP_HX_REQUEST="true")
        self.client.force_login(self.ajeno)
        ajeno = self.client.post(self.url, {"motivo": "x"}, HTTP_HX_REQUEST="true")
        self.client.force_login(self.operador)
        sin_htmx = self.client.get(self.url)

        self.assertEqual((prohibido.status_code, ajeno.status_code), (403, 403))
        self.assertRedirects(sin_htmx, reverse("antecedentes:detalle", args=[self.verificacion.pk]))
        self.autorizacion.refresh_from_db()
        self.assertIsNone(self.autorizacion.revocada_en)


class VigenciaDelCertificadoTests(UIyAPIBase):
    """S9 / ANT-012: la vigencia sale del certificado; el revisor no puede extenderla."""

    VALIDO_HASTA = date(2027, 4, 18)  # "Válido hasta el 18 de ABRIL del 2027" en lineas_certificado()

    def test_validar_sin_fecha_usa_la_vigencia_del_certificado(self):
        verificacion = self.con_certificado()

        services.validar_verificacion(verificacion=verificacion, revisor=self.revisor2, decision=E.VALIDADA)

        verificacion.refresh_from_db()
        self.assertEqual((verificacion.estado, verificacion.vigente_hasta), (E.VALIDADA, self.VALIDO_HASTA))

    def test_una_fecha_anterior_a_la_del_certificado_se_respeta(self):
        verificacion = self.con_certificado()

        services.validar_verificacion(
            verificacion=verificacion, revisor=self.revisor2, decision=E.VALIDADA, vigente_hasta=date(2027, 1, 31)
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.vigente_hasta, date(2027, 1, 31))

    def test_una_fecha_posterior_a_la_del_certificado_se_rechaza_sin_cambios(self):
        verificacion = self.con_certificado()

        with self.assertRaisesMessage(DomainError, "no puede superar la del certificado (18/04/2027)"):
            services.validar_verificacion(
                verificacion=verificacion, revisor=self.revisor2, decision=E.VALIDADA, vigente_hasta=date(2027, 5, 1)
            )

        verificacion.refresh_from_db()
        self.assertEqual((verificacion.estado, verificacion.vigente_hasta), (E.AUTENTICIDAD_PENDIENTE, None))

    def test_sin_certificado_ni_fecha_la_validacion_se_rechaza(self):
        verificacion = self.crear_verificacion(estado=E.REQUIERE_REVISION, cargado_por=self.operador)

        with self.assertRaisesMessage(DomainError, "hasta cuándo es vigente"):
            services.validar_verificacion(verificacion=verificacion, revisor=self.revisor2, decision=E.VALIDADA)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)

    def test_el_formulario_del_revisor_propone_la_vigencia_del_certificado(self):
        verificacion = self.con_certificado()
        self.client.force_login(self.revisor2)

        respuesta = self.client.get(reverse("antecedentes:detalle", args=[verificacion.pk]))

        self.assertContains(respuesta, 'value="2027-04-18"')

    def test_la_api_de_revision_acepta_validar_sin_vigente_hasta(self):
        verificacion = self.con_certificado()

        respuesta = self.enviar_json(
            reverse("antecedentes:api-v1-revision", args=[verificacion.pk]), {"decision": E.VALIDADA}, self.revisor2
        )

        verificacion.refresh_from_db()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(verificacion.vigente_hasta, self.VALIDO_HASTA)


class RenovacionTests(BaseAntecedentesTests):
    """ANT-012: nueva consulta antes de que venza, solo para quien sigue trabajando."""

    def setUp(self):
        super().setUp()
        self.empresa = EmpresaContratista.objects.create(
            organizacion=self.organizacion, razon_social="Contratista de renovación", ruc="1790000000777"
        )

    def validada(self, vence_en_dias, persona=None, asignada=True):
        persona = persona or self.crear_persona()
        if asignada:
            Asignacion.objects.create(
                organizacion=self.organizacion, persona=persona, empresa=self.empresa,
                cargo_o_actividad="Técnico", fecha_inicio=date.today(),
            )
        return self.crear_verificacion(
            estado=E.VALIDADA, persona=persona, resultado=ResultadoAntecedentes.NO,
            vigente_hasta=date.today() + timedelta(days=vence_en_dias),
        )

    def renovaciones(self, persona):
        return VerificacionAntecedentes.objects.filter(persona=persona, finalidad=F.RENOVACION)

    def test_programa_la_renovacion_de_lo_que_vence_dentro_del_plazo_sin_duplicarla(self):
        por_vencer = self.validada(vence_en_dias=10)

        primera = services.programar_renovaciones()
        segunda = services.programar_renovaciones()

        self.assertEqual((primera, segunda), (1, 0))
        renovacion = self.renovaciones(por_vencer.persona).get()
        # Sin autorización para esta finalidad no se consulta nada: espera la autorización.
        self.assertEqual(renovacion.estado, E.AUTORIZACION_PENDIENTE)
        por_vencer.refresh_from_db()
        self.assertEqual(por_vencer.estado, E.VALIDADA)

    def test_no_renueva_lo_que_vence_lejos_ni_a_quien_ya_no_tiene_asignacion_activa(self):
        lejana = self.validada(vence_en_dias=60)
        sin_asignacion = self.validada(vence_en_dias=5, asignada=False)

        self.assertEqual(services.programar_renovaciones(), 0)

        for verificacion in (lejana, sin_asignacion):
            self.assertFalse(self.renovaciones(verificacion.persona).exists())

    @override_settings(ANTECEDENTES_DIAS_RENOVACION=30)
    def test_el_plazo_de_anticipacion_es_configurable(self):
        a_25_dias = self.validada(vence_en_dias=25)

        self.assertEqual(services.programar_renovaciones(), 1)

        self.assertTrue(self.renovaciones(a_25_dias.persona).exists())

    def test_el_job_diario_programa_las_renovaciones(self):
        from apps.cumplimiento.jobs import procesar_vencimientos

        por_vencer = self.validada(vence_en_dias=3)

        resultado = procesar_vencimientos()

        self.assertEqual(resultado["renovaciones_antecedentes"], 1)
        self.assertTrue(self.renovaciones(por_vencer.persona).exists())
