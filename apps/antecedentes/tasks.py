import logging

from celery import shared_task

from apps.antecedentes import services
from apps.antecedentes.models import ConsultaProveedor, EstadoConsulta

logger = logging.getLogger(__name__)


# TODO(T4): reintentos con espera incremental (máx. 2) cuando la tarea haga I/O de red.
@shared_task
def preparar_consulta_asistida(consulta_id):
    """Prepara el punto humano; no resuelve CAPTCHA ni evade controles del portal."""
    consulta = ConsultaProveedor.objects.get(pk=consulta_id)
    if consulta.estado not in {EstadoConsulta.PENDIENTE, EstadoConsulta.REINTENTO_PROGRAMADO}:
        return {"estado": consulta.estado, "omitida": True}

    services.cambiar_estado_consulta(
        consulta=consulta,
        nuevo_estado=EstadoConsulta.CAPTCHA_REQUERIDO,
        registrar_intento=True,
        detalle_error=(
            "El portal requiere una acción humana o una integración oficial autorizada. "
            "No se automatizan CAPTCHA ni controles antifraude."
        ),
    )
    logger.info(
        "consulta_antecedentes_requiere_intervencion",
        extra={"consulta_id": str(consulta.pk), "proveedor": consulta.proveedor},
    )
    return {"estado": consulta.estado, "requiere_intervencion": True}
