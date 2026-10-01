class DomainError(Exception):
    codigo = "DOMAIN_ERROR"


class TransicionEstadoInvalida(DomainError):
    codigo = "INVALID_STATE_TRANSITION"


class AlcancePermisoInsuficiente(DomainError):
    codigo = "INSUFFICIENT_SCOPE"


class OperacionDuplicada(DomainError):
    codigo = "DUPLICATE_OPERATION"
