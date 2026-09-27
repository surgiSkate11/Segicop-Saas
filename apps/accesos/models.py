import uuid

from django.db import models

from apps.core.models import BaseModel, OrganizacionScopedModel


class EstadoPermiso(models.TextChoices):
    SOLICITADO = "SOLICITADO", "Solicitado"
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"
    SUSPENDIDO = "SUSPENDIDO", "Suspendido"
    VENCIDO = "VENCIDO", "Vencido"


class PermisoIngreso(OrganizacionScopedModel):
    """
    RF-040/RF-043: la habilitación real de ingreso. Este modelo NUNCA
    calcula nada por sí mismo — `services.calcular_habilitacion()` es
    de solo LECTURA sobre Persona.estado y VerificacionAntecedentes;
    este modelo solo guarda el resultado de esa lectura.
    """
    persona = models.ForeignKey("personas.Persona", on_delete=models.CASCADE, related_name="permisos_ingreso")
    instalacion = models.ForeignKey("identidad.Instalacion", on_delete=models.PROTECT, related_name="permisos_ingreso")
    estado = models.CharField(max_length=20, choices=EstadoPermiso.choices, default=EstadoPermiso.SOLICITADO)
    motivo = models.TextField(blank=True)
    fecha_inicio_vigencia = models.DateField()
    fecha_fin_vigencia = models.DateField()
    codigo = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    aprobado_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "Permiso de ingreso"
        verbose_name_plural = "Permisos de ingreso"
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.persona} · {self.instalacion.nombre} · {self.get_estado_display()}"

    @property
    def vigente_hoy(self) -> bool:
        from django.utils import timezone
        hoy = timezone.now().date()
        return self.fecha_inicio_vigencia <= hoy <= self.fecha_fin_vigencia


class TipoEvento(models.TextChoices):
    ENTRADA = "ENTRADA", "Entrada"
    SALIDA = "SALIDA", "Salida"
    DENEGADO = "DENEGADO", "Denegado"


class EventoAcceso(BaseModel):
    """Solo-anexado: un intento denegado también se registra."""
    permiso_ingreso = models.ForeignKey(PermisoIngreso, on_delete=models.PROTECT, null=True, blank=True, related_name="eventos")
    persona = models.ForeignKey("personas.Persona", on_delete=models.PROTECT, related_name="eventos_acceso")
    instalacion = models.ForeignKey("identidad.Instalacion", on_delete=models.PROTECT, related_name="eventos_acceso")
    tipo = models.CharField(max_length=10, choices=TipoEvento.choices)
    motivo_denegacion = models.TextField(blank=True)
    registrado_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, related_name="eventos_registrados")

    class Meta:
        verbose_name = "Evento de acceso"
        verbose_name_plural = "Eventos de acceso"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_tipo_display()} · {self.persona} · {self.instalacion.nombre}"