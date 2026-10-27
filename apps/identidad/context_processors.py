from apps.identidad.permisos import ROLES_ANTECEDENTES, acciones_permitidas


def organizacion_actual(request):
    """
    Agrega `organizacion_actual`, `rol_actual` y `acciones_permitidas` al contexto de TODOS los
    templates. Toma el primer RolAsignado activo del usuario.

    `acciones_permitidas` (S4) es solo cosmético: decide qué entradas del menú se muestran. El control
    real es el 403 de cada vista (`RequierePermisoMixin`). `ve_antecedentes` sigue las reglas propias
    de apps.antecedentes (operador o revisor de antecedentes).

    Cuando exista selección múltiple de organización (un usuario con
    roles en varias), esto se reemplaza por un valor guardado en sesión.
    """
    if not request.user.is_authenticated:
        return {}

    asignaciones = request.user.roles_asignados.filter(activo=True, organizacion__isnull=False)
    rol_asignado = asignaciones.select_related("organizacion", "rol").first()
    permitidas = acciones_permitidas(request.user)
    ve_antecedentes = asignaciones.filter(rol__codigo__in=ROLES_ANTECEDENTES).exists()
    if not rol_asignado:
        return {
            "organizacion_actual": None,
            "rol_actual": None,
            "acciones_permitidas": permitidas,
            "ve_antecedentes": ve_antecedentes,
        }

    return {
        "organizacion_actual": rol_asignado.organizacion,
        "rol_actual": rol_asignado.rol,
        "acciones_permitidas": permitidas,
        "ve_antecedentes": ve_antecedentes,
    }
