from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector


class ConectorManual(ConectorAntecedentes):
    """El conector solo arranca desde LISTA; la carga manual no tiene nada que automatizar."""

    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        return ResultadoConector(exito=True, detalle="Lista para carga manual.")
