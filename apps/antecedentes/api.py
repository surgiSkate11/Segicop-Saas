import hashlib
import re
import uuid

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.antecedentes import permisos
from apps.antecedentes.models import (
    ConsultaProveedor,
    DocumentoAntecedentes,
    EstadoConsulta,
    FinalidadConsulta,
)
from apps.antecedentes.serializers import serializador_verificacion
from apps.antecedentes.services import cambiar_estado_consulta, iniciar_verificacion, registrar_lectura
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_PDF_ACTIVE_CONTENT = re.compile(
    rb"/(?:JavaScript|JS|Launch|EmbeddedFile|RichMedia|SubmitForm|GoToR|OpenAction)\b",
    re.IGNORECASE,
)


def _validar_pdf_seguro(archivo):
    """Valida un PDF sin interpretarlo ni ejecutar contenido activo."""
    nombre = str(archivo.name or "")
    if (
        "\x00" in nombre
        or any(ord(caracter) < 32 for caracter in nombre)
        or not nombre.lower().endswith(".pdf")
        or len(nombre) > 255
    ):
        return "El nombre del archivo no es válido."
    if archivo.size <= 0 or archivo.size > MAX_DOCUMENT_BYTES:
        return "El archivo debe tener entre 1 byte y 10 MB."

    archivo.seek(0)
    contenido = archivo.read()
    archivo.seek(0)
    if not contenido.startswith(b"%PDF-") or b"%%EOF" not in contenido[-2048:]:
        return "El archivo no tiene una estructura PDF válida."
    if b"/Encrypt" in contenido[:1_048_576]:
        return "No se aceptan PDF cifrados."
    if _PDF_ACTIVE_CONTENT.search(contenido):
        return "El PDF contiene acciones o archivos incrustados no permitidos."
    return None


def _nombre_seguro_pdf():
    return f"antecedente-{uuid.uuid4().hex}.pdf"


class IniciarVerificacionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, persona_pk):
        persona = get_object_or_404(Persona, pk=persona_pk)
        permisos.exigir_operar(request.user, persona.organizacion_id)
        try:
            verificacion, creada = iniciar_verificacion(
                persona=persona,
                actor=request.user,
                finalidad=request.data.get("finalidad") or FinalidadConsulta.HABILITACION_INGRESO,
                clave_idempotencia=str(request.data.get("clave_idempotencia") or ""),
            )
        except DomainError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        serializador = serializador_verificacion(permisos.puede_revisar(request.user, verificacion.organizacion_id))
        return Response(
            serializador(verificacion).data,
            status=status.HTTP_202_ACCEPTED if creada else status.HTTP_200_OK,
        )


class VerificacionDetalleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        verificacion = get_object_or_404(
            permisos.verificaciones_visibles(request.user).prefetch_related("consultas_proveedor__documentos"), pk=pk
        )
        es_revisor = permisos.puede_revisar(request.user, verificacion.organizacion_id)
        registrar_lectura(usuario=request.user, verificacion=verificacion, resultado_visible=es_revisor)
        return Response(serializador_verificacion(es_revisor)(verificacion).data)


class CargarDocumentoAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request, consulta_pk):
        consulta = get_object_or_404(ConsultaProveedor.objects.select_related("verificacion__persona"), pk=consulta_pk)
        permisos.exigir_operar(request.user, consulta.verificacion.organizacion_id)
        archivo = request.FILES.get("archivo")
        if not archivo:
            return Response({"detail": "El campo archivo es obligatorio."}, status=status.HTTP_400_BAD_REQUEST)
        error_validacion = _validar_pdf_seguro(archivo)
        if error_validacion:
            return Response({"detail": error_validacion}, status=status.HTTP_400_BAD_REQUEST)

        digest = hashlib.sha256()
        for bloque in archivo.chunks():
            digest.update(bloque)
        archivo.seek(0)
        nombre_original = archivo.name
        archivo.name = _nombre_seguro_pdf()
        with transaction.atomic():
            documento = DocumentoAntecedentes.objects.create(
                consulta=consulta,
                archivo=archivo,
                nombre_original=nombre_original,
                mime_type="application/pdf",
                tamano_bytes=archivo.size,
                sha256=digest.hexdigest(),
                estado_validacion="PENDIENTE",
                detalle_validacion=(
                    "PDF recibido y pendiente de validación de identidad, contenido y autenticidad."
                ),
            )
            cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.COMPLETADA)
        return Response(
            {"documento_id": str(documento.pk), "estado": documento.estado_validacion},
            status=status.HTTP_201_CREATED,
        )
