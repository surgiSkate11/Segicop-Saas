"""
S8: rol dedicado + cuatro ojos.

Los roles son filas de `identidad.Rol` asignadas por organización (`RolAsignado` activo).
Ni el superusuario ni otros roles (garita, revisor documental...) dan acceso aquí:
mínimo privilegio, la plataforma no recibe acceso automático a datos sensibles.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from apps.antecedentes.models import VerificacionAntecedentes
from apps.identidad.models import RolAsignado

ROL_OPERADOR = "operador_antecedentes"
ROL_REVISOR = "revisor_antecedentes"

# Operador: inicia, consulta de forma asistida, carga y registra autorizaciones; no ve el resultado.
# Revisor: todo lo del operador, más ver resultados/PDF y decidir.
ROLES = {
    ROL_OPERADOR: (
        "Operador de antecedentes",
        "Inicia verificaciones, realiza la consulta asistida, carga documentos y registra autorizaciones; "
        "no ve el resultado ni el certificado.",
    ),
    ROL_REVISOR: (
        "Revisor de antecedentes",
        "Ve resultados y certificados, y valida, devuelve o anula las verificaciones que no cargó.",
    ),
}


def _id(organizacion):
    return getattr(organizacion, "pk", organizacion)


def _organizaciones_con_rol(usuario, codigos):
    if not getattr(usuario, "is_authenticated", False):
        return RolAsignado.objects.none().values_list("organizacion_id", flat=True)
    return RolAsignado.objects.filter(
        usuario=usuario, activo=True, rol__codigo__in=codigos, organizacion__isnull=False
    ).values_list("organizacion_id", flat=True)


def organizaciones_operables(usuario):
    return _organizaciones_con_rol(usuario, (ROL_OPERADOR, ROL_REVISOR))


def organizaciones_revisables(usuario):
    return _organizaciones_con_rol(usuario, (ROL_REVISOR,))


def puede_operar(usuario, organizacion) -> bool:
    return organizaciones_operables(usuario).filter(organizacion_id=_id(organizacion)).exists()


def puede_revisar(usuario, organizacion) -> bool:
    return organizaciones_revisables(usuario).filter(organizacion_id=_id(organizacion)).exists()


def verificaciones_visibles(usuario):
    """Verificaciones de las organizaciones donde el usuario es operador o revisor."""
    return VerificacionAntecedentes.objects.filter(organizacion_id__in=organizaciones_operables(usuario))


def exigir_operar(usuario, organizacion) -> None:
    if not puede_operar(usuario, organizacion):
        raise PermissionDenied("No tienes un rol de antecedentes en esta organización.")


def exigir_revisar(usuario, organizacion) -> None:
    if not puede_revisar(usuario, organizacion):
        raise PermissionDenied("Solo un revisor de antecedentes puede hacer esto.")


class OperadorRequeridoMixin(LoginRequiredMixin):
    """403 para quien no opera antecedentes en ninguna organización (bandeja y acciones)."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not organizaciones_operables(request.user).exists():
            raise PermissionDenied("No tienes un rol de antecedentes.")
        return super().dispatch(request, *args, **kwargs)
