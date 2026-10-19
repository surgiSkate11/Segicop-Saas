from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector
from apps.antecedentes.models import EstadoVerificacion


class ConectorAsistido(ConectorAntecedentes):
    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.REQUIERE_INTERVENCION,
            actor=actor,
            nota_bitacora=(
                "La consulta requiere intervención humana en el portal oficial. "
                "No se evaden CAPTCHA ni controles antifraude."
            ),
        )
        return ResultadoConector(exito=True, detalle="Intervención humana requerida.")
