from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector
from apps.antecedentes.models import EstadoVerificacion


class ConectorAsistido(ConectorAntecedentes):
    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.LISTA,
            actor=actor,
            nota_bitacora="Verificación asistida lista para consultar el portal oficial.",
        )
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.EN_CONSULTA,
            actor=actor,
            nota_bitacora="Consulta al portal oficial iniciada.",
        )
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.CAPTCHA_REQUERIDO,
            actor=actor,
            nota_bitacora=(
                "El portal requiere intervención humana (CAPTCHA). "
                "No se evaden CAPTCHA ni controles antifraude."
            ),
        )
        return ResultadoConector(exito=True, detalle="Intervención humana requerida.")
