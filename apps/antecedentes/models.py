import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models

from apps.core.models import BaseModel, OrganizacionScopedModel


class EstadoVerificacion(models.TextChoices):
    CREADA = "CREADA", "Creada"
    AUTORIZACION_PENDIENTE = "AUTORIZACION_PENDIENTE", "Autorización pendiente"
    LISTA = "LISTA", "Lista"
    EN_CONSULTA = "EN_CONSULTA", "En consulta"
    CAPTCHA_REQUERIDO = "CAPTCHA_REQUERIDO", "Captcha requerido"
    RESULTADO_OBTENIDO = "RESULTADO_OBTENIDO", "Resultado obtenido"
    PDF_RECIBIDO = "PDF_RECIBIDO", "PDF recibido"
    DATOS_COINCIDENTES = "DATOS_COINCIDENTES", "Datos coincidentes"
    AUTENTICIDAD_PENDIENTE = "AUTENTICIDAD_PENDIENTE", "Autenticidad pendiente"
    VALIDADA = "VALIDADA", "Validada"
    REQUIERE_REVISION = "REQUIERE_REVISION", "Requiere revisión"
    FALLIDA = "FALLIDA", "Fallida"
    VENCIDA = "VENCIDA", "Vencida"
    CANCELADA = "CANCELADA", "Cancelada"


# Estados que ya no cuentan como solicitud en curso (ANT-002): permiten abrir una nueva.
ESTADOS_CERRADOS = (
    EstadoVerificacion.VALIDADA,
    EstadoVerificacion.VENCIDA,
    EstadoVerificacion.CANCELADA,
    EstadoVerificacion.FALLIDA,
)


class FinalidadConsulta(models.TextChoices):
    HABILITACION_INGRESO = "HABILITACION_INGRESO", "Habilitación de ingreso a instalaciones"
    RENOVACION = "RENOVACION", "Renovación por vencimiento"


class MedioAutorizacion(models.TextChoices):
    DOCUMENTO_FIRMADO = "DOCUMENTO_FIRMADO", "Documento firmado"
    PORTAL = "PORTAL", "Portal"
    OTRO = "OTRO", "Otro"


def generar_clave_idempotencia():
    return uuid.uuid4().hex


class EstadoConsulta(models.TextChoices):
    """Estado técnico de la consulta a una fuente; no es el de la verificación."""

    PENDIENTE = "PENDIENTE", "Pendiente"
    EN_CURSO = "EN_CURSO", "En curso"
    CAPTCHA_REQUERIDO = "CAPTCHA_REQUERIDO", "Captcha requerido"
    REINTENTO_PROGRAMADO = "REINTENTO_PROGRAMADO", "Reintento programado"
    COMPLETADA = "COMPLETADA", "Completada"
    FALLIDA = "FALLIDA", "Fallida"


# Las bitácoras son de solo-anexado (RN-001): conservan los estados anteriores a la migración 0003.
ESTADOS_HISTORICOS = {
    "SOLICITADA": "Solicitada",
    "EN_COLA": "En cola",
    "EN_PROCESO": "En proceso",
    "REINTENTANDO": "Reintentando",
    "FALLIDA_TECNICA": "Fallida (técnica)",
    "RESULTADO_SI_OBTENIDO": "Resultado SI obtenido",
    "RESULTADO_NO_OBTENIDO": "Resultado NO obtenido",
    "RESULTADO_NO_DETERMINADO": "Resultado no determinado",
    "CONTINGENCIA_MANUAL": "Contingencia manual",
    "EN_REVISION_MANUAL": "En revisión manual",
    "OBSERVADA": "Observada",
    "RECHAZADA": "Rechazada",
    "REQUIERE_INTERVENCION": "Requiere intervención",
    "PORTAL_NO_DISPONIBLE": "Portal no disponible",
    "CARGA_MANUAL_PENDIENTE": "Carga manual pendiente",
    "CARGA_MANUAL_COMPLETADA": "Carga manual completada",
    "ERROR_DEFINITIVO": "Error definitivo",
}


class ModoConector(models.TextChoices):
    MANUAL = "MANUAL", "Manual"
    ASISTIDO = "ASISTIDO", "Asistido"


class ResultadoAntecedentes(models.TextChoices):
    SI = "SI", "Sí (hallazgos)"
    NO = "NO", "No (sin hallazgos)"
    NO_DETERMINADO = "NO_DETERMINADO", "No determinado"


class AutorizacionDatos(OrganizacionScopedModel):
    """
    ANT-001: consentimiento de la persona para consultar sus antecedentes.
    Append-only (RN-001): tras crearse solo cambian los campos de revocación,
    y únicamente a través de `services.revocar_autorizacion`.
    """

    persona = models.ForeignKey("personas.Persona", on_delete=models.PROTECT, related_name="autorizaciones_datos")
    finalidad = models.CharField(max_length=30, choices=FinalidadConsulta.choices)
    texto = models.TextField(help_text="Texto del consentimiento tal como lo aceptó la persona.")
    version = models.CharField(max_length=30)
    medio = models.CharField(max_length=20, choices=MedioAutorizacion.choices)
    referencia_evidencia = models.CharField(max_length=255, blank=True)
    otorgada_en = models.DateField()
    registrada_por = models.ForeignKey("identidad.Usuario", on_delete=models.PROTECT, related_name="+")
    revocada_en = models.DateTimeField(null=True, blank=True)
    motivo_revocacion = models.TextField(blank=True)

    class Meta:
        verbose_name = "Autorización de datos"
        verbose_name_plural = "Autorizaciones de datos"
        indexes = [models.Index(fields=["persona", "finalidad"])]

    def __str__(self):
        return f"{self.persona} · {self.get_finalidad_display()}"


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
    finalidad = models.CharField(
        max_length=30, choices=FinalidadConsulta.choices, default=FinalidadConsulta.HABILITACION_INGRESO
    )
    autorizacion = models.ForeignKey(
        AutorizacionDatos, on_delete=models.PROTECT, null=True, blank=True, related_name="verificaciones"
    )
    solicitante = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    responsable = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    clave_idempotencia = models.CharField(max_length=64, unique=True, default=generar_clave_idempotencia)
    estado = models.CharField(max_length=30, choices=EstadoVerificacion.choices, default=EstadoVerificacion.CREADA)
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
        constraints = [
            # ANT-002: una sola solicitud en curso por persona y finalidad.
            models.UniqueConstraint(
                fields=["persona", "finalidad"],
                condition=~models.Q(estado__in=ESTADOS_CERRADOS),
                name="una_solicitud_activa_por_persona_y_finalidad",
            )
        ]

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
    estado = models.CharField(max_length=35, choices=EstadoConsulta.choices, default=EstadoConsulta.PENDIENTE)
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


class SaludFuente(BaseModel):
    """
    Disyuntor (circuit breaker) por fuente: tras varios fallos seguidos, o si la fuente
    pide esperar (429), no se la consulta hasta `abierto_hasta`. Visible como salud de la integración.
    """

    proveedor = models.CharField(max_length=30, choices=ProveedorAntecedentes.choices, unique=True)
    fallos_consecutivos = models.PositiveIntegerField(default=0)
    abierto_hasta = models.DateTimeField(null=True, blank=True)
    ultimo_codigo = models.CharField(max_length=80, blank=True)
    ultima_evidencia = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Salud de fuente"
        verbose_name_plural = "Salud de fuentes"

    def __str__(self):
        return self.get_proveedor_display()


class IntentoConsulta(BaseModel):
    """Bitácora técnica append-only de cada intento de integración."""

    consulta = models.ForeignKey(ConsultaProveedor, on_delete=models.CASCADE, related_name="intentos_detalle")
    numero = models.PositiveIntegerField()
    estado_resultante = models.CharField(max_length=35)  # sin choices: conserva estados históricos
    codigo_error = models.CharField(max_length=80, blank=True)
    latencia_ms = models.PositiveIntegerField(null=True, blank=True)
    detalle = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["consulta", "numero"], name="intento_unico_por_consulta")
        ]
        ordering = ["-created_at"]


class AlmacenPrivado(FileSystemStorage):
    """
    S7: los certificados nunca se sirven desde /media/. Viven bajo ANTECEDENTES_PRIVATE_ROOT, sin URL
    pública, y solo se leen con la vista de descarga autenticada. La raíz se lee en cada uso (pruebas).
    """

    @property
    def base_location(self):
        return str(settings.ANTECEDENTES_PRIVATE_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None


class DocumentoAntecedentes(BaseModel):
    """Documento en almacenamiento privado, pendiente o aprobado por validación."""

    consulta = models.ForeignKey(ConsultaProveedor, on_delete=models.CASCADE, related_name="documentos")
    archivo = models.FileField(upload_to="%Y/%m/", storage=AlmacenPrivado())
    nombre_original = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    estado_validacion = models.CharField(max_length=30, default="PENDIENTE")
    identificacion_extraida = models.CharField(max_length=50, blank=True)
    nombre_extraido = models.CharField(max_length=255, blank=True)
    numero_certificado = models.CharField(max_length=100, blank=True)
    resultado_extraido = models.CharField(max_length=20, blank=True)
    fecha_emision = models.DateField(null=True, blank=True)
    vigente_hasta_extraida = models.DateField(null=True, blank=True)
    firma_detectada = models.BooleanField(default=False)  # diccionario de firma presente; no verificado (T9)
    codigo_error = models.CharField(max_length=80, blank=True)

    def get_resultado_extraido_display(self):
        if self.resultado_extraido in ResultadoAntecedentes.values:
            return ResultadoAntecedentes(self.resultado_extraido).label
        return self.resultado_extraido or "No reconocido"
    detalle_validacion = models.TextField(blank=True)
    validado_por = models.ForeignKey(
        "identidad.Usuario", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )


class EjecucionConector(BaseModel):
    """Bitácora de solo-anexado; su organización se alcanza vía verificacion.organizacion."""

    verificacion = models.ForeignKey(VerificacionAntecedentes, on_delete=models.CASCADE, related_name="ejecuciones")
    estado_resultante = models.CharField(max_length=30)  # sin choices: conserva estados históricos
    exito = models.BooleanField(default=True)
    detalle = models.TextField(blank=True)

    class Meta:
        verbose_name = "Ejecución de conector"
        verbose_name_plural = "Ejecuciones de conector"
        ordering = ["-created_at"]

    def get_estado_resultante_display(self):
        if self.estado_resultante in EstadoVerificacion.values:
            return EstadoVerificacion(self.estado_resultante).label
        return ESTADOS_HISTORICOS.get(self.estado_resultante, self.estado_resultante)

    def __str__(self):
        return f"{self.verificacion} -> {self.estado_resultante}"
