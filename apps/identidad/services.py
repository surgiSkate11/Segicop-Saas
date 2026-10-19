import logging
import smtplib
import uuid

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.core.services import crear_alerta
from apps.identidad.correos import enviar_correo_acceso
from apps.identidad.models import AlcanceAsignado, Rol, RolAsignado, Usuario

logger = logging.getLogger("sigecop.identidad")

# Roles de portal: se crean bajo demanda para no depender del fixture.
ROLES_PORTAL = {
    "contratista": (
        "Contratista",
        "Empresa contratista que sube la documentación requerida para su validación de ingreso.",
    ),
    "trabajador": ("Trabajador", "Persona que consulta su estado documental y sus asignaciones."),
}


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
        raise DomainError("Un rol de plataforma no puede asignarse dentro de una organización.")
    if not rol.es_rol_plataforma and organizacion is None:
        raise DomainError(f"El rol '{rol.nombre}' requiere una organización.")

    rol_asignado, _ = RolAsignado.objects.get_or_create(
        usuario=usuario,
        organizacion=organizacion,
        rol=rol,
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


def _rol_portal(codigo: str) -> Rol:
    nombre, descripcion = ROLES_PORTAL[codigo]
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre, "descripcion": descripcion})
    return rol


def _programar_correo_acceso(*, usuario: Usuario, organizacion, actor, con_enlace: bool) -> None:
    """
    El correo sale solo si la transacción confirma: nunca se envía acceso
    a una cuenta que terminó revertida. Si el envío falla, queda en la
    auditoría y como alerta del panel para que alguien lo reenvíe.
    """

    def _enviar():
        try:
            enviar_correo_acceso(usuario=usuario, organizacion=organizacion, con_enlace=con_enlace)
        except (smtplib.SMTPException, OSError) as error:
            logger.exception("No se pudo enviar el correo de acceso a %s", usuario.pk)
            emitir_evento(
                actor=actor,
                accion="ENVIAR_ACCESO",
                recurso=usuario,
                organizacion=organizacion,
                motivo=f"Falló el envío del correo de acceso: {error.__class__.__name__}.",
                resultado="ERROR",
            )
            crear_alerta(
                organizacion=organizacion,
                clave_idempotencia=f"ACCESO_NO_ENVIADO:{usuario.pk}:{uuid.uuid4()}",
                tipo="ACCESO_NO_ENVIADO",
                agrupador="CORREOS_DE_ACCESO",
                titulo="Correo de acceso no enviado",
                mensaje=f"No se pudo enviar el acceso de {usuario.nombre_completo} ({usuario.nombre_usuario}). Revísalo y reenvíalo.",
                severidad="ALTA",
            )
        else:
            emitir_evento(
                actor=actor,
                accion="ENVIAR_ACCESO",
                recurso=usuario,
                organizacion=organizacion,
                motivo="Enlace de activación enviado." if con_enlace else "Aviso de nuevo acceso enviado.",
            )

    transaction.on_commit(_enviar)


@transaction.atomic
def provisionar_cuenta(*, titular, actor) -> Usuario:
    """
    S1/S8: crea (o reutiliza) la cuenta de portal de un titular con
    CuentaAccesoMixin, le da el rol de portal en su organización y programa
    el correo de acceso. Si el RUC/cédula ya tiene cuenta (otra organización),
    se reutiliza: una sola identidad, varios accesos.
    """
    nombre_usuario = titular.nombre_usuario_cuenta.strip()
    correo = (titular.correo_cuenta or "").strip()
    if not correo:
        raise DomainError("Se necesita un correo de contacto para enviar el acceso.")

    usuario = Usuario.objects.filter(nombre_usuario=nombre_usuario).first()
    if usuario is None:
        correo = Usuario.objects.normalize_email(correo)
        if Usuario.objects.filter(email__iexact=correo).exists():
            raise DomainError(f"El correo {correo} ya está asociado a otra cuenta. Usa un correo distinto.")
        # Sin contraseña: queda inutilizable hasta que el titular la defina (S2).
        usuario = Usuario.objects.create_user(
            email=correo,
            nombre_completo=titular.nombre_cuenta,
            nombre_usuario=nombre_usuario,
            tipo_cuenta=titular.TIPO_CUENTA,
        )
        accion = "CREAR_CUENTA"
    elif usuario.tipo_cuenta != titular.TIPO_CUENTA:
        raise DomainError(f"El usuario {nombre_usuario} ya existe con otro tipo de cuenta.")
    else:
        accion = "VINCULAR_CUENTA"

    titular.usuario = usuario
    titular.save(update_fields=["usuario", "updated_at"])
    rol_asignado, _ = RolAsignado.objects.get_or_create(
        usuario=usuario, organizacion=titular.organizacion, rol=_rol_portal(titular.ROL_CUENTA)
    )
    if not rol_asignado.activo:
        rol_asignado.activo = True
        rol_asignado.save(update_fields=["activo", "updated_at"])

    emitir_evento(
        actor=actor,
        accion=accion,
        recurso=usuario,
        organizacion=titular.organizacion,
        motivo=f"Cuenta de portal {nombre_usuario} para {titular.nombre_cuenta}.",
    )
    _programar_correo_acceso(
        usuario=usuario,
        organizacion=titular.organizacion,
        actor=actor,
        con_enlace=not usuario.has_usable_password(),
    )
    return usuario


@transaction.atomic
def enviar_acceso(*, titular, actor) -> Usuario:
    """
    S9: (re)envía un enlace al correo de contacto ACTUAL del titular.
    Sin cuenta todavía -> la crea. Con cuenta -> nuevo enlace (el anterior
    sigue sirviendo hasta que se use uno, porque el token depende de la
    contraseña). Si el correo de contacto cambió, la cuenta lo adopta.
    """
    usuario = titular.usuario
    if usuario is None:
        return provisionar_cuenta(titular=titular, actor=actor)

    correo = Usuario.objects.normalize_email((titular.correo_cuenta or "").strip())
    if not correo:
        raise DomainError("Se necesita un correo de contacto para enviar el acceso.")
    if correo.lower() != usuario.email.lower():
        if Usuario.objects.filter(email__iexact=correo).exclude(pk=usuario.pk).exists():
            raise DomainError(f"El correo {correo} ya está asociado a otra cuenta. Usa un correo distinto.")
        usuario.email = correo
        usuario.save(update_fields=["email", "updated_at"])

    _programar_correo_acceso(usuario=usuario, organizacion=titular.organizacion, actor=actor, con_enlace=True)
    return usuario


@transaction.atomic
def sincronizar_cuenta(*, titular, actor) -> None:
    """
    Tras editar al titular: si cambió su RUC/cédula o su nombre, la cuenta
    lo sigue. Una cuenta compartida con otra organización no se renombra
    desde una sola de ellas (cambiaría el usuario de todas).
    """
    usuario = titular.usuario
    if usuario is None:
        return
    nombre_usuario = titular.nombre_usuario_cuenta.strip()
    if (usuario.nombre_usuario, usuario.nombre_completo) == (nombre_usuario, titular.nombre_cuenta):
        return

    if type(titular).objects.filter(usuario=usuario).exclude(pk=titular.pk).exists():
        raise DomainError(
            "La cuenta de acceso se comparte con otra organización; no se puede cambiar su usuario desde aquí."
        )
    if Usuario.objects.filter(nombre_usuario=nombre_usuario).exclude(pk=usuario.pk).exists():
        raise DomainError(f"Ya existe otra cuenta con el usuario {nombre_usuario}.")

    anterior = usuario.nombre_usuario
    usuario.nombre_usuario = nombre_usuario
    usuario.nombre_completo = titular.nombre_cuenta
    usuario.save(update_fields=["nombre_usuario", "nombre_completo", "updated_at"])
    emitir_evento(
        actor=actor,
        accion="SINCRONIZAR_CUENTA",
        recurso=usuario,
        organizacion=titular.organizacion,
        motivo=f"Cuenta {anterior} actualizada a {nombre_usuario}.",
    )


@transaction.atomic
def activar_cuenta(*, usuario: Usuario, password: str) -> Usuario:
    """S2: el titular define su contraseña desde el enlace (ya validado por la vista)."""
    usuario.set_password(password)
    if usuario.cuenta_activada_at is None:
        usuario.cuenta_activada_at = timezone.now()
    usuario.save(update_fields=["password", "cuenta_activada_at", "updated_at"])
    emitir_evento(
        actor=usuario,
        accion="ACTIVAR_CUENTA",
        recurso=usuario,
        motivo="El titular definió su contraseña desde el enlace de acceso.",
    )
    return usuario


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
