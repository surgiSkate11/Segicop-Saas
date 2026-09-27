from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.identidad.models import AlcanceAsignado, Organizacion, Rol, RolAsignado, Usuario


@transaction.atomic
def invitar_usuario(*, email: str, nombre_completo: str, actor=None) -> Usuario:
    """
    RF-004: invitar una cuenta "sin revelar si una persona ya existe".
    """
    usuario, fue_creado = Usuario.objects.get_or_create(
        email=Usuario.objects.normalize_email(email),
        defaults={"nombre_completo": nombre_completo, "is_active": False},
    )

    emitir_evento(
        actor=actor,
        accion="INVITAR_USUARIO" if fue_creado else "REENVIAR_INVITACION",
        recurso=usuario,
        motivo="Alta o reenvío de invitación de acceso.",
    )
    return usuario


@transaction.atomic
def asignar_rol(*, usuario: Usuario, rol: Rol, organizacion, actor: Usuario, instalaciones=None) -> RolAsignado:
    """
    Asigna un rol a un usuario dentro de una organización, con alcance
    opcional a instalaciones específicas.
    """
    if rol.es_rol_plataforma and organizacion is not None:
        raise DomainError(
            "Un rol de plataforma no puede asignarse dentro de una organización."
        )
    if not rol.es_rol_plataforma and organizacion is None:
        raise DomainError(f"El rol '{rol.nombre}' requiere una organización.")

    rol_asignado, _ = RolAsignado.objects.get_or_create(
        usuario=usuario, organizacion=organizacion, rol=rol,
        defaults={"activo": True},
    )

    for instalacion in instalaciones or []:
        AlcanceAsignado.objects.get_or_create(
            rol_asignado=rol_asignado,
            content_type=ContentType.objects.get_for_model(instalacion),
            object_id=instalacion.pk,
        )

    emitir_evento(
        actor=actor,
        accion="ASIGNAR_ROL",
        recurso=rol_asignado,
        organizacion=organizacion,
        motivo=f"Rol '{rol.nombre}' asignado a {usuario.email}.",
    )
    return rol_asignado


@transaction.atomic
def revocar_rol(*, rol_asignado: RolAsignado, actor: Usuario, motivo: str) -> None:
    rol_asignado.activo = False
    rol_asignado.save(update_fields=["activo", "updated_at"])

    emitir_evento(
        actor=actor,
        accion="REVOCAR_ROL",
        recurso=rol_asignado,
        organizacion=rol_asignado.organizacion,
        motivo=motivo,
    )