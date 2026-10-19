from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector
from apps.antecedentes.models import EstadoVerificacion


class ConectorManual(ConectorAntecedentes):
    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.LISTA,
            actor=actor,
            nota_bitacora="Verificación manual: lista para cargar el certificado.",
        )
        return ResultadoConector(exito=True, detalle="Lista para carga manual.")
