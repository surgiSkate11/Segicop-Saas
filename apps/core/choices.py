from django.db import models


class EstadoCumplimiento(models.TextChoices):
    """
    Estados de cumplimiento corporativo/personal (tabla "Estados de
    cumplimiento" del documento). Lo usan EmpresaContratista.estado y,
    más adelante, Persona.estado.
    """
    BORRADOR = "BORRADOR", "Borrador"
    INCOMPLETO = "INCOMPLETO", "Incompleto"
    EN_REVISION = "EN_REVISION", "En revisión"
    OBSERVADO = "OBSERVADO", "Observado"
    HABILITADO = "HABILITADO", "Habilitado"
    HABILITADO_CON_CONDICIONES = ("HABILITADO_CON_CONDICIONES", "Habilitado con condiciones")
    SUSPENDIDO = "SUSPENDIDO", "Suspendido"
    VENCIDO = "VENCIDO", "Vencido"
    NO_HABILITADO = "NO_HABILITADO", "No habilitado"