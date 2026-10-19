from rest_framework import serializers

from apps.antecedentes.models import ConsultaProveedor, DocumentoAntecedentes, VerificacionAntecedentes


class DocumentoAntecedentesSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentoAntecedentes
        fields = (
            "id",
            "nombre_original",
            "mime_type",
            "tamano_bytes",
            "sha256",
            "estado_validacion",
            "identificacion_extraida",
            "nombre_extraido",
            "numero_certificado",
            "detalle_validacion",
            "created_at",
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


class VerificacionAntecedentesSerializer(serializers.ModelSerializer):
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
            "resultado",
            "fecha_resultado",
            "vigente_hasta",
            "created_at",
            "updated_at",
            "consultas_proveedor",
        )
