from django.db import transaction

from apps.core.choices import EstadoCumplimiento
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.identidad.services import provisionar_cuenta, sincronizar_cuenta
from apps.personas.models import Asignacion, Persona


@transaction.atomic
def registrar_persona(
    *, organizacion, tipo_documento, numero_documento, nombres, apellidos, actor, **datos_opcionales
) -> Persona:
    """
    RF-022: detecta coincidencia por documento antes del IntegrityError.
    S1: con correo de contacto, crea su cuenta de portal (usuario = documento).
    """
    coincidencia = Persona.objects.filter(
        organizacion=organizacion, tipo_documento=tipo_documento, numero_documento=numero_documento
    ).first()
    if coincidencia:
        raise DomainError(f"Ya existe una persona registrada con ese documento: {coincidencia.nombre_completo}.")

    persona = Persona.objects.create(
        organizacion=organizacion,
        tipo_documento=tipo_documento,
        numero_documento=numero_documento,
        nombres=nombres,
        apellidos=apellidos,
        estado=EstadoCumplimiento.BORRADOR,
        **datos_opcionales,
    )
    emitir_evento(
        actor=actor,
        accion="REGISTRAR_PERSONA",
        recurso=persona,
        organizacion=organizacion,
        motivo=f"Alta de {persona.nombre_completo} ({numero_documento}).",
    )
    if persona.contacto_email:
        provisionar_cuenta(titular=persona, actor=actor)
    return persona


@transaction.atomic
def asignar_persona_a_empresa(
    *, persona, empresa, cargo_o_actividad, fecha_inicio, actor, contrato=None, fecha_fin=None
) -> Asignacion:
    """Valida aislamiento multi-tenant (AC-002) a nivel de servicio."""
    if persona.organizacion_id != empresa.organizacion_id:
        raise DomainError("La persona y la empresa deben pertenecer a la misma organización.")
    if contrato and contrato.empresa_id != empresa.id:
        raise DomainError("El contrato indicado no pertenece a la empresa indicada.")

    asignacion = Asignacion.objects.create(
        organizacion=empresa.organizacion,
        persona=persona,
        empresa=empresa,
        contrato=contrato,
        cargo_o_actividad=cargo_o_actividad,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    emitir_evento(
        actor=actor,
        accion="ASIGNAR_PERSONA",
        recurso=asignacion,
        organizacion=empresa.organizacion,
        motivo=f"{persona.nombre_completo} asignado a {empresa.razon_social}.",
    )
    return asignacion


_CAMPOS_EDITABLES_PERSONA = ("fecha_nacimiento", "contacto_telefono", "contacto_email")


@transaction.atomic
def actualizar_persona(
    *,
    persona: Persona,
    actor,
    tipo_documento: str,
    numero_documento: str,
    nombres: str,
    apellidos: str,
    **datos_opcionales,
) -> Persona:
    """Edita datos de identificación. Nunca toca `estado` (lo calcula el motor de cumplimiento)."""
    documento_cambio = (tipo_documento, numero_documento) != (persona.tipo_documento, persona.numero_documento)
    if documento_cambio and (
        Persona.objects.filter(
            organizacion=persona.organizacion, tipo_documento=tipo_documento, numero_documento=numero_documento
        )
        .exclude(pk=persona.pk)
        .exists()
    ):
        raise DomainError("Ya existe otra persona registrada con ese documento.")

    persona.tipo_documento = tipo_documento
    persona.numero_documento = numero_documento
    persona.nombres = nombres
    persona.apellidos = apellidos
    campos = ["tipo_documento", "numero_documento", "nombres", "apellidos", "updated_at"]
    for campo in _CAMPOS_EDITABLES_PERSONA:
        if campo in datos_opcionales:
            setattr(persona, campo, datos_opcionales[campo])
            campos.append(campo)
    persona.save(update_fields=campos)

    emitir_evento(
        actor=actor,
        accion="ACTUALIZAR_PERSONA",
        recurso=persona,
        organizacion=persona.organizacion,
        motivo=f"Datos de {persona.nombre_completo} actualizados.",
    )
    sincronizar_cuenta(titular=persona, actor=actor)
    return persona
