from django.db import models

from apps.core.models import BaseModel, OrganizacionScopedModel


class AplicaA(models.TextChoices):
    EMPRESA = "EMPRESA", "Empresa"
    PERSONA = "PERSONA", "Persona"


class CatalogoRequisito(OrganizacionScopedModel):
    """Catálogo configurable de requisitos documentales por organización."""
    codigo = models.SlugField(max_length=60)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    aplica_a = models.CharField(max_length=10, choices=AplicaA.choices)
    obligatorio = models.BooleanField(
        default=True,
        help_text="Si es obligatorio, su ausencia o rechazo bloquea la habilitación.",
    )
    vigencia_dias = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Días de vigencia antes de considerarse vencido. Vacío = sin vencimiento.",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Requisito del catálogo"
        verbose_name_plural = "Catálogo de requisitos"
        constraints = [
            models.UniqueConstraint(fields=["organizacion", "codigo"], name="codigo_requisito_unico_por_organizacion")
        ]

    def __str__(self):
        return self.nombre


class EstadoRequisito(models.TextChoices):
    """Estado de UN requisito aplicado — distinto del estado corporativo general (RN-007)."""
    PENDIENTE = "PENDIENTE", "Pendiente"
    EN_REVISION = "EN_REVISION", "En revisión"
    OBSERVADO = "OBSERVADO", "Observado"
    APROBADO = "APROBADO", "Aprobado"
    RECHAZADO = "RECHAZADO", "Rechazado"
    VENCIDO = "VENCIDO", "Vencido"


class RequisitoAplicado(OrganizacionScopedModel):
    """Vincula un CatalogoRequisito con UNA empresa o UNA persona (nunca ambas)."""
    catalogo = models.ForeignKey(CatalogoRequisito, on_delete=models.PROTECT, related_name="aplicaciones")
    empresa = models.ForeignKey(
        "empresas.EmpresaContratista", on_delete=models.CASCADE,
        related_name="requisitos_aplicados", null=True, blank=True,
    )
    persona = models.ForeignKey(
        "personas.Persona", on_delete=models.CASCADE,
        related_name="requisitos_aplicados", null=True, blank=True,
    )
    estado = models.CharField(max_length=20, choices=EstadoRequisito.choices, default=EstadoRequisito.PENDIENTE)
    fecha_limite = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Requisito aplicado"
        verbose_name_plural = "Requisitos aplicados"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(empresa__isnull=False, persona__isnull=True)
                    | models.Q(empresa__isnull=True, persona__isnull=False)
                ),
                name="requisito_aplicado_a_empresa_xor_persona",
            )
        ]
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        destino = self.empresa or self.persona
        return f"{self.catalogo.nombre} · {destino} · {self.get_estado_display()}"


class DocumentoVersion(OrganizacionScopedModel):
    """Versión INMUTABLE de un documento (RN-001): renovar = fila nueva, nunca editar."""
    requisito_aplicado = models.ForeignKey(RequisitoAplicado, on_delete=models.CASCADE, related_name="documentos")
    archivo = models.FileField(upload_to="cumplimiento/documentos/%Y/%m/")
    hash_sha256 = models.CharField(max_length=64, editable=False)
    version = models.PositiveIntegerField(editable=False)
    vigente = models.BooleanField(default=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    subido_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, related_name="documentos_subidos")

    class Meta:
        verbose_name = "Versión de documento"
        verbose_name_plural = "Versiones de documento"
        constraints = [
            models.UniqueConstraint(fields=["requisito_aplicado", "version"], name="version_unica_por_requisito")
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.requisito_aplicado} · v{self.version}"


class Revision(OrganizacionScopedModel):
    """Decisión humana sobre UNA versión. Solo-anexado (AC-010): nunca se edita ni borra."""
    class Decision(models.TextChoices):
        APROBADO = "APROBADO", "Aprobado"
        OBSERVADO = "OBSERVADO", "Observado"
        RECHAZADO = "RECHAZADO", "Rechazado"

    documento_version = models.ForeignKey(DocumentoVersion, on_delete=models.PROTECT, related_name="revisiones")
    revisor = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, related_name="revisiones_realizadas")
    decision = models.CharField(max_length=20, choices=Decision.choices)
    comentario = models.TextField(blank=True)

    class Meta:
        verbose_name = "Revisión"
        verbose_name_plural = "Revisiones"

    def __str__(self):
        return f"{self.get_decision_display()} · {self.documento_version} · {self.revisor.email}"