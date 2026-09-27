from django.db import models

from apps.core.models import OrganizacionScopedModel


class NivelRiesgo(models.TextChoices):
    BAJO = "BAJO", "Bajo"
    MEDIO = "MEDIO", "Medio"
    ALTO = "ALTO", "Alto"
    CRITICO = "CRITICO", "Crítico"


class EstadoTrabajo(models.TextChoices):
    SOLICITADO = "SOLICITADO", "Solicitado"
    RECHAZADO = "RECHAZADO", "Rechazado"
    ACTIVO = "ACTIVO", "Activo"
    SUSPENDIDO = "SUSPENDIDO", "Suspendido"
    CERRADO = "CERRADO", "Cerrado"


class PermisoTrabajo(OrganizacionScopedModel):
    """
    RF-050 a RF-053. Ciclo: SOLICITADO -> ACTIVO (al abrir) ->
    SUSPENDIDO/CERRADO. `abrir`/`reanudar` revalidan a CADA integrante
    en ese momento (RF-053).
    """
    empresa = models.ForeignKey("empresas.EmpresaContratista", on_delete=models.PROTECT, related_name="permisos_trabajo")
    contrato = models.ForeignKey("empresas.Contrato", on_delete=models.PROTECT, null=True, blank=True, related_name="permisos_trabajo")
    instalacion = models.ForeignKey("identidad.Instalacion", on_delete=models.PROTECT, related_name="permisos_trabajo")
    actividad = models.TextField()
    nivel_riesgo = models.CharField(max_length=10, choices=NivelRiesgo.choices, default=NivelRiesgo.MEDIO)
    integrantes = models.ManyToManyField("personas.Persona", related_name="permisos_trabajo")
    responsable_cuadrilla = models.ForeignKey("personas.Persona", on_delete=models.PROTECT, null=True, blank=True, related_name="permisos_trabajo_como_responsable")
    estado = models.CharField(max_length=20, choices=EstadoTrabajo.choices, default=EstadoTrabajo.SOLICITADO)
    motivo = models.TextField(blank=True)
    fecha_inicio_programada = models.DateField()
    fecha_fin_programada = models.DateField()
    fecha_apertura_real = models.DateTimeField(null=True, blank=True)
    fecha_cierre_real = models.DateTimeField(null=True, blank=True)
    aprobado_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        verbose_name = "Permiso de trabajo"
        verbose_name_plural = "Permisos de trabajo"
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.actividad[:40]} · {self.instalacion.nombre} · {self.get_estado_display()}"