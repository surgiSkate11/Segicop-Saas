import uuid
from django.db import models


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class OrganizacionScopedModel(BaseModel):
    organizacion = models.ForeignKey("identidad.Organizacion", on_delete=models.PROTECT, related_name="%(class)ss")

    class Meta(BaseModel.Meta):
        abstract = True


class EventoAuditoria(BaseModel):
    """Registro append-only de mutaciones relevantes del dominio."""

    actor = models.ForeignKey(
        "identidad.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eventos_auditoria",
    )
    organizacion = models.ForeignKey(
        "identidad.Organizacion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="eventos_auditoria",
    )
    accion = models.CharField(max_length=100)
    recurso_tipo = models.CharField(max_length=150)
    recurso_id = models.CharField(max_length=64, blank=True)
    motivo = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    resultado = models.CharField(max_length=30, default="OK")

    class Meta:
        verbose_name = "Evento de auditoría"
        verbose_name_plural = "Eventos de auditoría"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organizacion", "created_at"]),
            models.Index(fields=["accion", "created_at"]),
        ]


class EstadoAlerta(models.TextChoices):
    PENDIENTE = "PENDIENTE", "Pendiente"
    LEIDA = "LEIDA", "Leída"


class AlertaNotificacion(OrganizacionScopedModel):
    """Alerta agrupable e idempotente para la operación diaria."""

    clave_idempotencia = models.CharField(max_length=255, unique=True)
    tipo = models.CharField(max_length=60)
    agrupador = models.CharField(max_length=120)
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    severidad = models.CharField(max_length=20, default="INFO")
    estado = models.CharField(max_length=20, choices=EstadoAlerta.choices, default=EstadoAlerta.PENDIENTE)
    fecha_referencia = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Alerta de notificación"
        verbose_name_plural = "Alertas de notificación"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organizacion", "estado"]),
            models.Index(fields=["organizacion", "agrupador", "estado"]),
        ]


class ExportacionAuditoria(BaseModel):
    """Evidencia de cada reporte generado para una operación auditable."""

    actor = models.ForeignKey(
        "identidad.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exportaciones_auditoria",
    )
    organizacion = models.ForeignKey(
        "identidad.Organizacion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="exportaciones_auditoria",
    )
    tipo = models.CharField(max_length=80)
    formato = models.CharField(max_length=20)
    filtros = models.JSONField(default=dict, blank=True)
    filas = models.PositiveIntegerField(default=0)
    hash_sha256 = models.CharField(max_length=64)

    class Meta:
        verbose_name = "Exportación auditada"
        verbose_name_plural = "Exportaciones auditadas"
        ordering = ["-created_at"]


class ConfiguracionCorreo(BaseModel):
    """
    S11: servidor SMTP y remitente de los correos del sistema, editable
    desde el panel administrativo. Solo una puede estar activa; sin ninguna
    activa se usan los settings (EMAIL_BACKEND, DEFAULT_FROM_EMAIL).
    La clave SMTP se guarda en la BD: restringir el admin a superusuarios.
    """

    activa = models.BooleanField(default=False)
    host = models.CharField("Servidor SMTP", max_length=255)
    puerto = models.PositiveIntegerField(default=587)
    usuario = models.CharField("Usuario SMTP", max_length=255, blank=True)
    password = models.CharField("Clave SMTP", max_length=255, blank=True)
    usar_tls = models.BooleanField("Usar TLS (STARTTLS)", default=True)
    usar_ssl = models.BooleanField("Usar SSL", default=False)
    timeout = models.PositiveIntegerField("Tiempo de espera (s)", default=10)
    remitente_nombre = models.CharField("Nombre del remitente", max_length=120, default="SIGECOP")
    remitente_email = models.EmailField("Correo del remitente")

    class Meta:
        verbose_name = "Configuración de correo"
        verbose_name_plural = "Configuración de correo"
        constraints = [
            models.UniqueConstraint(
                fields=["activa"], condition=models.Q(activa=True), name="una_configuracion_correo_activa"
            )
        ]

    def __str__(self):
        return f"{self.remitente_email} vía {self.host}:{self.puerto}{' (activa)' if self.activa else ''}"

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.usar_tls and self.usar_ssl:
            raise ValidationError("Usa TLS o SSL, no ambos.")

    @property
    def remitente(self) -> str:
        return f"{self.remitente_nombre} <{self.remitente_email}>" if self.remitente_nombre else self.remitente_email

    @classmethod
    def vigente(cls):
        return cls.objects.filter(activa=True).first()
