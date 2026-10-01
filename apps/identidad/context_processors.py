def organizacion_actual(request):
    """
    Agrega `organizacion_actual` y `rol_actual` al contexto de TODOS los
    templates. Toma el primer RolAsignado activo del usuario.

    Cuando exista selección múltiple de organización (un usuario con
    roles en varias), esto se reemplaza por un valor guardado en sesión.
    """
    if not request.user.is_authenticated:
        return {}

    rol_asignado = (
        request.user.roles_asignados.filter(activo=True, organizacion__isnull=False)
        .select_related("organizacion", "rol")
        .first()
    )
    if not rol_asignado:
        return {"organizacion_actual": None, "rol_actual": None}

    return {"organizacion_actual": rol_asignado.organizacion, "rol_actual": rol_asignado.rol}
