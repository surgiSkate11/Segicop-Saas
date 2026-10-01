import logging

from celery import shared_task
from opentelemetry import trace


logger = logging.getLogger(__name__)


@shared_task(name="apps.core.tasks.procesar_vencimientos")
def procesar_vencimientos():
    from apps.cumplimiento.jobs import procesar_vencimientos as ejecutar

    tracer = trace.get_tracer("sigecop.celery")
    with tracer.start_as_current_span("sigecop.procesar_vencimientos") as span:
        resultado = ejecutar()
        span.set_attribute("sigecop.alertas.total", resultado["total_alertas"])
        span.set_attribute("sigecop.vencimientos.documentos", resultado["documentos"])
        span.set_attribute("sigecop.vencimientos.antecedentes", resultado["antecedentes"])
        span.set_attribute("sigecop.vencimientos.permisos", resultado["permisos_ingreso"])
        logger.info("procesamiento_de_vencimientos_completado", extra={"resultado": resultado})
        return resultado
