"""
Formatos de la evidencia de la Función Judicial (T12): el PDF o la captura de la búsqueda en e-SATJE.

Una captura solo se identifica por su firma de formato: SIGECOP nunca lee ni interpreta su contenido.
El PDF pasa por las mismas comprobaciones que el certificado (`pdf.py`).
"""

TIPOS_POR_EXTENSION = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
ACEPTA = "application/pdf,image/png,image/jpeg"
_FIRMAS = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


def tipo_mime(nombre: str) -> str:
    extension = "." + str(nombre).lower().rsplit(".", 1)[-1] if "." in str(nombre) else ""
    return TIPOS_POR_EXTENSION.get(extension, "")


def extension(mime_type: str) -> str:
    """Extensión del nombre propio en el almacén; un tipo desconocido no lleva extensión."""
    return {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}.get(mime_type, "")


def motivo_imagen_invalida(contenido: bytes, mime_type: str) -> str | None:
    """None si los bytes empiezan con la firma del formato que dice el nombre."""
    if contenido.startswith(_FIRMAS[mime_type]):
        return None
    return "El archivo no es una imagen PNG o JPEG válida."
