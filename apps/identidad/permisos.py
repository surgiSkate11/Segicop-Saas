"""
S2/S3: matriz rol → acción de las vistas internas (denegar por defecto).

Es la tabla de `odd/preguntas-director.md` P5, derivada solo de la tabla de roles de los requerimientos.
Vive en UN solo lugar: si el director corrige una fila, cambia una línea de `PERMISOS`.

Los roles son filas de `identidad.Rol` asignadas por organización (`RolAsignado` activo). Los roles
`contratista` y `admin_plataforma` no aparecen aquí: no reciben ninguna acción interna.
Antecedentes conserva sus reglas propias (`apps.antecedentes.permisos`).
"""

from django.core.exceptions import PermissionDenied

from apps.identidad.models import RolAsignado

ADMIN_CLIENTE = "admin_cliente"
GESTOR_CONTRATISTA = "gestor_contratista"
REVISOR_DOCUMENTAL = "revisor_documental"
RESPONSABLE_AREA = "responsable_area"
SEGURIDAD_GARITA = "seguridad_garita"
OPERADOR_ANTECEDENTES = "operador_antecedentes"
REVISOR_ANTECEDENTES = "revisor_antecedentes"

# Solo para mostrar u ocultar el menú; el acceso a antecedentes lo decide apps.antecedentes.permisos.
ROLES_ANTECEDENTES = frozenset({OPERADOR_ANTECEDENTES, REVISOR_ANTECEDENTES})

PERMISOS = {
    "ver_registros": frozenset(
        {
            ADMIN_CLIENTE,
            GESTOR_CONTRATISTA,
            REVISOR_DOCUMENTAL,
            RESPONSABLE_AREA,
            OPERADOR_ANTECEDENTES,
            REVISOR_ANTECEDENTES,
        }
    ),
    "editar_registros": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA}),
    "enviar_credenciales": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA}),
    "configurar_catalogo": frozenset({ADMIN_CLIENTE}),
    "asignar_requisitos": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA}),
    "subir_documentos": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA}),
    "revisar_documentos": frozenset({ADMIN_CLIENTE, REVISOR_DOCUMENTAL}),
    "solicitar_ingreso": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA, RESPONSABLE_AREA}),
    "aprobar_ingreso": frozenset({ADMIN_CLIENTE, RESPONSABLE_AREA}),
    "crear_trabajo": frozenset({ADMIN_CLIENTE, GESTOR_CONTRATISTA, RESPONSABLE_AREA}),
    "gestionar_trabajo": frozenset({ADMIN_CLIENTE, RESPONSABLE_AREA}),
    "garita": frozenset({SEGURIDAD_GARITA}),
    "ver_alertas": frozenset(
        {
            ADMIN_CLIENTE,
            GESTOR_CONTRATISTA,
            REVISOR_DOCUMENTAL,
            RESPONSABLE_AREA,
            OPERADOR_ANTECEDENTES,
            REVISOR_ANTECEDENTES,
        }
    ),
    "exportar_reportes": frozenset({ADMIN_CLIENTE}),
    "gestionar_usuarios": frozenset({ADMIN_CLIENTE}),
}


# Roles que la pantalla de usuarios internos (T2) puede asignar: exactamente los que aparecen en la matriz.
# Los roles de portal (contratista, trabajador) y el de plataforma nunca se asignan desde ahí.
ROLES_INTERNOS = frozenset().union(*PERMISOS.values())


def _roles_permitidos(accion):
    """Falla cerrado: una acción que no está en la matriz es un error de programación, nunca un permiso."""
    try:
        return PERMISOS[accion]
    except KeyError:
        raise ValueError(f"Acción desconocida: {accion!r}") from None


def _asignaciones_activas(usuario):
    if not getattr(usuario, "is_authenticated", False):
        return RolAsignado.objects.none()
    return RolAsignado.objects.filter(usuario=usuario, activo=True, organizacion__isnull=False)


def tiene_rol_activo(usuario):
    """¿Tiene algún rol activo (de organización o de plataforma)? Condición mínima para abrir el panel."""
    if not getattr(usuario, "is_authenticated", False):
        return False
    return RolAsignado.objects.filter(usuario=usuario, activo=True).exists()


def organizaciones_con_permiso(usuario, accion):
    """Ids de las organizaciones donde el usuario tiene un rol ACTIVO que permite `accion`."""
    roles = _roles_permitidos(accion)
    return _asignaciones_activas(usuario).filter(rol__codigo__in=roles).values_list("organizacion_id", flat=True)


def puede(usuario, accion, organizacion_id=None):
    """¿Puede el usuario `accion` en esa organización (o en alguna, si no se indica)?"""
    organizaciones = organizaciones_con_permiso(usuario, accion)
    if organizacion_id is not None:
        organizaciones = organizaciones.filter(organizacion_id=getattr(organizacion_id, "pk", organizacion_id))
    return organizaciones.exists()


def exigir(usuario, accion, organizacion_id=None):
    if not puede(usuario, accion, organizacion_id):
        raise PermissionDenied("Tu rol no permite esta acción.")


def organizacion_con_permiso(usuario, accion):
    """Una organización (la primera, de forma estable) donde el usuario puede `accion`; None si no hay."""
    asignacion = (
        _asignaciones_activas(usuario)
        .filter(rol__codigo__in=_roles_permitidos(accion))
        .select_related("organizacion")
        .order_by("created_at", "pk")
        .first()
    )
    return asignacion.organizacion if asignacion else None


def acciones_permitidas(usuario):
    """Acciones que el usuario tiene en al menos una organización (para el menú; el 403 es el control real)."""
    codigos = set(_asignaciones_activas(usuario).values_list("rol__codigo", flat=True))
    return {accion for accion, roles in PERMISOS.items() if roles & codigos}
