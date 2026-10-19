from rest_framework import serializers

from apps.antecedentes.models import (
    ConsultaProveedor,
    DocumentoAntecedentes,
    EstadoVerificacion,
    FinalidadConsulta,
    VerificacionAntecedentes,
)

# ANT-011: el operador nunca recibe el resultado ni lo extraído del certificado; solo el revisor.


class DocumentoAntecedentesSerializer(serializers.ModelSerializer):
    """Vista del operador: datos operativos del archivo."""

    class Meta:
        model = DocumentoAntecedentes
        fields = ("id", "nombre_original", "mime_type", "tamano_bytes", "sha256", "estado_validacion", "created_at")


class DocumentoRevisorSerializer(DocumentoAntecedentesSerializer):
    class Meta(DocumentoAntecedentesSerializer.Meta):
        fields = DocumentoAntecedentesSerializer.Meta.fields + (
            "identificacion_extraida",
            "nombre_extraido",
            "numero_certificado",
            "resultado_extraido",
            "fecha_emision",
            "vigente_hasta_extraida",
            "firma_detectada",
            "codigo_error",
            "detalle_validacion",
        )


class ConsultaProveedorSerializer(serializers.ModelSerializer):
    documentos = DocumentoAntecedentesSerializer(many=True, read_only=True)

    class Meta:
        model = ConsultaProveedor
        fields = (
            "id",
            "proveedor",
            "estado",
            "intentos",
            "ultimo_intento_at",
            "proximo_intento_at",
            "codigo_error",
            "detalle_error",
            "requiere_intervencion",
            "documentos",
        )


class ConsultaRevisorSerializer(ConsultaProveedorSerializer):
    documentos = DocumentoRevisorSerializer(many=True, read_only=True)


class VerificacionOperadorSerializer(serializers.ModelSerializer):
    consultas_proveedor = ConsultaProveedorSerializer(many=True, read_only=True)
    persona_nombre = serializers.CharField(source="persona.nombre_completo", read_only=True)

    class Meta:
        model = VerificacionAntecedentes
        fields = (
            "id",
            "persona",
            "persona_nombre",
            "finalidad",
            "autorizacion",
            "clave_idempotencia",
            "estado",
            "modo_conector",
            "vigente_hasta",
            "created_at",
            "updated_at",
            "consultas_proveedor",
        )


class VerificacionRevisorSerializer(VerificacionOperadorSerializer):
    consultas_proveedor = ConsultaRevisorSerializer(many=True, read_only=True)

    class Meta(VerificacionOperadorSerializer.Meta):
        fields = VerificacionOperadorSerializer.Meta.fields + ("resultado", "fecha_resultado")


def serializador_verificacion(es_revisor: bool):
    return VerificacionRevisorSerializer if es_revisor else VerificacionOperadorSerializer


# --- Entradas de la API /api/v1/verificaciones-antecedentes (S10) -------------------------------------------


class CrearVerificacionEntrada(serializers.Serializer):
    personaId = serializers.UUIDField()
    finalidad = serializers.ChoiceField(choices=FinalidadConsulta.choices, default=FinalidadConsulta.HABILITACION_INGRESO)
    # Debe ser la autorización vigente de la persona para la finalidad (lo comprueba la vista).
    autorizacionId = serializers.UUIDField(required=False)
    # Solo se valida: el conector configurado decide cómo se consulta.
    modoPreferido = serializers.ChoiceField(choices=("OFICIAL_O_ASISTIDO", "ASISTIDO", "MANUAL"), required=False)

    def validate(self, datos):
        desconocidos = sorted(set(self.initial_data) - set(self.fields))
        if desconocidos:
            raise serializers.ValidationError({desconocidos[0]: "Campo no admitido."})
        return datos


class RevisionEntrada(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[EstadoVerificacion.VALIDADA, EstadoVerificacion.LISTA, EstadoVerificacion.CANCELADA]
    )
    comentario = serializers.CharField(required=False, allow_blank=True, default="")
    vigenteHasta = serializers.DateField(required=False, allow_null=True, default=None)
