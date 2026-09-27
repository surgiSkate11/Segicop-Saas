import hashlib


def calcular_hash_archivo(archivo) -> str:
    """
    RN-001: cada versión de documento se identifica por su hash ANTES
    de subirse. No consume el puntero permanentemente (se rebobina).
    """
    hasher = hashlib.sha256()
    archivo.seek(0)
    for bloque in archivo.chunks():
        hasher.update(bloque)
    archivo.seek(0)
    return hasher.hexdigest()