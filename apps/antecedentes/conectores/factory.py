from django.conf import settings

from apps.antecedentes.conectores.asistido import ConectorAsistido
from apps.antecedentes.conectores.base import ConectorAntecedentes
from apps.antecedentes.conectores.manual import ConectorManual

_CONECTORES: dict[str, type[ConectorAntecedentes]] = {"manual": ConectorManual, "asistido": ConectorAsistido}


def obtener_conector() -> ConectorAntecedentes:
    modo = getattr(settings, "ANTECEDENTES_CONECTOR_MODE", "manual")
    clase = _CONECTORES.get(modo)
    if clase is None:
        raise ValueError(f"ANTECEDENTES_CONECTOR_MODE inválido: '{modo}'. Valores válidos: {list(_CONECTORES)}.")
    return clase()