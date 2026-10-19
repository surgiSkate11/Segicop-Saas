from django.conf import settings

from apps.antecedentes import maquina_estados
from apps.antecedentes.conectores.base import ConectorAntecedentes, ResultadoConector, ResultadoSondeo
from apps.antecedentes.conectores.ministerio import PORTAL_URL, sondear_portal
from apps.antecedentes.models import EstadoVerificacion


class ConectorAsistido(ConectorAntecedentes):
    """
    Arranca desde LISTA: la autorización de la persona ya está registrada.
    Comprueba el portal oficial; el CAPTCHA lo resuelve una persona (S3, S4).
    """

    url_portal = PORTAL_URL

    def iniciar(self, verificacion, actor=None) -> ResultadoConector:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.EN_CONSULTA,
            actor=actor,
            nota_bitacora="Consulta al portal oficial iniciada.",
        )
        return ResultadoConector(exito=True, detalle="Comprobando el portal oficial.")

    def sondear(self) -> ResultadoSondeo:
        return sondear_portal(self.url_portal, getattr(settings, "ANTECEDENTES_SONDEO_TIMEOUT", 5))
