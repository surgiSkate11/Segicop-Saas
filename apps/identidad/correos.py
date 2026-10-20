"""
Correos de acceso a cuentas de portal (S1/S2). Nunca contienen una
contraseña: llevan el usuario (RUC/cédula) y, si corresponde, el enlace
de activación de un solo uso.
"""

from django.conf import settings
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.core.correo import enviar_correo
from apps.identidad.tokens import activacion_cuenta_token

HORAS_VIGENCIA_ENLACE = settings.PASSWORD_RESET_TIMEOUT // 3600


def url_activacion(usuario) -> str:
    uid = urlsafe_base64_encode(force_bytes(usuario.pk))
    ruta = reverse("identidad:activar_cuenta", args=[uid, activacion_cuenta_token.make_token(usuario)])
    return f"{settings.SIGECOP_SITIO_URL.rstrip('/')}{ruta}"


def enviar_correo_acceso(*, usuario, organizacion, con_enlace: bool) -> None:
    """
    con_enlace=True  -> cuenta nueva/sin activar o reenvío: enlace para definir contraseña.
    con_enlace=False -> cuenta ya activa que gana acceso a otra organización: solo aviso.
    """
    contexto = {
        "usuario": usuario,
        "organizacion": organizacion,
        "enlace": url_activacion(usuario) if con_enlace else None,
        "horas_vigencia": HORAS_VIGENCIA_ENLACE,
        "url_login": f"{settings.SIGECOP_SITIO_URL.rstrip('/')}{reverse('identidad:login')}",
    }
    asunto = (
        f"SIGECOP · Activa tu acceso para {organizacion.nombre}"
        if con_enlace
        else f"SIGECOP · Nuevo acceso habilitado en {organizacion.nombre}"
    )
    enviar_correo(
        asunto=asunto,
        destinatarios=[usuario.email],
        texto=render_to_string("identidad/correos/acceso.txt", contexto),
        html=render_to_string("identidad/correos/acceso.html", contexto),
    )
