from django.db import transaction
from django.utils import timezone

from apps.accesos.services import calcular_habilitacion
from apps.core.choices import EstadoCumplimiento
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError, TransicionEstadoInvalida
from apps.trabajos.models import EstadoTrabajo, PermisoTrabajo

_ESTADOS_HABILITANTES = {EstadoCumplimiento.HABILITADO, EstadoCumplimiento.HABILITADO_CON_CONDICIONES}

TRANSICIONES: dict[str, set[str]] = {
    EstadoTrabajo.SOLICITADO: {EstadoTrabajo.ACTIVO},
    EstadoTrabajo.ACTIVO: {EstadoTrabajo.SUSPENDIDO, EstadoTrabajo.CERRADO},
    EstadoTrabajo.SUSPENDIDO: {EstadoTrabajo.ACTIVO, EstadoTrabajo.CERRADO},
    EstadoTrabajo.CERRADO: set(),
    EstadoTrabajo.RECHAZADO: set(),
}


def _evaluar_cuadrilla(permiso):
    """Reutiliza apps.accesos.services.calcular_habilitacion — misma regla RN-007, sin duplicar."""
    if permiso.empresa.estado not in _ESTADOS_HABILITANTES:
        return (
            False,
            f"La empresa '{permiso.empresa.razon_social}' está en estado {permiso.empresa.get_estado_display()}.",
        )
    for persona in permiso.integrantes.all():
        resultado = calcular_habilitacion(persona)
        if not resultado.habilitado:
            return False, f"{persona.nombre_completo}: {resultado.motivo}"
    return True, "Toda la cuadrilla y la empresa están habilitadas."


def _transicionar(*, permiso, nuevo_estado, actor, motivo=""):
    permitidos = TRANSICIONES.get(permiso.estado, set())
    if nuevo_estado not in permitidos:
        raise TransicionEstadoInvalida(f"No se puede pasar de '{permiso.estado}' a '{nuevo_estado}'.")
    permiso.estado = nuevo_estado
    if motivo:
        permiso.motivo = motivo
    permiso.save()
    emitir_evento(
        actor=actor,
        accion="TRANSICION_PERMISO_TRABAJO",
        recurso=permiso,
        organizacion=permiso.organizacion,
        motivo=f"-> {nuevo_estado}. {motivo}".strip(),
    )


@transaction.atomic
def crear_permiso_trabajo(
    *,
    empresa,
    instalacion,
    actividad,
    nivel_riesgo,
    integrantes,
    actor,
    fecha_inicio_programada,
    fecha_fin_programada,
    contrato=None,
    responsable_cuadrilla=None,
):
    if fecha_fin_programada <= fecha_inicio_programada:
        raise DomainError("La fecha de fin programada debe ser posterior a la de inicio.")
    if not integrantes:
        raise DomainError("Un permiso de trabajo requiere al menos un integrante.")

    permiso = PermisoTrabajo.objects.create(
        organizacion=empresa.organizacion,
        empresa=empresa,
        contrato=contrato,
        instalacion=instalacion,
        actividad=actividad,
        nivel_riesgo=nivel_riesgo,
        responsable_cuadrilla=responsable_cuadrilla,
        estado=EstadoTrabajo.SOLICITADO,
        fecha_inicio_programada=fecha_inicio_programada,
        fecha_fin_programada=fecha_fin_programada,
    )
    permiso.integrantes.set(integrantes)

    habilitado, motivo = _evaluar_cuadrilla(permiso)
    if not habilitado:
        permiso.estado = EstadoTrabajo.RECHAZADO
        permiso.motivo = motivo
        permiso.save(update_fields=["estado", "motivo", "updated_at"])

    emitir_evento(
        actor=actor, accion="CREAR_PERMISO_TRABAJO", recurso=permiso, organizacion=empresa.organizacion, motivo=motivo
    )
    return permiso


@transaction.atomic
def abrir_permiso_trabajo(*, permiso, actor):
    habilitado, motivo = _evaluar_cuadrilla(permiso)
    if not habilitado:
        raise DomainError(f"No se puede abrir: {motivo}")
    permiso.fecha_apertura_real = timezone.now()
    permiso.aprobado_por = actor
    _transicionar(permiso=permiso, nuevo_estado=EstadoTrabajo.ACTIVO, actor=actor, motivo=motivo)
    return permiso


@transaction.atomic
def suspender_permiso_trabajo(*, permiso, motivo, actor):
    if not motivo:
        raise DomainError("Toda suspensión requiere un motivo.")
    _transicionar(permiso=permiso, nuevo_estado=EstadoTrabajo.SUSPENDIDO, actor=actor, motivo=motivo)
    return permiso


@transaction.atomic
def reanudar_permiso_trabajo(*, permiso, actor):
    habilitado, motivo = _evaluar_cuadrilla(permiso)
    if not habilitado:
        raise DomainError(f"No se puede reanudar: {motivo}")
    _transicionar(permiso=permiso, nuevo_estado=EstadoTrabajo.ACTIVO, actor=actor, motivo=motivo)
    return permiso


@transaction.atomic
def cerrar_permiso_trabajo(*, permiso, motivo, actor):
    _transicionar(permiso=permiso, nuevo_estado=EstadoTrabajo.CERRADO, actor=actor, motivo=motivo)
    permiso.fecha_cierre_real = timezone.now()
    permiso.save(update_fields=["fecha_cierre_real"])
    return permiso
