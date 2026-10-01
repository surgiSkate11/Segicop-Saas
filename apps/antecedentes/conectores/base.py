from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ResultadoConector:
    exito: bool
    detalle: str = ""


class ConectorAntecedentes(ABC):
    @abstractmethod
    def iniciar(self, verificacion, actor=None) -> ResultadoConector: ...
