from apps.antecedentes.models import EjecucionConector, EstadoVerificacion
from apps.core.events import emitir_evento
from apps.core.exceptions import TransicionEstadoInvalida

E = EstadoVerificacion

TRANSICIONES: dict[str, set[str]] = {
    E.SOLICITADA: {E.CONTINGENCIA_MANUAL, E.EN_COLA, E.FALLIDA_TECNICA},
    E.EN_COLA: {E.EN_PROCESO},
    E.EN_PROCESO: {
        E.REINTENTANDO,
        E.FALLIDA_TECNICA,
        E.RESULTADO_SI_OBTENIDO,
        E.RESULTADO_NO_OBTENIDO,
        E.RESULTADO_NO_DETERMINADO,
    },
    E.REINTENTANDO: {E.EN_PROCESO, E.FALLIDA_TECNICA},
    E.FALLIDA_TECNICA: {E.CONTINGENCIA_MANUAL},
    E.RESULTADO_SI_OBTENIDO: {E.EN_REVISION_MANUAL},
    E.RESULTADO_NO_OBTENIDO: {E.EN_REVISION_MANUAL},
    E.RESULTADO_NO_DETERMINADO: {E.EN_REVISION_MANUAL},
    E.CONTINGENCIA_MANUAL: {E.EN_REVISION_MANUAL},
    E.EN_REVISION_MANUAL: {E.VALIDADA, E.OBSERVADA, E.RECHAZADA},
    E.VALIDADA: {E.VENCIDA},
    E.OBSERVADA: set(),
    E.RECHAZADA: set(),
    E.VENCIDA: set(),
}


def transicionar(*, verificacion, nuevo_estado: str, actor=None, nota_bitacora: str = "", **campos_modelo):
    permitidos = TRANSICIONES.get(verificacion.estado, set())
    if nuevo_estado not in permitidos:
        raise TransicionEstadoInvalida(f"No se puede pasar de '{verificacion.estado}' a '{nuevo_estado}'.")

    estado_anterior = verificacion.estado
    verificacion.estado = nuevo_estado
    for campo, valor in campos_modelo.items():
        setattr(verificacion, campo, valor)
    verificacion.save()

    EjecucionConector.objects.create(
        verificacion=verificacion,
        estado_resultante=nuevo_estado,
        exito=nuevo_estado != E.FALLIDA_TECNICA,
        detalle=nota_bitacora,
    )
    emitir_evento(
        actor=actor,
        accion="TRANSICION_ANTECEDENTES",
        recurso=verificacion,
        organizacion=verificacion.organizacion,
        motivo=f"{estado_anterior} -> {nuevo_estado}. {nota_bitacora}".strip(),
    )
    return verificacion
