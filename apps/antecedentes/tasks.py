import logging

from celery import shared_task

from apps.antecedentes import services
from apps.antecedentes.models import ConsultaProveedor

logger = logging.getLogger(__name__)


@shared_task
def ejecutar_consulta_asistida(consulta_id):
    """Modo asíncrono (ANTECEDENTES_CONSULTA_ASINCRONA): mismo servicio, mismos límites; nunca resuelve CAPTCHA."""
    consulta = ConsultaProveedor.objects.get(pk=consulta_id)
    ejecutada = services.ejecutar_consulta_asistida(consulta=consulta)
    consulta.refresh_from_db()
    logger.info(
        "consulta_antecedentes_ejecutada",
        extra={"consulta_id": str(consulta.pk), "proveedor": consulta.proveedor, "estado": consulta.estado},
    )
    return {"estado": consulta.estado, "omitida": not ejecutada}
