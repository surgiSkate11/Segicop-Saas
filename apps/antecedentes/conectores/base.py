from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


@dataclass
class ResultadoConector:
    exito: bool
    detalle: str = ""


class ClaseRespuesta(str, Enum):
    """Cómo respondió una fuente, sin detalles del proveedor (S2, tabla de manejo de errores)."""

    DISPONIBLE = "DISPONIBLE"  # respondió: sigue la intervención humana
    TRANSITORIA = "TRANSITORIA"  # tiempo agotado, red o 5xx: se reintenta con límite
    DENEGADA = "DENEGADA"  # 4xx o acceso denegado: no se reintenta
    LIMITADA = "LIMITADA"  # 429: la fuente pide esperar


@dataclass
class ResultadoSondeo:
    clase: ClaseRespuesta
    codigo_http: int | None = None
    latencia_ms: int | None = None
    evidencia: str = ""  # evidencia técnica minimizada: nunca cuerpo, cabeceras ni datos personales
    reintentar_tras: int | None = None  # segundos pedidos por la fuente (Retry-After)


class ConectorAntecedentes(ABC):
    @abstractmethod
    def iniciar(self, verificacion, actor=None) -> ResultadoConector: ...
