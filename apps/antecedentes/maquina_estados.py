from apps.antecedentes.models import EjecucionConector, EstadoVerificacion
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError, TransicionEstadoInvalida

E = EstadoVerificacion

TERMINALES = {E.VALIDADA, E.VENCIDA, E.CANCELADA}

TRANSICIONES: dict[str, set[str]] = {
    E.CREADA: {E.AUTORIZACION_PENDIENTE, E.LISTA},
    E.AUTORIZACION_PENDIENTE: {E.LISTA},
    E.LISTA: {E.EN_CONSULTA},
    E.EN_CONSULTA: {E.CAPTCHA_REQUERIDO, E.RESULTADO_OBTENIDO, E.FALLIDA},
    E.CAPTCHA_REQUERIDO: {E.EN_CONSULTA},
    E.RESULTADO_OBTENIDO: {E.PDF_RECIBIDO},
    E.PDF_RECIBIDO: {E.DATOS_COINCIDENTES, E.REQUIERE_REVISION},
    E.DATOS_COINCIDENTES: {E.AUTENTICIDAD_PENDIENTE},
    E.AUTENTICIDAD_PENDIENTE: {E.VALIDADA, E.REQUIERE_REVISION},
    E.REQUIERE_REVISION: set(),
    E.FALLIDA: set(),
    E.VALIDADA: {E.VENCIDA},
    E.VENCIDA: set(),
    E.CANCELADA: set(),
}

# Extensiones sobre el diagrama de requerimientos:
# la carga manual siempre está disponible (S2), aun tras un fallo o un CAPTCHA.
for _origen in (E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.FALLIDA):
    TRANSICIONES[_origen].add(E.PDF_RECIBIDO)
# una decisión humana sobre REQUIERE_REVISION valida, pide una nueva carga o anula.
TRANSICIONES[E.REQUIERE_REVISION] |= {E.VALIDADA, E.LISTA}
# tras un fallo se puede abrir un nuevo intento.
TRANSICIONES[E.FALLIDA].add(E.LISTA)
# cualquier estado no terminal puede anularse con motivo.
for _origen in TRANSICIONES.keys() - TERMINALES:
    TRANSICIONES[_origen].add(E.CANCELADA)

# Estados que esperan una acción humana (bandeja).
# EN_CONSULTA incluido: tras abrir el portal, el caso espera que alguien cargue el certificado.
ESTADOS_BANDEJA = (E.AUTORIZACION_PENDIENTE, E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.PDF_RECIBIDO, E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE, E.FALLIDA)
# Estados desde los que el operador puede cargar el resultado a mano.
ESTADOS_CARGA_MANUAL = (E.LISTA, E.EN_CONSULTA, E.CAPTCHA_REQUERIDO, E.FALLIDA)
# Estados en los que el revisor decide.
ESTADOS_DECISION = (E.REQUIERE_REVISION, E.AUTENTICIDAD_PENDIENTE)


def transicionar(
    *, verificacion, nuevo_estado: str, actor=None, nota_bitacora: str = "", motivo: str = "", **campos_modelo
):
    permitidos = TRANSICIONES.get(verificacion.estado, set())
    if nuevo_estado not in permitidos:
        raise TransicionEstadoInvalida(f"No se puede pasar de '{verificacion.estado}' a '{nuevo_estado}'.")
    if nuevo_estado == E.CANCELADA:
        if not motivo.strip():
            raise DomainError("Anular la solicitud requiere indicar el motivo.")
        nota_bitacora = f"La solicitud fue anulada con motivo: {motivo.strip()} {nota_bitacora}".strip()

    estado_anterior = verificacion.estado
    verificacion.estado = nuevo_estado
    for campo, valor in campos_modelo.items():
        setattr(verificacion, campo, valor)
    verificacion.save()

    EjecucionConector.objects.create(
        verificacion=verificacion,
        estado_resultante=nuevo_estado,
        exito=nuevo_estado != E.FALLIDA,
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
