"""
Lectura del texto del certificado de antecedentes penales del Ministerio del Interior (S7).

Módulo puro, sin Django: recibe el texto ya extraído del PDF. Admite dos diseños:

- El certificado real (TCPDF): los rótulos están en la imagen de fondo y el texto trae solo los valores,
  en orden fijo antes de "Válido hasta". Se leen por posición y cada valor debe tener su forma esperada.
- Texto con rótulos ("Fecha de Emisión: ..."), tolerante a tildes, mayúsculas y saltos de línea.

Ante lo que no reconoce devuelve vacío en lugar de inventar: el caso pasa entonces a revisión humana.
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


# Forma de cada valor del diseño oficial, en orden, sobre texto sin tildes y en minúsculas.
_POSICIONES_OFICIALES = (
    ("fecha_emision", re.compile(r"\d{1,2} de [a-z]+ del? \d{4}")),
    ("numero_certificado", re.compile(r"(?=[a-z0-9-]*\d)[a-z0-9-]{8,30}")),
    ("tipo_documento", re.compile(r"(?:cedula|pasaporte|documento|carne|refugiado)[a-z ]*")),
    ("identificacion", re.compile(r"[a-z0-9]{5,20}")),
    ("nombres", re.compile(r"[a-zñ]+(?: [a-zñ]+)+")),
    ("registra", re.compile(r"si|no")),
)


def _por_posicion(texto: str) -> dict[str, str]:
    """Valores del certificado oficial: las seis líneas antes de "Válido hasta", todas con su forma o ninguna."""
    lineas = [re.sub(r"\s+", " ", linea).strip() for linea in (texto or "").splitlines()]
    lineas = [linea for linea in lineas if linea]
    fin = next((i for i, linea in enumerate(lineas) if _sin_tildes(linea).startswith("valido hasta")), None)
    if fin is None or fin < len(_POSICIONES_OFICIALES):
        return {}
    candidatas = lineas[fin - len(_POSICIONES_OFICIALES):fin]
    valores = {}
    for (clave, forma), linea in zip(_POSICIONES_OFICIALES, candidatas):
        if not forma.fullmatch(_sin_tildes(linea)):
            return {}
        valores[clave] = linea
    return valores


def analizar_texto(texto: str) -> CertificadoExtraido:
    posicionales = _por_posicion(texto)
    plano = re.sub(r"\s+", " ", texto or "").strip()
    normalizado = _sin_tildes(plano)
    marcas = list(_PATRON_ETIQUETAS.finditer(normalizado))
    valores: dict[str, str] = {}
    for indice, marca in enumerate(marcas):
        clave = marca.lastgroup
        fin = marcas[indice + 1].start() if indice + 1 < len(marcas) else len(plano)
        valores.setdefault(clave, plano[marca.end():fin].strip(" :.-\t"))
    for clave, valor in posicionales.items():
        valores.setdefault(clave, valor)  # un rótulo explícito tiene prioridad

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
