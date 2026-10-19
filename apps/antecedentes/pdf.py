"""
Validación y lectura de los PDF de antecedentes (S7: "Aceptar solo PDF válido, dentro del límite y analizado").

No hay antivirus: el "análisis" es estructural (firmas del formato, cifrado, contenido activo) más la
lectura con pypdf, que no ejecuta nada del documento.
"""

import io
import logging
import re

from pypdf import PdfReader

from apps.core.exceptions import DomainError

logging.getLogger("pypdf").setLevel(logging.ERROR)  # un PDF dañado no debe inundar el registro

ANT_DOCUMENT_INVALID = "ANT_DOCUMENT_INVALID"
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_PAGINAS_LEIDAS = 10  # el certificado es de una página; más no aporta datos
_PDF_ACTIVE_CONTENT = re.compile(
    rb"/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia|SubmitForm|GoToR|OpenAction)\b",
    re.IGNORECASE,
)
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


def validar_estructura(contenido: bytes):
    """Valida un PDF sin interpretarlo ni ejecutar contenido activo."""
    if not contenido.startswith(b"%PDF-") or b"%%EOF" not in contenido[-2048:]:
        return "El archivo no tiene una estructura PDF válida."
    if b"/Encrypt" in contenido[:1_048_576]:
        return "No se aceptan PDF cifrados."
    if _PDF_ACTIVE_CONTENT.search(contenido):
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


def extraer_texto(contenido: bytes):
    """Texto del PDF con pypdf. Devuelve (texto, error): error es el motivo si no se pudo leer."""
    try:
        lector = PdfReader(io.BytesIO(contenido), strict=False)
        if lector.is_encrypted:
            return "", "No se aceptan PDF cifrados."
        paginas = lector.pages[:MAX_PAGINAS_LEIDAS]
        texto = "\n".join((pagina.extract_text() or "") for pagina in paginas)
    except Exception:  # pypdf lanza muchos tipos distintos ante archivos dañados
        return "", "No se pudo leer el PDF: está dañado o incompleto."
    if not texto.strip():
        return "", "El PDF no contiene texto legible (¿es una imagen escaneada o está dañado?)."
    return texto, None
