"""
Lectura aislada de un PDF: texto y contenido activo (S7). Solo usa la biblioteca estándar y pypdf.

`pdf.extraer_texto` lo ejecuta como un proceso aparte (`python -I pdf_lector.py <páginas> <bytes>`, PDF por la
entrada estándar, JSON por la salida) para poder cortarlo por tiempo: un PDF malicioso puede hacer que pypdf
tarde minutos o agote la memoria, y eso nunca debe pasar dentro del proceso web ni con una fila bloqueada.
"""

import io
import json
import logging
import sys

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

logging.getLogger("pypdf").setLevel(logging.ERROR)  # un PDF dañado no debe inundar el registro

MAX_CARACTERES = 200_000  # el certificado tiene unas decenas de líneas

# Claves de diccionario o valores de nombre (/S /JavaScript, /Type /EmbeddedFile) que ejecutan o incrustan algo.
NOMBRES_ACTIVOS = frozenset(
    {
        "/JavaScript",
        "/JS",
        "/OpenAction",
        "/AA",
        "/Launch",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/RichMedia",
        "/XFA",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
    }
)

MENSAJE_ACTIVO = "El PDF contiene acciones o archivos incrustados no permitidos."
MENSAJE_CIFRADO = "No se aceptan PDF cifrados."
MENSAJE_ILEGIBLE = "No se pudo leer el PDF: está dañado o incompleto."
MENSAJE_SIN_TEXTO = "El PDF no contiene texto legible (¿es una imagen escaneada o está dañado?)."


def _objetos(lector):
    """Todos los objetos del documento, también los comprimidos dentro de flujos de objetos."""
    referencias = [
        IndirectObject(numero, generacion, lector)
        for generacion, entradas in lector.xref.items()
        for numero in entradas
    ]
    referencias += [IndirectObject(numero, 0, lector) for numero in lector.xref_objStm]
    for referencia in referencias:
        try:
            yield lector.get_object(referencia)
        except Exception:  # un objeto dañado no impide revisar los demás
            continue


def tiene_contenido_activo(lector) -> bool:
    """Busca los nombres de NOMBRES_ACTIVOS (ya sin escapes #xx) en cualquier objeto resuelto del documento."""
    pendientes = [lector.trailer, *_objetos(lector)]
    vistos = set()
    while pendientes:
        objeto = pendientes.pop()
        if isinstance(objeto, NameObject):
            if objeto in NOMBRES_ACTIVOS:
                return True
        elif isinstance(objeto, (DictionaryObject, ArrayObject)) and id(objeto) not in vistos:
            vistos.add(id(objeto))
            if isinstance(objeto, DictionaryObject):
                pendientes.extend(objeto.keys())
                pendientes.extend(objeto.values())
            else:
                pendientes.extend(objeto)
    return False


def leer(contenido: bytes, max_paginas: int, max_bytes_pagina: int) -> dict:
    """Devuelve {"error": motivo o None, "texto": ...}."""
    try:
        lector = PdfReader(io.BytesIO(contenido), strict=False)
        if lector.is_encrypted:
            return {"error": MENSAJE_CIFRADO, "texto": ""}
        if tiene_contenido_activo(lector):
            return {"error": MENSAJE_ACTIVO, "texto": ""}
        paginas = lector.pages
        if len(paginas) > max_paginas:
            return {"error": f"El PDF tiene demasiadas páginas (máximo {max_paginas}).", "texto": ""}
        for pagina in paginas:
            contenidos = pagina.get_contents()
            if contenidos is not None and len(contenidos.get_data()) > max_bytes_pagina:
                return {
                    "error": "El contenido de una página del PDF es demasiado grande para analizarlo.",
                    "texto": "",
                }
        texto = "\n".join((pagina.extract_text() or "") for pagina in paginas)[:MAX_CARACTERES]
    except Exception:  # pypdf lanza muchos tipos distintos ante archivos dañados
        return {"error": MENSAJE_ILEGIBLE, "texto": ""}
    if not texto.strip():
        return {"error": MENSAJE_SIN_TEXTO, "texto": ""}
    return {"error": None, "texto": texto}


if __name__ == "__main__":
    resultado = leer(sys.stdin.buffer.read(), int(sys.argv[1]), int(sys.argv[2]))
    sys.stdout.write(json.dumps(resultado))
