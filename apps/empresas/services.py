from django.db import transaction
from django.utils import timezone

from apps.core.choices import EstadoCumplimiento
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError, TransicionEstadoInvalida
from apps.empresas.models import Contrato, EmpresaContratista


@transaction.atomic
def crear_empresa(*, organizacion, razon_social: str, ruc: str, actor, **datos_opcionales) -> EmpresaContratista:
    """RF-012: detecta el RUC duplicado antes del IntegrityError de BD."""
    if EmpresaContratista.objects.filter(organizacion=organizacion, ruc=ruc).exists():
        raise DomainError(f"Ya existe una empresa con RUC {ruc} registrada en esta organización.")

    empresa = EmpresaContratista.objects.create(
        organizacion=organizacion, razon_social=razon_social, ruc=ruc,
        estado=EstadoCumplimiento.BORRADOR, **datos_opcionales,
    )
    emitir_evento(actor=actor, accion="CREAR_EMPRESA", recurso=empresa, organizacion=organizacion,
                  motivo=f"Alta de {razon_social} ({ruc}).")
    return empresa


@transaction.atomic
def suspender_empresa(*, empresa: EmpresaContratista, motivo: str, actor, vigente_hasta=None) -> EmpresaContratista:
    """RF-013: suspender con motivo y vigencia."""
    if empresa.estado == EstadoCumplimiento.SUSPENDIDO:
        raise TransicionEstadoInvalida("La empresa ya está suspendida; reactívala antes de suspenderla de nuevo.")
    if not motivo:
        raise DomainError("Toda suspensión requiere un motivo explícito.")

    empresa.estado = EstadoCumplimiento.SUSPENDIDO
    empresa.motivo_estado = motivo
    empresa.suspendida_desde = timezone.now()
    empresa.suspendida_hasta = vigente_hasta
    empresa.save(update_fields=["estado", "motivo_estado", "suspendida_desde", "suspendida_hasta", "updated_at"])

    emitir_evento(actor=actor, accion="SUSPENDER_EMPRESA", recurso=empresa,
                  organizacion=empresa.organizacion, motivo=motivo)
    # El "impacto calculado" (qué permisos de ingreso/trabajo se bloquean)
    # se conecta en la Fase 7/8, cuando existan PermisoIngreso/PermisoTrabajo.
    return empresa


@transaction.atomic
def reactivar_empresa(*, empresa: EmpresaContratista, motivo: str, actor) -> EmpresaContratista:
    if empresa.estado != EstadoCumplimiento.SUSPENDIDO:
        raise TransicionEstadoInvalida("Solo se puede reactivar una empresa suspendida.")

    empresa.estado = EstadoCumplimiento.EN_REVISION
    empresa.motivo_estado = motivo
    empresa.suspendida_desde = None
    empresa.suspendida_hasta = None
    empresa.save(update_fields=["estado", "motivo_estado", "suspendida_desde", "suspendida_hasta", "updated_at"])

    emitir_evento(actor=actor, accion="REACTIVAR_EMPRESA", recurso=empresa,
                  organizacion=empresa.organizacion, motivo=motivo)
    return empresa


@transaction.atomic
def crear_contrato(*, empresa: EmpresaContratista, numero: str, objeto: str, fecha_inicio, fecha_fin,
                    actor, instalaciones=None, responsable=None) -> Contrato:
    if fecha_fin <= fecha_inicio:
        raise DomainError("La fecha de fin del contrato debe ser posterior a la de inicio.")

    contrato = Contrato.objects.create(
        organizacion=empresa.organizacion, empresa=empresa, numero=numero, objeto=objeto,
        fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, responsable=responsable,
    )
    if instalaciones:
        contrato.instalaciones.set(instalaciones)

    emitir_evento(actor=actor, accion="CREAR_CONTRATO", recurso=contrato,
                  organizacion=empresa.organizacion, motivo=f"Contrato {numero} para {empresa.razon_social}.")
    return contrato