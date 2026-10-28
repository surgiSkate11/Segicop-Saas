from django.db import transaction
from django.utils import timezone

from apps.core.choices import EstadoCumplimiento
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.cumplimiento.models import (
    CatalogoRequisito,
    DocumentoVersion,
    EstadoRequisito,
    RequisitoAplicado,
    Revision,
)
from apps.cumplimiento.utils import calcular_hash_archivo


@transaction.atomic
def crear_catalogo_requisito(
    *, organizacion, actor, codigo, nombre, descripcion="", aplica_a, obligatorio=True, vigencia_dias=None
) -> CatalogoRequisito:
    if CatalogoRequisito.objects.filter(organizacion=organizacion, codigo=codigo).exists():
        raise DomainError(f"Ya existe un requisito con el código '{codigo}' en esta organización.")

    catalogo = CatalogoRequisito.objects.create(
        organizacion=organizacion,
        codigo=codigo,
        nombre=nombre,
        descripcion=descripcion,
        aplica_a=aplica_a,
        obligatorio=obligatorio,
        vigencia_dias=vigencia_dias,
    )
    emitir_evento(
        actor=actor,
        accion="CREAR_CATALOGO_REQUISITO",
        recurso=catalogo,
        organizacion=organizacion,
        motivo=f"Requisito '{nombre}' agregado al catálogo.",
    )
    return catalogo


@transaction.atomic
def asignar_requisito(*, catalogo, actor, empresa=None, persona=None, fecha_limite=None) -> RequisitoAplicado:
    if bool(empresa) == bool(persona):
        raise DomainError("Un requisito aplicado debe tener exactamente una empresa o una persona.")
    if empresa and catalogo.aplica_a != "EMPRESA":
        raise DomainError(f"El requisito '{catalogo.nombre}' no aplica a empresas.")
    if persona and catalogo.aplica_a != "PERSONA":
        raise DomainError(f"El requisito '{catalogo.nombre}' no aplica a personas.")

    organizacion = empresa.organizacion if empresa else persona.organizacion
    requisito, creado = RequisitoAplicado.objects.get_or_create(
        organizacion=organizacion,
        catalogo=catalogo,
        empresa=empresa,
        persona=persona,
        defaults={"estado": EstadoRequisito.PENDIENTE, "fecha_limite": fecha_limite},
    )
    if creado:
        emitir_evento(
            actor=actor,
            accion="ASIGNAR_REQUISITO",
            recurso=requisito,
            organizacion=organizacion,
            motivo=f"Requisito '{catalogo.nombre}' asignado.",
        )
        _recalcular(empresa=empresa, persona=persona, actor=actor)
    return requisito


@transaction.atomic
def subir_documento(*, requisito_aplicado, archivo, actor, fecha_vencimiento=None) -> DocumentoVersion:
    """RN-001: inmutable, hash antes de guardar, deja EN_REVISION (nunca auto-aprueba)."""
    hash_calculado = calcular_hash_archivo(archivo)
    ultima_version = requisito_aplicado.documentos.order_by("-version").values_list("version", flat=True).first() or 0

    if requisito_aplicado.documentos.filter(hash_sha256=hash_calculado, vigente=True).exists():
        raise DomainError("Este archivo es idéntico a la versión vigente actual; no se sube como nueva versión.")

    requisito_aplicado.documentos.filter(vigente=True).update(vigente=False)

    documento = DocumentoVersion.objects.create(
        organizacion=requisito_aplicado.organizacion,
        requisito_aplicado=requisito_aplicado,
        archivo=archivo,
        hash_sha256=hash_calculado,
        version=ultima_version + 1,
        vigente=True,
        fecha_vencimiento=fecha_vencimiento,
        subido_por=actor,
    )

    requisito_aplicado.estado = EstadoRequisito.EN_REVISION
    requisito_aplicado.save(update_fields=["estado", "updated_at"])

    emitir_evento(
        actor=actor,
        accion="SUBIR_DOCUMENTO",
        recurso=documento,
        organizacion=requisito_aplicado.organizacion,
        motivo=f"Versión {documento.version} de '{requisito_aplicado.catalogo.nombre}'.",
    )
    _recalcular(empresa=requisito_aplicado.empresa, persona=requisito_aplicado.persona, actor=actor)
    return documento


@transaction.atomic
def revisar_documento(*, documento_version, revisor, decision: str, comentario: str = "") -> Revision:
    """RN-007: decisión de autenticidad/cumplimiento, nunca se mezcla con antecedentes ni habilitación."""
    if decision not in Revision.Decision.values:
        raise DomainError(f"Decisión de revisión inválida: {decision}")
    if decision == Revision.Decision.OBSERVADO and not comentario:
        raise DomainError("Toda observación requiere un comentario explicando qué corregir.")
    if decision == Revision.Decision.RECHAZADO and not comentario:
        raise DomainError("Todo rechazo requiere un comentario con el motivo.")

    revision = Revision.objects.create(
        organizacion=documento_version.organizacion,
        documento_version=documento_version,
        revisor=revisor,
        decision=decision,
        comentario=comentario,
    )

    requisito_aplicado = documento_version.requisito_aplicado
    mapa_estado = {
        Revision.Decision.APROBADO: EstadoRequisito.APROBADO,
        Revision.Decision.OBSERVADO: EstadoRequisito.OBSERVADO,
        Revision.Decision.RECHAZADO: EstadoRequisito.RECHAZADO,
    }
    requisito_aplicado.estado = mapa_estado[decision]
    requisito_aplicado.save(update_fields=["estado", "updated_at"])

    emitir_evento(
        actor=revisor,
        accion=f"REVISAR_DOCUMENTO_{decision}",
        recurso=revision,
        organizacion=documento_version.organizacion,
        motivo=comentario or "Sin comentario.",
    )
    _recalcular(empresa=requisito_aplicado.empresa, persona=requisito_aplicado.persona, actor=revisor)
    return revision


def _expirar_vencidos(requisitos_obligatorios, actor=None):
    """Detección perezosa de vencimiento en cada recálculo (el job diario de Fase 9 hace lo mismo en bloque)."""
    hoy = timezone.localdate()
    for requisito in requisitos_obligatorios:
        if requisito.estado != EstadoRequisito.APROBADO:
            continue
        documento_vigente = requisito.documentos.filter(vigente=True).first()
        if documento_vigente and documento_vigente.fecha_vencimiento and documento_vigente.fecha_vencimiento < hoy:
            estado_anterior = requisito.estado
            requisito.estado = EstadoRequisito.VENCIDO
            requisito.save(update_fields=["estado", "updated_at"])
            emitir_evento(
                actor=actor,
                accion="VENCER_REQUISITO",
                recurso=requisito,
                organizacion=requisito.organizacion,
                motivo=f"{estado_anterior} -> {EstadoRequisito.VENCIDO}; documento v{documento_vigente.version} vencido.",
                metadata={
                    "documento_id": str(documento_vigente.id),
                    "fecha_vencimiento": str(documento_vigente.fecha_vencimiento),
                },
            )


def _calcular_estado(requisitos_obligatorios, requisitos_opcionales) -> str:
    if not requisitos_obligatorios:
        return EstadoCumplimiento.BORRADOR
    estados = {r.estado for r in requisitos_obligatorios}
    if EstadoRequisito.RECHAZADO in estados:
        return EstadoCumplimiento.NO_HABILITADO
    if EstadoRequisito.VENCIDO in estados:
        return EstadoCumplimiento.VENCIDO
    if EstadoRequisito.OBSERVADO in estados:
        return EstadoCumplimiento.OBSERVADO
    if EstadoRequisito.PENDIENTE in estados:
        return EstadoCumplimiento.INCOMPLETO
    if EstadoRequisito.EN_REVISION in estados:
        return EstadoCumplimiento.EN_REVISION
    estados_opcionales = {r.estado for r in requisitos_opcionales}
    if estados_opcionales - {EstadoRequisito.APROBADO}:
        return EstadoCumplimiento.HABILITADO_CON_CONDICIONES
    return EstadoCumplimiento.HABILITADO


def _recalcular(*, empresa=None, persona=None, actor=None):
    if empresa:
        _recalcular_para(objetivo=empresa, campo_filtro="empresa", actor=actor)
    if persona:
        _recalcular_para(objetivo=persona, campo_filtro="persona", actor=actor)


def _recalcular_para(*, objetivo, campo_filtro, actor):
    requisitos = list(RequisitoAplicado.objects.filter(**{campo_filtro: objetivo}).select_related("catalogo"))
    obligatorios = [r for r in requisitos if r.catalogo.obligatorio]
    opcionales = [r for r in requisitos if not r.catalogo.obligatorio]

    _expirar_vencidos(obligatorios, actor=actor)
    nuevo_estado = _calcular_estado(obligatorios, opcionales)

    if objetivo.estado == nuevo_estado:
        return
    estado_anterior = objetivo.estado
    objetivo.estado = nuevo_estado
    objetivo.save(update_fields=["estado", "updated_at"])
    emitir_evento(
        actor=actor,
        accion="RECALCULAR_ESTADO",
        recurso=objetivo,
        organizacion=objetivo.organizacion,
        motivo=f"{estado_anterior} -> {nuevo_estado}",
    )


def recalcular_empresa(empresa, actor=None):
    _recalcular_para(objetivo=empresa, campo_filtro="empresa", actor=actor)


def recalcular_persona(persona, actor=None):
    _recalcular_para(objetivo=persona, campo_filtro="persona", actor=actor)
