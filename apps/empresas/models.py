from django.db import models

from apps.core.choices import EstadoCumplimiento
from apps.core.models import OrganizacionScopedModel
from apps.identidad.models import CuentaAccesoMixin, TipoCuenta


class EmpresaContratista(CuentaAccesoMixin, OrganizacionScopedModel):
    """
    RF-010: razón social, RUC, representante, contactos, estado.
    RF-012: el RUC no se repite dentro de la misma organización cliente.
    Su cuenta de portal entra con el RUC y recibe el acceso en `contacto_email`.
    """

    TIPO_CUENTA = TipoCuenta.EMPRESA
    ROL_CUENTA = "contratista"

    razon_social = models.CharField(max_length=255)
    ruc = models.CharField(max_length=20)
    representante_legal = models.CharField(max_length=255, blank=True)
    contacto_email = models.EmailField(blank=True)
    contacto_telefono = models.CharField(max_length=30, blank=True)

    estado = models.CharField(
        max_length=30,
        choices=EstadoCumplimiento.choices,
        default=EstadoCumplimiento.BORRADOR,
    )

    motivo_estado = models.TextField(
        blank=True,
        help_text="Motivo de la última suspensión/reactivación u observación.",
    )
    suspendida_desde = models.DateTimeField(null=True, blank=True)
    suspendida_hasta = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Vigencia de la suspensión; nula si es indefinida.",
    )

    class Meta:
        verbose_name = "Empresa contratista"
        verbose_name_plural = "Empresas contratistas"
        constraints = [models.UniqueConstraint(fields=["organizacion", "ruc"], name="ruc_unico_por_organizacion")]
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.razon_social} ({self.ruc})"

    @property
    def nombre_usuario_cuenta(self) -> str:
        return self.ruc

    @property
    def correo_cuenta(self) -> str:
        return self.contacto_email

    @property
    def nombre_cuenta(self) -> str:
        return self.razon_social


class Contrato(OrganizacionScopedModel):
    """
    RF-011: base para asignar requisitos según actividad, riesgo,
    instalación o contrato.
    """

    empresa = models.ForeignKey(EmpresaContratista, on_delete=models.PROTECT, related_name="contratos")
    numero = models.CharField(max_length=50)
    objeto = models.TextField(help_text="Descripción del objeto contractual.")
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    instalaciones = models.ManyToManyField("identidad.Instalacion", related_name="contratos", blank=True)
    responsable = models.ForeignKey(
        "identidad.Usuario",
        on_delete=models.PROTECT,
        related_name="contratos_responsable",
        null=True,
        blank=True,
        help_text="Responsable de área asignado a este contrato.",
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"
        constraints = [
            models.UniqueConstraint(
                fields=["organizacion", "empresa", "numero"],
                name="numero_contrato_unico_por_empresa",
            )
        ]

    def __str__(self):
        return f"Contrato {self.numero} · {self.empresa.razon_social}"
