from datetime import date, datetime

from django import template
from django.utils.dateformat import format as formatear

register = template.Library()


@register.filter
def celda(fila, clave):
    """Value of one report cell formatted for HTML: dates d/m/Y, date-times d/m/Y H:i, empty as a dash."""
    valor = fila.get(clave)
    if isinstance(valor, datetime):
        return formatear(valor, "d/m/Y H:i")
    if isinstance(valor, date):
        return formatear(valor, "d/m/Y")
    return valor if valor not in (None, "") else "—"
