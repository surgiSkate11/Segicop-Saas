from django.db import models

from apps.core.models import BaseModel, OrganizacionScopedModel


class EstadoVerificacion(models.TextChoices):
    SOLICITADA = "SOLICITADA", "Solicitada"
    EN_COLA = "EN_COLA", "En cola"
    EN_PROCESO = "EN_PROCESO", "En proceso"
    REINTENTANDO = "REINTENTANDO", "Reintentando"
    FALLIDA_TECNICA = "FALLIDA_TECNICA", "Fallida (técnica)"
    RESULTADO_SI_OBTENIDO = "RESULTADO_SI_OBTENIDO", "Resultado SI obtenido"
    RESULTADO_NO_OBTENIDO = "RESULTADO_NO_OBTENIDO", "Resultado NO obtenido"
    RESULTADO_NO_DETERMINADO = "RESULTADO_NO_DETERMINADO", "Resultado no determinado"
    CONTINGENCIA_MANUAL = "CONTINGENCIA_MANUAL", "Contingencia manual"
    EN_REVISION_MANUAL = "EN_REVISION_MANUAL", "En revisión manual"
    VALIDADA = "VALIDADA", "Validada"
    OBSERVADA = "OBSERVADA", "Observada"
    RECHAZADA = "RECHAZADA", "Rechazada"
    VENCIDA = "VENCIDA", "Vencida"


class ModoConector(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    ASISTIDO = "ASISTIDO", "Asistido"


class ResultadoAntecedentes(models.TextChoices):
    SI = "SI", "Sí (hallazgos)"
    NO = "NO", "No (sin hallazgos)"
    NO_DETERMINADO = "NO_DETERMINADO", "No determinado"


class VerificacionAntecedentes(OrganizacionScopedModel):
    """
    RN-007 / ANT-014: el resultado NUNCA se fusiona con la habilitación
    final (apps.accesos, Fase 7) ni con el estado documental
    (apps.cumplimiento). Cada intento es una fila NUEVA — nunca se
    reescribe una fila existente para "reintentar".

    `resultado` y `numero_certificado` quedan marcados como PENDIENTES
    de cifrado a nivel de aplicación antes de producción.
    """
    persona = models.ForeignKey("personas.Persona", on_delete=models.CASCADE, related_name="verificaciones_antecedentes")
    estado = models.CharField(max_length=30, choices=EstadoVerificacion.choices, default=EstadoVerificacion.SOLICITADA)
    modo_conector = models.CharField(max_length=10, choices=ModoConector.choices)
    resultado = models.CharField(max_length=20, choices=ResultadoAntecedentes.choices, null=True, blank=True)
    numero_certificado = models.CharField(max_length=100, blank=True)
    proveedor = models.CharField(max_length=150, blank=True)
    comentario = models.TextField(blank=True)
    fecha_resultado = models.DateTimeField(null=True, blank=True)
    vigente_hasta = models.DateField(null=True, blank=True)
    cargado_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    validado_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "Verificación de antecedentes"
        verbose_name_plural = "Verificaciones de antecedentes"
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.persona} · {self.get_estado_display()}"


class EjecucionConector(BaseModel):
    """Bitácora de solo-anexado; su organización se alcanza vía verificacion.organizacion."""
    verificacion = models.ForeignKey(VerificacionAntecedentes, on_delete=models.CASCADE, related_name="ejecuciones")
    estado_resultante = models.CharField(max_length=30, choices=EstadoVerificacion.choices)
    exito = models.BooleanField(default=True)
    detalle = models.TextField(blank=True)

    class Meta:
        verbose_name = "Ejecución de conector"
        verbose_name_plural = "Ejecuciones de conector"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.verificacion} -> {self.estado_resultante}"