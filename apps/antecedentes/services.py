from django.db import transaction
from django.utils import timezone

from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.factory import obtener_conector
from apps.antecedentes.conectores.manual import ConectorManual
from apps.antecedentes.models import EstadoVerificacion, ModoConector, ResultadoAntecedentes, VerificacionAntecedentes
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


def iniciar_verificacion(*, persona: Persona, actor) -> VerificacionAntecedentes:
    """
    SIN @transaction.atomic a propósito: si el conector falla, la
    transición a FALLIDA_TECNICA y su bitácora deben persistir ANTES de
    que la excepción se propague, o un fallo técnico quedaría sin rastro.
    """
    conector = obtener_conector()
    modo = ModoConector.MANUAL if isinstance(conector, ConectorManual) else ModoConector.ASISTIDO

    verificacion = VerificacionAntecedentes.objects.create(
        organizacion=persona.organizacion,
        persona=persona,
        estado=EstadoVerificacion.SOLICITADA,
        modo_conector=modo,
    )
    conector.iniciar(verificacion, actor=actor)
    return verificacion


@transaction.atomic
def registrar_resultado_manual(*, verificacion, actor, resultado, numero_certificado="", proveedor="", comentario=""):
    if resultado not in ResultadoAntecedentes.values:
        raise DomainError(f"Resultado inválido: {resultado}")

    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.EN_REVISION_MANUAL,
        actor=actor,
        nota_bitacora="Resultado cargado manualmente, pendiente de validación.",
        resultado=resultado,
        numero_certificado=numero_certificado,
        proveedor=proveedor,
        comentario=comentario,
        fecha_resultado=timezone.now(),
        cargado_por=actor,
    )
    return verificacion


@transaction.atomic
def validar_verificacion(*, verificacion, revisor, decision: str, comentario="", vigente_hasta=None):
    decisiones_validas = {EstadoVerificacion.VALIDADA, EstadoVerificacion.OBSERVADA, EstadoVerificacion.RECHAZADA}
    if decision not in decisiones_validas:
        raise DomainError(f"Decisión inválida: {decision}")
    if decision == EstadoVerificacion.VALIDADA and not vigente_hasta:
        raise DomainError("Toda validación debe indicar hasta cuándo es vigente el resultado.")
    if decision in {EstadoVerificacion.OBSERVADA, EstadoVerificacion.RECHAZADA} and not comentario:
        raise DomainError("Observar o rechazar requiere un comentario con el motivo.")

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
