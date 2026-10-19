from rest_framework import serializers

from apps.antecedentes.models import ConsultaProveedor, DocumentoAntecedentes, VerificacionAntecedentes

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
