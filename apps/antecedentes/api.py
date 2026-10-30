from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.antecedentes import permisos
from apps.antecedentes import services
from apps.antecedentes.models import ConsultaProveedor, EstadoVerificacion, FinalidadConsulta, ProveedorAntecedentes
from apps.antecedentes.pdf import DocumentoInvalido
from apps.antecedentes.serializers import CrearVerificacionEntrada, RevisionEntrada, serializador_verificacion
from apps.antecedentes.services import (
    cargar_certificado,
    continuar_consulta,
    iniciar_verificacion,
    registrar_lectura,
    validar_verificacion,
)
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


class IniciarVerificacionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, persona_pk):
        persona = get_object_or_404(
            Persona, pk=persona_pk, organizacion_id__in=permisos.organizaciones_operables(request.user)
        )
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
        consulta = get_object_or_404(
            ConsultaProveedor.objects.select_related("verificacion__persona"),
            pk=consulta_pk,
            verificacion__organizacion_id__in=permisos.organizaciones_operables(request.user),
        )
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


# --- /api/v1/verificaciones-antecedentes (S10) ------------------------------------------------------------

CODIGO_SOLICITUD_INVALIDA = "ANT_REQUEST_INVALID"
CODIGO_PROHIBIDO = "ANT_FORBIDDEN"
INTERVENCION_HUMANA = "INTERVENCION_HUMANA"
MAX_CLAVE_IDEMPOTENCIA = 64  # largo de VerificacionAntecedentes.clave_idempotencia


class SolicitudInvalida(DomainError):
    codigo = CODIGO_SOLICITUD_INVALIDA


class AutorizacionRequerida(DomainError):
    codigo = "ANT_AUTHORIZATION_REQUIRED"


def enmascarar_documento(numero: str) -> str:
    """Solo los últimos cuatro dígitos quedan visibles."""
    return "*" * max(len(numero) - 4, 0) + numero[-4:] if len(numero) > 4 else "*" * len(numero)


def _espera_humana(verificacion):
    """(code, accionRequerida, reintentarDesde) de los casos que esperan a una persona."""
    estado = verificacion.estado
    if estado == EstadoVerificacion.AUTORIZACION_PENDIENTE:
        return "ANT_AUTHORIZATION_REQUIRED", "REGISTRAR_AUTORIZACION", None
    if estado == EstadoVerificacion.CAPTCHA_REQUERIDO:
        return services.ANT_CAPTCHA_REQUIRED, INTERVENCION_HUMANA, None
    if estado == EstadoVerificacion.EN_CONSULTA:
        return None, INTERVENCION_HUMANA, None
    if estado == EstadoVerificacion.FALLIDA:
        # El fallo es siempre el del Ministerio: la evidencia judicial no tiene código de error de la fuente.
        consulta = verificacion.consultas_proveedor.filter(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR).first()
        codigo = (consulta and consulta.codigo_error) or services.ANT_SOURCE_UNAVAILABLE
        desde = services.reintentar_desde(verificacion)
        return codigo, "CARGA_MANUAL", desde.isoformat() if desde else None
    return None, None, None


def cuerpo_verificacion(verificacion, usuario) -> dict:
    """Serializador según el rol (ANT-011) más lo que el caso espera de una persona."""
    es_revisor = permisos.puede_revisar(usuario, verificacion.organizacion_id)
    cuerpo = dict(serializador_verificacion(es_revisor)(verificacion).data)
    codigo, accion, reintentar_desde = _espera_humana(verificacion)
    cuerpo.update(
        code=codigo,
        accionRequerida=accion,
        reintentarDesde=reintentar_desde,
        numeroDocumentoEnmascarado=enmascarar_documento(verificacion.persona.numero_documento),
    )
    return cuerpo


def _primer_error(errores) -> str:
    if isinstance(errores, dict):
        campo, detalle = next(iter(errores.items()))
        return f"{campo}: {_primer_error(detalle)}"
    if isinstance(errores, (list, tuple)):
        return _primer_error(errores[0]) if errores else ""
    return str(errores)


def _validar_entrada(serializador_clase, datos) -> dict:
    serializador = serializador_clase(data=datos)
    if not serializador.is_valid():
        raise SolicitudInvalida(_primer_error(serializador.errors))
    return serializador.validated_data


class VerificacionesAPIView(APIView):
    """Base: DomainError -> 400 {code, detail}; permisos de rol -> 403; sin rol en la organización -> 404."""

    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, DomainError):
            codigo = CODIGO_SOLICITUD_INVALIDA if exc.codigo == DomainError.codigo else exc.codigo
            return Response({"code": codigo, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if isinstance(exc, PermissionDenied):
            return Response({"code": CODIGO_PROHIBIDO, "detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return super().handle_exception(exc)

    def verificacion(self, request, pk):
        """Sin rol de antecedentes en la organización de la verificación, esta no existe (404)."""
        return get_object_or_404(
            permisos.verificaciones_visibles(request.user).select_related("persona", "autorizacion"), pk=pk
        )


class CrearVerificacionAPIView(VerificacionesAPIView):
    def post(self, request):
        datos = _validar_entrada(CrearVerificacionEntrada, request.data)
        clave = request.headers.get("Idempotency-Key", "").strip()
        if len(clave) > MAX_CLAVE_IDEMPOTENCIA:
            raise SolicitudInvalida(f"Idempotency-Key no puede superar {MAX_CLAVE_IDEMPOTENCIA} caracteres.")
        persona = get_object_or_404(
            Persona, pk=datos["personaId"], organizacion_id__in=permisos.organizaciones_operables(request.user)
        )
        autorizacion_id = datos.get("autorizacionId")
        if autorizacion_id is not None:
            vigente = services.autorizacion_vigente(persona, datos["finalidad"])
            if vigente is None or vigente.pk != autorizacion_id:
                raise AutorizacionRequerida(
                    "La autorización indicada no es la vigente de esta persona para esa finalidad."
                )
        verificacion, creada = iniciar_verificacion(
            persona=persona, actor=request.user, finalidad=datos["finalidad"], clave_idempotencia=clave
        )
        verificacion.refresh_from_db()
        return Response(
            cuerpo_verificacion(verificacion, request.user),
            status=status.HTTP_202_ACCEPTED if creada else status.HTTP_200_OK,
        )


class DetalleVerificacionAPIView(VerificacionesAPIView):
    def get(self, request, pk):
        verificacion = self.verificacion(request, pk)
        es_revisor = permisos.puede_revisar(request.user, verificacion.organizacion_id)
        registrar_lectura(usuario=request.user, verificacion=verificacion, resultado_visible=es_revisor)
        return Response(cuerpo_verificacion(verificacion, request.user))


class ContinuarVerificacionAPIView(VerificacionesAPIView):
    def post(self, request, pk):
        verificacion = self.verificacion(request, pk)
        url = continuar_consulta(verificacion=verificacion, actor=request.user)
        return Response({**cuerpo_verificacion(verificacion, request.user), "urlPortal": url})


class DocumentoVerificacionAPIView(VerificacionesAPIView):
    parser_classes = [MultiPartParser]

    def post(self, request, pk):
        verificacion = self.verificacion(request, pk)
        archivo = request.FILES.get("archivo")
        if not archivo:
            raise SolicitudInvalida("El campo archivo es obligatorio.")
        documento, creado = cargar_certificado(verificacion=verificacion, archivo=archivo, actor=request.user)
        cuerpo = cuerpo_verificacion(verificacion, request.user)
        cuerpo.update(documentoId=str(documento.pk), estadoDocumento=documento.estado_validacion)
        if documento.codigo_error:
            cuerpo["code"] = documento.codigo_error
        return Response(cuerpo, status=status.HTTP_201_CREATED if creado else status.HTTP_200_OK)


class RevisionVerificacionAPIView(VerificacionesAPIView):
    def post(self, request, pk):
        verificacion = self.verificacion(request, pk)
        permisos.exigir_revisar(request.user, verificacion.organizacion_id)
        datos = _validar_entrada(RevisionEntrada, request.data)
        validar_verificacion(
            verificacion=verificacion,
            revisor=request.user,
            decision=datos["decision"],
            comentario=datos["comentario"],
            vigente_hasta=datos["vigenteHasta"],
        )
        return Response(cuerpo_verificacion(verificacion, request.user))
