from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.accesos.models import EstadoPermiso, EventoAcceso, PermisoIngreso, TipoEvento
from apps.antecedentes.models import EstadoVerificacion, ResultadoAntecedentes
from apps.core.choices import EstadoCumplimiento
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


@dataclass
class ResultadoHabilitacion:
    habilitado: bool
    motivo: str


_ESTADOS_HABILITANTES = {EstadoCumplimiento.HABILITADO, EstadoCumplimiento.HABILITADO_CON_CONDICIONES}


def calcular_habilitacion(persona: Persona) -> ResultadoHabilitacion:
    """
    RN-007: SOLO LECTURA. No escribe en Persona, EmpresaContratista ni
    VerificacionAntecedentes — únicamente las consulta y combina.
    """
    if persona.estado not in _ESTADOS_HABILITANTES:
        return ResultadoHabilitacion(False, f"Estado documental de la persona: {persona.get_estado_display()}.")

    asignacion_activa = persona.asignaciones.filter(activa=True).select_related("empresa").first()
    if not asignacion_activa:
        return ResultadoHabilitacion(False, "La persona no tiene una asignación activa a una empresa.")

    empresa = asignacion_activa.empresa
    if empresa.estado not in _ESTADOS_HABILITANTES:
        return ResultadoHabilitacion(False, f"La empresa '{empresa.razon_social}' está en estado {empresa.get_estado_display()}.")

    hoy = timezone.now().date()
    verificacion = (
        persona.verificaciones_antecedentes.filter(estado=EstadoVerificacion.VALIDADA).order_by("-created_at").first()
    )
    if not verificacion:
        return ResultadoHabilitacion(False, "No hay una verificación de antecedentes validada.")
    if verificacion.vigente_hasta and verificacion.vigente_hasta < hoy:
        return ResultadoHabilitacion(False, "La verificación de antecedentes validada ya venció.")
    if verificacion.resultado != ResultadoAntecedentes.NO:
        return ResultadoHabilitacion(False, f"El resultado de antecedentes es '{verificacion.get_resultado_display()}' y requiere revisión humana adicional.")

    return ResultadoHabilitacion(True, "Cumple estado documental, estado de empresa y antecedentes vigentes.")


@transaction.atomic
def solicitar_permiso_ingreso(*, persona, instalacion, actor, fecha_inicio_vigencia, fecha_fin_vigencia) -> PermisoIngreso:
    if fecha_fin_vigencia <= fecha_inicio_vigencia:
        raise DomainError("La fecha de fin de vigencia debe ser posterior a la de inicio.")

    resultado = calcular_habilitacion(persona)
    permiso = PermisoIngreso.objects.create(
        organizacion=persona.organizacion, persona=persona, instalacion=instalacion,
        estado=EstadoPermiso.APROBADO if resultado.habilitado else EstadoPermiso.RECHAZADO,
        motivo=resultado.motivo, fecha_inicio_vigencia=fecha_inicio_vigencia, fecha_fin_vigencia=fecha_fin_vigencia,
        aprobado_por=actor if resultado.habilitado else None,
    )
    emitir_evento(actor=actor, accion="SOLICITAR_PERMISO_INGRESO", recurso=permiso, organizacion=persona.organizacion, motivo=resultado.motivo)
    return permiso


@transaction.atomic
def registrar_evento_acceso(*, codigo, instalacion, tipo: str, actor) -> EventoAcceso:
    if tipo not in {TipoEvento.ENTRADA, TipoEvento.SALIDA}:
        raise DomainError(f"Tipo de evento inválido: {tipo}")

    permiso = PermisoIngreso.objects.filter(codigo=codigo).select_related("persona", "instalacion").first()
    if not permiso:
        raise DomainError("Código no reconocido. No se puede registrar el evento sin una persona identificada.")

    motivo_denegacion = ""
    if permiso.estado != EstadoPermiso.APROBADO:
        motivo_denegacion = f"Permiso en estado {permiso.get_estado_display()}, no está aprobado."
    elif not permiso.vigente_hoy:
        motivo_denegacion = "El permiso está fuera de su ventana de vigencia."
    elif permiso.instalacion_id != instalacion.id:
        motivo_denegacion = f"El permiso es para '{permiso.instalacion.nombre}', no para esta instalación."

    tipo_evento = TipoEvento.DENEGADO if motivo_denegacion else tipo
    evento = EventoAcceso.objects.create(
        permiso_ingreso=permiso, persona=permiso.persona, instalacion=instalacion,
        tipo=tipo_evento, motivo_denegacion=motivo_denegacion, registrado_por=actor,
    )
    emitir_evento(actor=actor, accion=f"EVENTO_ACCESO_{tipo_evento}", recurso=evento,
                  organizacion=permiso.organizacion, motivo=motivo_denegacion or "Acceso registrado normalmente.")
    return evento


def marcar_vencidos(actor=None) -> int:
    hoy = timezone.now().date()
    permisos = PermisoIngreso.objects.filter(estado=EstadoPermiso.APROBADO, fecha_fin_vigencia__lt=hoy)
    total = permisos.count()
    for permiso in permisos:
        permiso.estado = EstadoPermiso.VENCIDO
        permiso.save(update_fields=["estado", "updated_at"])
        emitir_evento(actor=actor, accion="PERMISO_INGRESO_VENCIDO", recurso=permiso,
                      organizacion=permiso.organizacion, motivo="Vencimiento detectado automáticamente.")
    return total