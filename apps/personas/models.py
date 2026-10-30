from django.db import models

from apps.core.choices import EstadoCumplimiento
from apps.core.models import OrganizacionScopedModel
from apps.identidad.models import CuentaAccesoMixin, TipoCuenta


class TipoDocumento(models.TextChoices):
    CEDULA = "CEDULA", "Cédula"
    PASAPORTE = "PASAPORTE", "Pasaporte"


class Persona(CuentaAccesoMixin, OrganizacionScopedModel):
    """
    RF-020/021: identidad única dentro de la organización.
    RF-022: coincidencias detectadas en services.py.
    `estado` es el estado DOCUMENTAL de la persona — nunca se fusiona
    con el resultado de antecedentes ni con la habilitación final
    (RN-007), esos viven en apps futuras que solo leerán este campo.
    Su cuenta de portal entra con el número de documento.
    """

    TIPO_CUENTA = TipoCuenta.PERSONA
    ROL_CUENTA = "trabajador"

    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices)
    numero_documento = models.CharField(max_length=30)
    nombres = models.CharField(max_length=150)
    apellidos = models.CharField(max_length=150)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    contacto_telefono = models.CharField(max_length=30, blank=True)
    contacto_email = models.EmailField(blank=True)
    # T13 / S11: fields of the previous system. The position lives per company in Asignacion.cargo_o_actividad.
    nacionalidad = models.CharField(max_length=60, blank=True, db_default="")
    domicilio = models.CharField(max_length=255, blank=True, db_default="")
    telefono_emergencia = models.CharField("teléfono de emergencia", max_length=30, blank=True, db_default="")
    estado = models.CharField(max_length=30, choices=EstadoCumplimiento.choices, default=EstadoCumplimiento.BORRADOR)

    class Meta:
        verbose_name = "Persona"
        verbose_name_plural = "Personas"
        constraints = [
            models.UniqueConstraint(
                fields=["organizacion", "tipo_documento", "numero_documento"],
                name="documento_unico_por_organizacion",
            )
        ]
        indexes = [models.Index(fields=["organizacion", "estado"])]

    def __str__(self):
        return f"{self.nombres} {self.apellidos} ({self.numero_documento})"

    @property
    def nombre_completo(self):
        return f"{self.nombres} {self.apellidos}"

    @property
    def nombre_usuario_cuenta(self) -> str:
        return self.numero_documento

    @property
    def correo_cuenta(self) -> str:
        return self.contacto_email

    @property
    def nombre_cuenta(self) -> str:
        return self.nombre_completo


class Asignacion(OrganizacionScopedModel):
    """Vincula Persona <-> EmpresaContratista (y opcionalmente Contrato)."""

    persona = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="asignaciones")
    empresa = models.ForeignKey("empresas.EmpresaContratista", on_delete=models.PROTECT, related_name="asignaciones")
    contrato = models.ForeignKey(
        "empresas.Contrato", on_delete=models.PROTECT, related_name="asignaciones", null=True, blank=True
    )
    cargo_o_actividad = models.CharField(max_length=150)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField(null=True, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Asignación"
        verbose_name_plural = "Asignaciones"
        indexes = [models.Index(fields=["organizacion", "activa"])]

    def __str__(self):
        return f"{self.persona.nombre_completo} · {self.empresa.razon_social} ({self.cargo_o_actividad})"
