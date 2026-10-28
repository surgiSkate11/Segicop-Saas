"""
T5 / S8 (RF-060, RF-061): resumen diario de alertas por correo, uno por organización y día.

Destinatarios: usuarios internos activos con la acción `recibir_resumen_alertas` de la matriz
(`identidad.permisos`). El envío se decide y audita dentro de la transacción (eso evita un segundo
resumen el mismo día) y el correo sale solo si la transacción confirma. Si el SMTP falla, queda un
evento con resultado ERROR y una alerta ALTA: nunca se da por enviado en silencio.
"""

import logging
import smtplib

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse

from apps.core.correo import enviar_correo
from apps.core.events import emitir_evento
from apps.core.models import AlertaNotificacion, EventoAuditoria
from apps.core.services import crear_alerta
from apps.identidad.models import Organizacion, RolAsignado, TipoCuenta
from apps.identidad.permisos import PERMISOS

logger = logging.getLogger("sigecop.identidad")

ACCION = "ENVIAR_RESUMEN_ALERTAS"
MAX_ALERTAS_EN_CORREO = 50


def _destinatarios(organizacion):
    return sorted(
        set(
            RolAsignado.objects.filter(
                organizacion=organizacion,
                activo=True,
                rol__codigo__in=PERMISOS["recibir_resumen_alertas"],
                usuario__is_active=True,
                usuario__tipo_cuenta=TipoCuenta.INTERNA,
            ).values_list("usuario__email", flat=True)
        )
    )


def _ya_enviado(organizacion, hoy):
    return EventoAuditoria.objects.filter(
        accion=ACCION, organizacion=organizacion, resultado="OK", created_at__date=hoy
    ).exists()


def enviar_resumenes_alertas(*, hoy) -> int:
    """Programa un resumen por organización con alertas creadas hoy. Devuelve cuántos programó."""
    enviados = 0
    organizaciones_ids = (
        AlertaNotificacion.objects.filter(created_at__date=hoy).values_list("organizacion_id", flat=True).distinct()
    )
    for organizacion in Organizacion.objects.filter(pk__in=organizaciones_ids).order_by("nombre"):
        enviados += int(_enviar_resumen(organizacion=organizacion, hoy=hoy))
    return enviados


@transaction.atomic
def _enviar_resumen(*, organizacion, hoy) -> bool:
    if _ya_enviado(organizacion, hoy):
        return False
    destinatarios = _destinatarios(organizacion)
    if not destinatarios:
        return False
    alertas = list(
        AlertaNotificacion.objects.filter(organizacion=organizacion, created_at__date=hoy).order_by(
            "agrupador", "titulo"
        )
    )
    emitir_evento(
        actor=None,
        accion=ACCION,
        recurso=organizacion,
        organizacion=organizacion,
        motivo=f"Resumen de {len(alertas)} alertas del {hoy:%d/%m/%Y} para {len(destinatarios)} destinatarios.",
    )
    contexto = {
        "organizacion": organizacion,
        "hoy": hoy,
        "alertas": alertas[:MAX_ALERTAS_EN_CORREO],
        "total": len(alertas),
        "omitidas": max(len(alertas) - MAX_ALERTAS_EN_CORREO, 0),
        "url_alertas": f"{settings.SIGECOP_SITIO_URL.rstrip('/')}{reverse('identidad:alertas')}",
    }

    def _enviar():
        try:
            enviar_correo(
                asunto=f"SIGECOP · {len(alertas)} alertas nuevas en {organizacion.nombre}",
                destinatarios=destinatarios,
                texto=render_to_string("identidad/correos/resumen_alertas.txt", contexto),
                html=render_to_string("identidad/correos/resumen_alertas.html", contexto),
            )
        except (smtplib.SMTPException, OSError) as error:
            logger.exception("No se pudo enviar el resumen de alertas de %s", organizacion.pk)
            emitir_evento(
                actor=None,
                accion=ACCION,
                recurso=organizacion,
                organizacion=organizacion,
                motivo=f"Falló el envío del resumen de alertas: {error.__class__.__name__}.",
                resultado="ERROR",
            )
            crear_alerta(
                organizacion=organizacion,
                clave_idempotencia=f"RESUMEN_NO_ENVIADO:{organizacion.pk}:{hoy.isoformat()}",
                tipo="RESUMEN_NO_ENVIADO",
                agrupador="CORREOS",
                titulo="Resumen diario de alertas no enviado",
                mensaje="Revisa la configuración de correo. Las alertas siguen disponibles en SIGECOP.",
                severidad="ALTA",
                fecha_referencia=hoy,
            )

    transaction.on_commit(_enviar)
    return True
