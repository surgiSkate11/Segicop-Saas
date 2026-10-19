from django.utils import timezone

from apps.accesos import services as accesos_services
from apps.accesos.models import EstadoPermiso, PermisoIngreso
from apps.antecedentes import services as antecedentes_services
from apps.antecedentes.models import EstadoVerificacion, VerificacionAntecedentes
from apps.core.services import crear_alerta
from apps.cumplimiento import services as cumplimiento_services
from apps.cumplimiento.models import EstadoRequisito, RequisitoAplicado


def procesar_vencimientos(*, actor=None):
    hoy = timezone.now().date()
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
    return {
        "documentos": alertas_documentos,
        "antecedentes": alertas_antecedentes,
        "permisos_ingreso": alertas_permisos,
        "renovaciones_antecedentes": renovaciones_antecedentes,
        "total_alertas": alertas_documentos + alertas_antecedentes + alertas_permisos,
    }


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
