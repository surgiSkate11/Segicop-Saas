from django import template

from apps.antecedentes import permisos

register = template.Library()


@register.simple_tag(takes_context=True)
def puede_operar_antecedentes(context, organizacion_id):
    """Una consulta de roles por petición, no por fila del listado."""
    request = context["request"]
    if not hasattr(request, "_organizaciones_operables_antecedentes"):
        request._organizaciones_operables_antecedentes = set(permisos.organizaciones_operables(request.user))
    return organizacion_id in request._organizaciones_operables_antecedentes
