from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector
from apps.antecedentes.models import EstadoVerificacion
from apps.core.exceptions import DomainError


class ConectorAsistido(ConectorAntecedentes):
    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.FALLIDA_TECNICA,
            actor=actor,
            nota_bitacora="Conector asistido aún no implementado (requiere worker Celery aislado).",
        )
        raise DomainError(
            "El conector asistido todavía no está implementado en este proyecto. "
            "Usa ANTECEDENTES_CONECTOR_MODE=manual mientras se construye el worker Celery."
        )
