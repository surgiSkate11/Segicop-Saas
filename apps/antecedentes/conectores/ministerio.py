"""
Fuente: certificado de antecedentes penales del Ministerio del Interior.

El portal está detrás de un control antibots y exige CAPTCHA (S3): aquí solo se comprueba
que responde. Una petición GET identificada, sin la cédula, sin leer el cuerpo y sin
intentar resolver desafíos; la consulta real la hace una persona en su navegador (S4).
"""

import time
import urllib.error
import urllib.request

from apps.antecedentes.conectores.base import ClaseRespuesta, ResultadoSondeo

PORTAL_URL = "https://certificados.ministeriodelinterior.gob.ec/gestorcertificados/antecedentes/"
NOMBRE = "Ministerio del Interior"
USER_AGENT = "SIGECOP/1.0 (comprobacion de disponibilidad; sin datos personales)"


def _segundos(valor) -> int | None:
    try:
        return max(int(valor), 0)
    except (TypeError, ValueError):
        return None  # Retry-After con fecha HTTP u otro formato: se usa la pausa por defecto


def sondear_portal(url: str = PORTAL_URL, timeout: float = 5) -> ResultadoSondeo:
    peticion = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    inicio = time.monotonic()

    def latencia() -> int:
        return int((time.monotonic() - inicio) * 1000)

    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            codigo = respuesta.status
    except urllib.error.HTTPError as error:
        codigo = error.code
        reintentar_tras = _segundos(error.headers.get("Retry-After")) if error.headers else None
        error.close()
        ms = latencia()
        evidencia = f"HTTP {codigo} · {ms} ms"
        if codigo == 429:
            return ResultadoSondeo(ClaseRespuesta.LIMITADA, codigo, ms, evidencia, reintentar_tras)
        if codigo >= 500:
            return ResultadoSondeo(ClaseRespuesta.TRANSITORIA, codigo, ms, evidencia)
        return ResultadoSondeo(ClaseRespuesta.DENEGADA, codigo, ms, evidencia)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        causa = getattr(error, "reason", error)
        ms = latencia()
        return ResultadoSondeo(ClaseRespuesta.TRANSITORIA, None, ms, f"Sin respuesta ({type(causa).__name__}) · {ms} ms")

    ms = latencia()
    return ResultadoSondeo(ClaseRespuesta.DISPONIBLE, codigo, ms, f"HTTP {codigo} · {ms} ms")
