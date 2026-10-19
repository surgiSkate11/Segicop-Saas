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
    REQUIERE_INTERVENCION = "REQUIERE_INTERVENCION", "Requiere intervención"
    PORTAL_NO_DISPONIBLE = "PORTAL_NO_DISPONIBLE", "Portal no disponible"
    CARGA_MANUAL_PENDIENTE = "CARGA_MANUAL_PENDIENTE", "Carga manual pendiente"
    CARGA_MANUAL_COMPLETADA = "CARGA_MANUAL_COMPLETADA", "Carga manual completada"
    ERROR_DEFINITIVO = "ERROR_DEFINITIVO", "Error definitivo"


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

    persona = models.ForeignKey(
        "personas.Persona", on_delete=models.CASCADE, related_name="verificaciones_antecedentes"
    )
    estado = models.CharField(max_length=30, choices=EstadoVerificacion.choices, default=EstadoVerificacion.SOLICITADA)
    modo_conector = models.CharField(max_length=10, choices=ModoConector.choices)
    resultado = models.CharField(max_length=20, choices=ResultadoAntecedentes.choices, null=True, blank=True)
    numero_certificado = models.CharField(max_length=100, blank=True)
    proveedor = models.CharField(max_length=150, blank=True)
    comentario = models.TextField(blank=True)
    fecha_resultado = models.DateTimeField(null=True, blank=True)
    vigente_hasta = models.DateField(null=True, blank=True)
    cargado_por = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    validado_por = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Verificación de antecedentes"
        verbose_name_plural = "Verificaciones de antecedentes"
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.persona} · {self.get_estado_display()}"


class ProveedorAntecedentes(models.TextChoices):
    MINISTERIO_INTERIOR = "MINISTERIO_INTERIOR", "Ministerio del Interior"
    FUNCION_JUDICIAL = "FUNCION_JUDICIAL", "Función Judicial"


class ConsultaProveedor(BaseModel):
    """Consulta independiente por fuente; nunca mezcla sus semánticas."""

    verificacion = models.ForeignKey(
        VerificacionAntecedentes, on_delete=models.CASCADE, related_name="consultas_proveedor"
    )
    proveedor = models.CharField(max_length=30, choices=ProveedorAntecedentes.choices)
    estado = models.CharField(max_length=35, choices=EstadoVerificacion.choices, default=EstadoVerificacion.EN_COLA)
    intentos = models.PositiveIntegerField(default=0)
    ultimo_intento_at = models.DateTimeField(null=True, blank=True)
    proximo_intento_at = models.DateTimeField(null=True, blank=True)
    codigo_error = models.CharField(max_length=80, blank=True)
    detalle_error = models.TextField(blank=True)
    requiere_intervencion = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["verificacion", "proveedor"],
                name="consulta_unica_por_verificacion_y_proveedor",
            )
        ]
        indexes = [models.Index(fields=["estado", "proximo_intento_at"])]


class IntentoConsulta(BaseModel):
    """Bitácora técnica append-only de cada intento de integración."""

    consulta = models.ForeignKey(ConsultaProveedor, on_delete=models.CASCADE, related_name="intentos_detalle")
    numero = models.PositiveIntegerField()
    estado_resultante = models.CharField(max_length=35, choices=EstadoVerificacion.choices)
    codigo_error = models.CharField(max_length=80, blank=True)
    latencia_ms = models.PositiveIntegerField(null=True, blank=True)
    detalle = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["consulta", "numero"], name="intento_unico_por_consulta")
        ]
        ordering = ["-created_at"]


class DocumentoAntecedentes(BaseModel):
    """Documento en almacenamiento privado, pendiente o aprobado por validación."""

    consulta = models.ForeignKey(ConsultaProveedor, on_delete=models.CASCADE, related_name="documentos")
    archivo = models.FileField(upload_to="antecedentes/staging/%Y/%m/")
    nombre_original = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    estado_validacion = models.CharField(max_length=30, default="PENDIENTE")
    identificacion_extraida = models.CharField(max_length=50, blank=True)
    nombre_extraido = models.CharField(max_length=255, blank=True)
    numero_certificado = models.CharField(max_length=100, blank=True)
    detalle_validacion = models.TextField(blank=True)
    validado_por = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )


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
