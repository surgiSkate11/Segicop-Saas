import importlib
from datetime import date, timedelta
from itertools import product

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accesos.services import calcular_habilitacion
from apps.antecedentes import maquina_estados, services
from apps.antecedentes.api import _validar_pdf_seguro
from apps.antecedentes.models import (
    ConsultaProveedor,
    EjecucionConector,
    EstadoConsulta,
    EstadoVerificacion,
    IntentoConsulta,
    ModoConector,
    ProveedorAntecedentes,
    ResultadoAntecedentes,
    VerificacionAntecedentes,
)
from apps.antecedentes.tasks import preparar_consulta_asistida
from apps.core.choices import EstadoCumplimiento
from apps.core.exceptions import DomainError, TransicionEstadoInvalida
from apps.core.models import EventoAuditoria
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Organizacion, Rol, RolAsignado, Usuario
from apps.personas.models import Asignacion, Persona, TipoDocumento

E = EstadoVerificacion

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


class BaseAntecedentesTests(TestCase):
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

    def crear_verificacion(self, estado=E.CREADA, **campos):
        return VerificacionAntecedentes.objects.create(
            organizacion=self.organizacion,
            persona=self.persona,
            modo_conector=ModoConector.MANUAL,
            estado=estado,
            **campos,
        )


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
        consulta = ConsultaProveedor.objects.create(
            verificacion=self.crear_verificacion(), proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
        )

        preparar_consulta_asistida(str(consulta.pk))
        segunda = preparar_consulta_asistida(str(consulta.pk))

        consulta.refresh_from_db()
        self.assertEqual(consulta.estado, EstadoConsulta.CAPTCHA_REQUERIDO)
        self.assertEqual(consulta.intentos, 1)
        self.assertEqual(IntentoConsulta.objects.filter(consulta=consulta).count(), 1)
        self.assertTrue(segunda["omitida"])


class IniciarVerificacionTests(BaseAntecedentesTests):
    def test_modo_manual_deja_la_verificacion_lista_con_una_consulta(self):
        with override_settings(ANTECEDENTES_CONECTOR_MODE="manual"):
            verificacion = services.iniciar_verificacion(persona=self.persona, actor=self.usuario)

        verificacion.refresh_from_db()
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
        with override_settings(ANTECEDENTES_CONECTOR_MODE="manual"):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                services.iniciar_verificacion(persona=self.persona, actor=self.usuario)

        self.assertEqual(callbacks, [])

    def test_modo_asistido_llega_a_captcha_requerido(self):
        with override_settings(ANTECEDENTES_CONECTOR_MODE="asistido"):
            verificacion = services.iniciar_verificacion(persona=self.persona, actor=self.usuario)

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.CAPTCHA_REQUERIDO)
        self.assertEqual(
            list(verificacion.ejecuciones.order_by("created_at").values_list("estado_resultante", flat=True)),
            [E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO],
        )

    def test_modo_asistido_aparece_en_la_bandeja(self):
        rol = Rol.objects.create(codigo="revisor-prueba", nombre="Revisor de prueba")
        RolAsignado.objects.create(usuario=self.usuario, organizacion=self.organizacion, rol=rol)
        with override_settings(ANTECEDENTES_CONECTOR_MODE="asistido"):
            verificacion = services.iniciar_verificacion(persona=self.persona, actor=self.usuario)
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
            {E.LISTA, E.CAPTCHA_REQUERIDO, E.PDF_RECIBIDO, E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE, E.FALLIDA},
        )


class FlujoManualTests(BaseAntecedentesTests):
    def iniciar_manual(self):
        with override_settings(ANTECEDENTES_CONECTOR_MODE="manual"):
            return services.iniciar_verificacion(persona=self.persona, actor=self.usuario)

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

        with override_settings(ANTECEDENTES_CONECTOR_MODE="manual"):
            verificacion = services.iniciar_verificacion(persona=self.persona, actor=self.usuario)
        self.assertEqual(verificacion.estado, E.LISTA)
        services.registrar_resultado_manual(
            verificacion=verificacion, actor=self.usuario, resultado=ResultadoAntecedentes.NO
        )
        self.assertEqual(verificacion.estado, E.REQUIERE_REVISION)
        self.assertFalse(calcular_habilitacion(self.persona).habilitado)
        services.validar_verificacion(
            verificacion=verificacion,
            revisor=self.usuario,
            decision=E.VALIDADA,
            vigente_hasta=date.today() + timedelta(days=30),
        )

        verificacion.refresh_from_db()
        self.assertEqual(verificacion.estado, E.VALIDADA)
        self.assertTrue(calcular_habilitacion(self.persona).habilitado)

    def test_detalle_muestra_acciones_segun_estado(self):
        rol = Rol.objects.create(codigo="revisor-prueba", nombre="Revisor de prueba")
        RolAsignado.objects.create(usuario=self.usuario, organizacion=self.organizacion, rol=rol)
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


class PdfSeguroTests(TestCase):
    def test_rechaza_archivo_no_pdf_aunque_use_extension_pdf(self):
        archivo = SimpleUploadedFile("documento.pdf", b"<script>alert(1)</script>")

        self.assertEqual(_validar_pdf_seguro(archivo), "El archivo no tiene una estructura PDF válida.")

    def test_rechaza_pdf_con_contenido_activo(self):
        archivo = SimpleUploadedFile(
            "documento.pdf",
            b"%PDF-1.7\n1 0 obj << /OpenAction 2 0 R >> endobj\n%%EOF",
        )

        self.assertEqual(
            _validar_pdf_seguro(archivo),
            "El PDF contiene acciones o archivos incrustados no permitidos.",
        )

    def test_acepta_pdf_minimo_sin_contenido_activo(self):
        archivo = SimpleUploadedFile("documento.pdf", b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF")

        self.assertIsNone(_validar_pdf_seguro(archivo))

    def test_rechaza_nombre_de_archivo_con_traversal_o_control(self):
        archivo = SimpleUploadedFile("../documento\x00.pdf", b"%PDF-1.7\n%%EOF")

        self.assertEqual(_validar_pdf_seguro(archivo), "El nombre del archivo no es válido.")
