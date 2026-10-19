from django.db import transaction
from django.utils import timezone

from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.factory import obtener_conector
from apps.antecedentes.conectores.manual import ConectorManual
from apps.antecedentes.models import (
    ConsultaProveedor,
    EstadoConsulta,
    EstadoVerificacion,
    IntentoConsulta,
    ModoConector,
    ProveedorAntecedentes,
    ResultadoAntecedentes,
    VerificacionAntecedentes,
)
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


def iniciar_verificacion(*, persona: Persona, actor) -> VerificacionAntecedentes:
    """
    SIN @transaction.atomic a propósito: las transiciones del conector y su
    bitácora deben persistir aunque una excepción posterior se propague, o un
    fallo quedaría sin rastro.
    """
    conector = obtener_conector()
    modo = ModoConector.MANUAL if isinstance(conector, ConectorManual) else ModoConector.ASISTIDO

    verificacion = VerificacionAntecedentes.objects.create(
        organizacion=persona.organizacion,
        persona=persona,
        estado=EstadoVerificacion.CREADA,
        modo_conector=modo,
    )
    conector.iniciar(verificacion, actor=actor)
    consulta = ConsultaProveedor.objects.create(
        verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR
    )
    if modo == ModoConector.ASISTIDO:
        transaction.on_commit(lambda: _encolar_consulta(consulta.pk))
    return verificacion


def _encolar_consulta(consulta_id):
    from apps.antecedentes.tasks import preparar_consulta_asistida

    preparar_consulta_asistida.delay(str(consulta_id))


@transaction.atomic
def cambiar_estado_consulta(
    *,
    consulta: ConsultaProveedor,
    nuevo_estado: str,
    registrar_intento: bool = False,
    detalle_error: str = "",
    codigo_error: str = "",
    proximo_intento_at=None,
) -> ConsultaProveedor:
    """ÚNICO escritor de `ConsultaProveedor.estado`; un intento de integración queda en `IntentoConsulta`."""
    if nuevo_estado not in EstadoConsulta.values:
        raise DomainError(f"Estado de consulta inválido: {nuevo_estado}")

    consulta.estado = nuevo_estado
    consulta.requiere_intervencion = nuevo_estado == EstadoConsulta.CAPTCHA_REQUERIDO
    consulta.detalle_error = detalle_error
    consulta.codigo_error = codigo_error
    consulta.proximo_intento_at = proximo_intento_at
    if registrar_intento:
        consulta.intentos += 1
        consulta.ultimo_intento_at = timezone.now()
    consulta.save()

    if registrar_intento:
        IntentoConsulta.objects.create(
            consulta=consulta,
            numero=consulta.intentos,
            estado_resultante=nuevo_estado,
            codigo_error=codigo_error,
            detalle=detalle_error,
        )
    return consulta


@transaction.atomic
def registrar_resultado_manual(*, verificacion, actor, resultado, numero_certificado="", proveedor="", comentario=""):
    if resultado not in ResultadoAntecedentes.values:
        raise DomainError(f"Resultado inválido: {resultado}")

    # Sin comparación de identidad todavía: todo resultado manual pasa a revisión humana.
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.PDF_RECIBIDO,
        actor=actor,
        nota_bitacora="Resultado cargado manualmente.",
        resultado=resultado,
        numero_certificado=numero_certificado,
        proveedor=proveedor,
        comentario=comentario,
        fecha_resultado=timezone.now(),
        cargado_por=actor,
    )
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.REQUIERE_REVISION,
        actor=actor,
        nota_bitacora="Resultado manual pendiente de validación humana.",
    )
    for consulta in verificacion.consultas_proveedor.all():
        cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.COMPLETADA)
    return verificacion


@transaction.atomic
def validar_verificacion(*, verificacion, revisor, decision: str, comentario="", vigente_hasta=None):
    decisiones_validas = {EstadoVerificacion.VALIDADA, EstadoVerificacion.LISTA, EstadoVerificacion.CANCELADA}
    if decision not in decisiones_validas:
        raise DomainError(f"Decisión inválida: {decision}")
    if verificacion.estado not in maquina_estados.ESTADOS_DECISION:
        raise DomainError(f"La verificación en estado '{verificacion.estado}' no espera una decisión de revisión.")
    if decision == EstadoVerificacion.VALIDADA and not vigente_hasta:
        raise DomainError("Toda validación debe indicar hasta cuándo es vigente el resultado.")
    if decision != EstadoVerificacion.VALIDADA and not comentario.strip():
        raise DomainError("Devolver o anular una verificación requiere un comentario con el motivo.")

    campos = {"validado_por": revisor}
    if comentario:
        campos["comentario"] = comentario
    if decision == EstadoVerificacion.VALIDADA:
        campos["vigente_hasta"] = vigente_hasta

    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=decision,
        actor=revisor,
        nota_bitacora=f"Decisión de revisión: {decision}.",
        motivo=comentario,
        **campos,
    )
    return verificacion


def marcar_vencidas(actor=None) -> int:
    """Job en bloque para Celery-beat (Fase 9); por ahora, función normal."""
    hoy = timezone.now().date()
    total = 0
    for verificacion in VerificacionAntecedentes.objects.filter(
        estado=EstadoVerificacion.VALIDADA, vigente_hasta__lt=hoy
    ):
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.VENCIDA,
            actor=actor,
            nota_bitacora="Vencimiento detectado automáticamente.",
        )
        total += 1
    return total
