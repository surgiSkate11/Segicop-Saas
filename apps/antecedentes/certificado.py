"""
Lectura del texto del certificado de antecedentes penales del Ministerio del Interior (S7).

Módulo puro, sin Django: recibe el texto ya extraído del PDF. Todavía no hay un PDF real de muestra,
así que es tolerante (tildes, mayúsculas, saltos de línea, con o sin dos puntos) y, ante lo que no
reconoce, devuelve vacío en lugar de inventar: el caso pasa entonces a revisión humana.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# Etiquetas del diseño sobre texto sin tildes y en minúsculas; cada una delimita el valor de la anterior.
_ETIQUETAS = {
    "fecha_emision": r"fecha de emision",
    "numero_certificado": r"(?:numero|nro\.?|no\.?) de certificado",
    "tipo_documento": r"tipo de documento",
    "identificacion": r"(?:numero|nro\.?|no\.?) de identificacion",
    "nombres": r"apellidos y nombres",
    "registra": r"registra antecedentes",
    "valido_hasta": r"valido hasta(?: el)?",
    "firma": r"documento firmado electronicamente",
}
_PATRON_ETIQUETAS = re.compile("|".join(f"(?P<{clave}>{patron})" for clave, patron in _ETIQUETAS.items()))
_FECHA_LARGA = re.compile(r"(\d{1,2})\s+de\s+([a-z]+)\s+(?:del?\s+)?(\d{4})")
_FECHA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_FECHA_BARRAS = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})")


@dataclass(frozen=True)
class CertificadoExtraido:
    fecha_emision: date | None = None
    numero_certificado: str = ""
    tipo_documento: str = ""
    identificacion: str = ""
    nombres: str = ""
    resultado: str | None = None  # "SI" | "NO" | None si no se reconoce
    vigente_hasta: date | None = None
    marca_firma: bool = False


def _sin_tildes(texto: str) -> str:
    """Minúsculas y sin diacríticos, conservando la longitud para alinear posiciones con el original."""
    return "".join(unicodedata.normalize("NFD", caracter)[0].lower() for caracter in texto)


def _fecha(valor: str) -> date | None:
    valor = _sin_tildes(valor)
    try:
        if coincidencia := _FECHA_ISO.search(valor):
            anio, mes, dia = coincidencia.groups()
        elif coincidencia := _FECHA_LARGA.search(valor):
            dia, nombre_mes, anio = coincidencia.groups()
            mes = MESES.get(nombre_mes)
        elif coincidencia := _FECHA_BARRAS.search(valor):
            dia, mes, anio = coincidencia.groups()
        else:
            return None
        return date(int(anio), int(mes), int(dia))
    except (TypeError, ValueError):
        return None


def _resultado(valor: str) -> str | None:
    coincidencia = re.match(r"\W*(si|no)\b", _sin_tildes(valor))
    return coincidencia.group(1).upper() if coincidencia else None


def analizar_texto(texto: str) -> CertificadoExtraido:
    plano = re.sub(r"\s+", " ", texto or "").strip()
    normalizado = _sin_tildes(plano)
    marcas = list(_PATRON_ETIQUETAS.finditer(normalizado))
    valores: dict[str, str] = {}
    for indice, marca in enumerate(marcas):
        clave = marca.lastgroup
        fin = marcas[indice + 1].start() if indice + 1 < len(marcas) else len(plano)
        valores.setdefault(clave, plano[marca.end():fin].strip(" :.-\t"))

    return CertificadoExtraido(
        fecha_emision=_fecha(valores.get("fecha_emision", "")),
        numero_certificado=valores.get("numero_certificado", "").split(" ")[0],
        tipo_documento=valores.get("tipo_documento", ""),
        identificacion=valores.get("identificacion", "").split(" ")[0],
        nombres=valores.get("nombres", ""),
        resultado=_resultado(valores.get("registra", "")),
        vigente_hasta=_fecha(valores.get("valido_hasta", "")),
        marca_firma="firma" in valores,
    )


def _palabras(nombre: str) -> list[str]:
    return sorted(re.findall(r"[a-z0-9]+", _sin_tildes(nombre)))


def nombres_equivalentes(extraido: str, esperado: str) -> bool:
    """Mismas palabras sin importar orden, tildes, mayúsculas ni espacios; vacío nunca coincide."""
    palabras = _palabras(extraido)
    return bool(palabras) and palabras == _palabras(esperado)
