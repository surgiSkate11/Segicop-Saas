"""
Validación y lectura de los PDF de antecedentes (S7: "Aceptar solo PDF válido, dentro del límite y analizado").

No hay antivirus: el "análisis" es estructural (firmas del formato, cifrado, contenido activo) más la
lectura con pypdf, que no ejecuta nada del documento. Esa lectura corre en un proceso aparte con tiempo
límite (`pdf_lector.py`): un PDF hostil no puede colgar la petición ni retener un bloqueo de la base.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from apps.core.exceptions import DomainError

ANT_DOCUMENT_INVALID = "ANT_DOCUMENT_INVALID"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PAGINAS_LEIDAS = 5  # el certificado es de una página; más no aporta datos
MAX_BYTES_CONTENIDO_PAGINA = 512 * 1024  # contenido ya descomprimido de una página
PDF_TIMEOUT_POR_DEFECTO = 10  # segundos; ajuste ANTECEDENTES_PDF_TIMEOUT
_SCRIPT_LECTOR = Path(__file__).with_name("pdf_lector.py")
_PDF_ACTIVE_CONTENT = re.compile(
    rb"/(?:JavaScript|JS|AA|Launch|EmbeddedFiles?|RichMedia|XFA|SubmitForm|ImportData|GoToR)\b",
    re.IGNORECASE,
)
# `/OpenAction` aparte: el certificado oficial la usa para abrir en la página 1 (`[6 0 R /FitH null]`).
# Pasa un destino (arreglo) o un diccionario `/S /GoTo`, directos o detrás de una referencia que se
# encuentre en los bytes; cualquier otra forma, o una referencia que no se encuentra, se rechaza.
_OPEN_ACTION = re.compile(
    rb"/OpenAction\s*(\[|(\d+)\s+(\d+)\s+R\b|<<(.{0,512}?)>>)?", re.IGNORECASE | re.DOTALL
)
_ACCION_GOTO = re.compile(rb"/S\s*/GoTo\b")
_OTRA_ACCION = re.compile(rb"/S\s*/(?!GoTo\b)")
_NOMBRE_CON_ESCAPES = re.compile(rb"/[^\x00\s/<>\[\]()%{}]*#[^\x00\s/<>\[\]()%{}]*")
_ESCAPE_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
_FIRMA_PDF = re.compile(rb"/ByteRange\b|/Type\s*/Sig\b")


class DocumentoInvalido(DomainError):
    """PDF rechazado; `documento` es la fila en cuarentena, o None si ni siquiera se guardó."""

    codigo = ANT_DOCUMENT_INVALID

    def __init__(self, mensaje, documento=None):
        super().__init__(mensaje)
        self.documento = documento


def validar_entrada(archivo):
    """Nombre y tamaño; lo que falla aquí ni siquiera se guarda."""
    nombre = str(archivo.name or "")
    if (
        "\x00" in nombre
        or any(ord(caracter) < 32 for caracter in nombre)
        or not nombre.lower().endswith(".pdf")
        or len(nombre) > 255
    ):
        return "El nombre del archivo no es válido."
    if archivo.size <= 0 or archivo.size > MAX_DOCUMENT_BYTES:
        return "El archivo debe tener entre 1 byte y 10 MB."
    return None


def _sin_escapes_en_nombres(contenido: bytes) -> bytes:
    """`/J#61vaScript` es el nombre `/JavaScript` (escape #xx del formato): se compara ya resuelto."""
    return _NOMBRE_CON_ESCAPES.sub(
        lambda nombre: _ESCAPE_HEX.sub(lambda escape: bytes([int(escape.group(1), 16)]), nombre.group(0)), contenido
    )


def _accion_goto(diccionario: bytes) -> bool:
    return bool(_ACCION_GOTO.search(diccionario)) and not _OTRA_ACCION.search(diccionario)


def _open_action_sospechosa(contenido: bytes) -> bool:
    for accion in _OPEN_ACTION.finditer(contenido):
        forma, numero, generacion, diccionario = accion.groups()
        if forma is None:
            return True
        if diccionario is not None and not _accion_goto(diccionario):
            return True
        if numero is not None:
            objeto = re.search(
                rb"(?<!\d)%s\s+%s\s+obj\s*(\[|<<(.{0,512}?)>>\s*endobj)" % (numero, generacion), contenido, re.DOTALL
            )
            if objeto is None or (objeto.group(2) is not None and not _accion_goto(objeto.group(2))):
                return True
    return False


def validar_estructura(contenido: bytes):
    """Valida un PDF sin interpretarlo ni ejecutar contenido activo."""
    if not contenido.startswith(b"%PDF-") or b"%%EOF" not in contenido[-2048:]:
        return "El archivo no tiene una estructura PDF válida."
    if b"/Encrypt" in contenido[:1_048_576]:
        return "No se aceptan PDF cifrados."
    normalizado = _sin_escapes_en_nombres(contenido)
    if (
        _PDF_ACTIVE_CONTENT.search(contenido)
        or _PDF_ACTIVE_CONTENT.search(normalizado)
        or _open_action_sospechosa(normalizado)
    ):
        return "El PDF contiene acciones o archivos incrustados no permitidos."
    return None


def validar_pdf_seguro(archivo):
    error = validar_entrada(archivo)
    if error:
        return error
    archivo.seek(0)
    contenido = archivo.read()
    archivo.seek(0)
    return validar_estructura(contenido)


def tiene_firma(contenido: bytes) -> bool:
    """Hay un diccionario de firma (/ByteRange o /Type /Sig); no se verifica criptográficamente (T9)."""
    return bool(_FIRMA_PDF.search(contenido))


def _config(nombre: str, por_defecto):
    return getattr(settings, nombre, por_defecto)


def extraer_texto(contenido: bytes):
    """
    Texto del PDF con pypdf, en un proceso aparte con tiempo límite. Devuelve (texto, error): error es el
    motivo de rechazo (ilegible, cifrado, contenido activo, demasiadas páginas, contenido desproporcionado
    o tiempo agotado). Si ni siquiera se puede lanzar el proceso es un fallo nuestro, no del documento.
    """
    tiempo = _config("ANTECEDENTES_PDF_TIMEOUT", PDF_TIMEOUT_POR_DEFECTO)
    comando = [
        sys.executable,
        "-I",  # aislado: ni el directorio actual ni variables PYTHON* influyen en lo que se importa
        str(_SCRIPT_LECTOR),
        str(MAX_PAGINAS_LEIDAS),
        str(_config("ANTECEDENTES_PDF_MAX_BYTES_PAGINA", MAX_BYTES_CONTENIDO_PAGINA)),
    ]
    try:
        proceso = subprocess.run(comando, input=contenido, capture_output=True, timeout=tiempo, check=False)
    except subprocess.TimeoutExpired:  # subprocess ya terminó el proceso hijo
        return "", f"El PDF tardó más de {tiempo:g} s en analizarse y se rechazó."
    except OSError:
        raise DomainError("No se pudo analizar el PDF en este momento; inténtalo de nuevo.")
    try:
        resultado = json.loads(proceso.stdout)
        return resultado["texto"], resultado["error"]
    except (ValueError, KeyError, TypeError):  # el lector murió (memoria, error interno) sin responder
        return "", "No se pudo leer el PDF: está dañado o incompleto."
