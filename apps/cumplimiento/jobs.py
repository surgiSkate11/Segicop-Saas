from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accesos import services as accesos_services
from apps.accesos.models import EstadoPermiso, PermisoIngreso
from apps.antecedentes import services as antecedentes_services
from apps.antecedentes.models import EstadoVerificacion, VerificacionAntecedentes
from apps.core.services import crear_alerta
from apps.cumplimiento import services as cumplimiento_services
from apps.cumplimiento.models import DocumentoVersion, EstadoRequisito, RequisitoAplicado
from apps.identidad.notificaciones import enviar_resumenes_alertas


def procesar_vencimientos(*, actor=None):
    hoy = timezone.localdate()
    documentos = list(
        RequisitoAplicado.objects.filter(
            estado=EstadoRequisito.APROBADO,
            documentos__vigente=True,
            documentos__fecha_vencimiento__lt=hoy,
        ).distinct()
    )
    for requisito in documentos:
        if requisito.empresa_id:
            cumplimiento_services.recalcular_empresa(requisito.empresa, actor=actor)
        else:
            cumplimiento_services.recalcular_persona(requisito.persona, actor=actor)

    antecedentes_services.marcar_vencidas(actor=actor)
    renovaciones_antecedentes = antecedentes_services.programar_renovaciones(actor=actor)
    accesos_services.marcar_vencidos(actor=actor)

    alertas_documentos = _alertar_documentos_vencidos(hoy=hoy)
    alertas_antecedentes = _alertar_antecedentes_vencidos()
    alertas_permisos = _alertar_permisos_vencidos()
    avisos_previos = _avisar_documentos_por_vencer(hoy=hoy)
    resumenes = enviar_resumenes_alertas(hoy=hoy)
    return {
        "documentos": alertas_documentos,
        "antecedentes": alertas_antecedentes,
        "permisos_ingreso": alertas_permisos,
        "avisos_previos": avisos_previos,
        "renovaciones_antecedentes": renovaciones_antecedentes,
        "total_alertas": alertas_documentos + alertas_antecedentes + alertas_permisos + avisos_previos,
        "resumenes_enviados": resumenes,
    }


def _avisar_documentos_por_vencer(hoy):
    """
    RF-033: un aviso por umbral cruzado (el menor que cubre los días restantes). La clave usa el
    documento y el umbral, así que repetir el job no duplica y una renovación vuelve a avisar.
    """
    umbrales = sorted(settings.SIGECOP_DIAS_AVISO_VENCIMIENTO)
    if not umbrales:
        return 0
    total = 0
    documentos = DocumentoVersion.objects.filter(
        vigente=True,
        requisito_aplicado__estado=EstadoRequisito.APROBADO,
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=hoy + timedelta(days=umbrales[-1]),
    ).select_related(
        "organizacion", "requisito_aplicado__catalogo", "requisito_aplicado__empresa", "requisito_aplicado__persona"
    )
    for documento in documentos:
        requisito = documento.requisito_aplicado
        dias = (documento.fecha_vencimiento - hoy).days
        umbral = next(u for u in umbrales if dias <= u)
        sujeto = requisito.empresa or requisito.persona
        _, creada = crear_alerta(
            organizacion=documento.organizacion,
            clave_idempotencia=f"DOCUMENTO:{documento.id}:POR_VENCER:{umbral}",
            tipo="DOCUMENTO_POR_VENCER",
            agrupador="DOCUMENTOS_POR_VENCER",
            titulo=f"Documento por vencer: {requisito.catalogo.nombre}",
            mensaje=f"El documento de {sujeto} vence el {documento.fecha_vencimiento:%d/%m/%Y} (en {dias} días).",
            severidad="MEDIA",
            fecha_referencia=documento.fecha_vencimiento,
        )
        total += int(creada)
    return total


def _alertar_documentos_vencidos(hoy):
    total = 0
    documentos = (
        RequisitoAplicado.objects.filter(
            estado=EstadoRequisito.VENCIDO,
            documentos__vigente=True,
            documentos__fecha_vencimiento__lt=hoy,
        )
        .select_related("organizacion", "empresa", "persona", "catalogo")
        .distinct()
    )
    for requisito in documentos:
        sujeto = requisito.empresa or requisito.persona
        _, creada = crear_alerta(
            organizacion=requisito.organizacion,
            clave_idempotencia=f"DOCUMENTO:{requisito.id}:VENCIDO",
            tipo="VENCIMIENTO_DOCUMENTO",
            agrupador="DOCUMENTOS_VENCIDOS",
            titulo=f"Documento vencido: {requisito.catalogo.nombre}",
            mensaje=f"Revisar documentación de {sujeto}.",
            severidad="ALTA",
            fecha_referencia=hoy,
        )
        total += int(creada)
    return total


def _alertar_antecedentes_vencidos():
    total = 0
    verificaciones = VerificacionAntecedentes.objects.filter(
        estado=EstadoVerificacion.VENCIDA,
    ).select_related("organizacion", "persona")
    for verificacion in verificaciones:
        _, creada = crear_alerta(
            organizacion=verificacion.organizacion,
            clave_idempotencia=f"ANTECEDENTE:{verificacion.id}:VENCIDA",
            tipo="VENCIMIENTO_ANTECEDENTES",
            agrupador="ANTECEDENTES_VENCIDOS",
            titulo="Verificación de antecedentes vencida",
            mensaje=f"Solicitar una nueva verificación para {verificacion.persona.nombre_completo}.",
            severidad="ALTA",
            fecha_referencia=verificacion.vigente_hasta,
        )
        total += int(creada)
    return total


def _alertar_permisos_vencidos():
    total = 0
    permisos = PermisoIngreso.objects.filter(estado=EstadoPermiso.VENCIDO).select_related(
        "organizacion", "persona", "instalacion"
    )
    for permiso in permisos:
        _, creada = crear_alerta(
            organizacion=permiso.organizacion,
            clave_idempotencia=f"PERMISO_INGRESO:{permiso.id}:VENCIDO",
            tipo="VENCIMIENTO_PERMISO_INGRESO",
            agrupador="PERMISOS_INGRESO_VENCIDOS",
            titulo="Permiso de ingreso vencido",
            mensaje=f"El permiso de {permiso.persona.nombre_completo} para {permiso.instalacion.nombre} venció.",
            severidad="MEDIA",
            fecha_referencia=permiso.fecha_fin_vigencia,
        )
        total += int(creada)
    return total
