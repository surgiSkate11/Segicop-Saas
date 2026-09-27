from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector
from apps.antecedentes.models import EstadoVerificacion


class ConectorManual(ConectorAntecedentes):
    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion, nuevo_estado=EstadoVerificacion.CONTINGENCIA_MANUAL,
            actor=actor, nota_bitacora="Verificación manual: pendiente de carga de resultado.",
        )
        return ResultadoConector(exito=True, detalle="Encolada para carga manual.")