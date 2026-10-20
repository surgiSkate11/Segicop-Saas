"""
Punto único de salida de correo del sistema. Todo envío pasa por aquí
para que el remitente/servidor se resuelva en un solo lugar: la
ConfiguracionCorreo activa (panel administrativo) o, sin ella, los settings.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

from apps.core.models import ConfiguracionCorreo


def obtener_conexion(configuracion=None):
    configuracion = configuracion or ConfiguracionCorreo.vigente()
    if configuracion is None:
        return get_connection()
    return get_connection(
        "django.core.mail.backends.smtp.EmailBackend",
        host=configuracion.host,
        port=configuracion.puerto,
        username=configuracion.usuario,
        password=configuracion.password,
        use_tls=configuracion.usar_tls,
        use_ssl=configuracion.usar_ssl,
        timeout=configuracion.timeout,
    )


def remitente_por_defecto(configuracion=None) -> str:
    configuracion = configuracion or ConfiguracionCorreo.vigente()
    return configuracion.remitente if configuracion else settings.DEFAULT_FROM_EMAIL


def enviar_correo(
    *, asunto: str, destinatarios: list[str], texto: str, html: str | None = None, configuracion=None
) -> None:
    """`configuracion` explícita solo para probar una que todavía no está activa."""
    configuracion = configuracion or ConfiguracionCorreo.vigente()
    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=remitente_por_defecto(configuracion),
        to=destinatarios,
        connection=obtener_conexion(configuracion),
    )
    if html:
        mensaje.attach_alternative(html, "text/html")
    mensaje.send(fail_silently=False)
