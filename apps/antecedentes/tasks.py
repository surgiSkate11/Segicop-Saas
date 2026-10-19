import logging

from celery import shared_task
from django.utils import timezone

from apps.antecedentes.models import ConsultaProveedor, EstadoVerificacion, IntentoConsulta

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=4,
)
def preparar_consulta_asistida(self, consulta_id):
    """Prepara el punto humano; no resuelve CAPTCHA ni evade controles del portal."""
    consulta = ConsultaProveedor.objects.get(pk=consulta_id)
    if consulta.estado not in {EstadoVerificacion.EN_COLA, EstadoVerificacion.REINTENTANDO}:
        return {"estado": consulta.estado, "omitida": True}

    ahora = timezone.now()
    consulta.intentos += 1
    consulta.ultimo_intento_at = ahora
    consulta.estado = EstadoVerificacion.REQUIERE_INTERVENCION
    consulta.requiere_intervencion = True
    consulta.detalle_error = (
        "El portal requiere una acción humana o una integración oficial autorizada. "
        "No se automatizan CAPTCHA ni controles antifraude."
    )
    consulta.save(
        update_fields=[
            "intentos",
            "ultimo_intento_at",
            "estado",
            "requiere_intervencion",
            "detalle_error",
            "updated_at",
        ]
    )
    IntentoConsulta.objects.create(
        consulta=consulta,
        numero=consulta.intentos,
        estado_resultante=consulta.estado,
        detalle=consulta.detalle_error,
    )
    logger.info(
        "consulta_antecedentes_requiere_intervencion",
        extra={"consulta_id": str(consulta.pk), "proveedor": consulta.proveedor},
    )
    return {"estado": consulta.estado, "requiere_intervencion": True}
