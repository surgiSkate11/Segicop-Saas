"""
Punto único de salida de correo del sistema. Todo envío pasa por aquí
para que el remitente/servidor se resuelva en un solo lugar.
"""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection


def obtener_conexion():
    return get_connection()


def remitente_por_defecto() -> str:
    return settings.DEFAULT_FROM_EMAIL


def enviar_correo(*, asunto: str, destinatarios: list[str], texto: str, html: str | None = None) -> None:
    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=remitente_por_defecto(),
        to=destinatarios,
        connection=obtener_conexion(),
    )
    if html:
        mensaje.attach_alternative(html, "text/html")
    mensaje.send(fail_silently=False)
