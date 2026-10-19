from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.antecedentes import permisos
from apps.antecedentes.models import ConsultaProveedor, FinalidadConsulta
from apps.antecedentes.pdf import DocumentoInvalido
from apps.antecedentes.serializers import serializador_verificacion
from apps.antecedentes.services import cargar_certificado, iniciar_verificacion, registrar_lectura
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


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
        try:
            documento, creado = cargar_certificado(
                verificacion=consulta.verificacion, archivo=archivo, actor=request.user
            )
        except DocumentoInvalido as error:
            # El PDF queda en cuarentena (si llegó a guardarse); el caso sigue como estaba.
            return Response({"code": error.codigo, "detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except DomainError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"documento_id": str(documento.pk), "estado": documento.estado_validacion},
            status=status.HTTP_201_CREATED if creado else status.HTTP_200_OK,
        )
