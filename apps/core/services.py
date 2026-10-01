import csv
import hashlib
import io

from django.db import transaction
from openpyxl import Workbook

from apps.core.events import emitir_evento
from apps.core.models import AlertaNotificacion, EstadoAlerta, ExportacionAuditoria


def crear_alerta(
    *, organizacion, clave_idempotencia, tipo, agrupador, titulo, mensaje, severidad="INFO", fecha_referencia=None
):
    """Crea una alerta una sola vez aunque el job se reintente."""
    alerta, creada = AlertaNotificacion.objects.get_or_create(
        clave_idempotencia=clave_idempotencia,
        defaults={
            "organizacion": organizacion,
            "tipo": tipo,
            "agrupador": agrupador,
            "titulo": titulo,
            "mensaje": mensaje,
            "severidad": severidad,
            "fecha_referencia": fecha_referencia,
        },
    )
    return alerta, creada


@transaction.atomic
def marcar_alerta_leida(*, alerta, actor):
    if alerta.estado == EstadoAlerta.LEIDA:
        return alerta
    alerta.estado = EstadoAlerta.LEIDA
    alerta.save(update_fields=["estado", "updated_at"])
    emitir_evento(
        actor=actor,
        accion="MARCAR_ALERTA_LEIDA",
        recurso=alerta,
        organizacion=alerta.organizacion,
        motivo=f"Alerta '{alerta.titulo}' marcada como leída.",
    )
    return alerta


def exportar_alertas_csv(*, organizaciones_ids, actor, estado=None):
    alertas = AlertaNotificacion.objects.filter(organizacion_id__in=organizaciones_ids)
    if estado:
        alertas = alertas.filter(estado=estado)
    alertas = alertas.select_related("organizacion").order_by("organizacion_id", "-created_at")

    salida = io.StringIO()
    escritor = csv.writer(salida)
    escritor.writerow(["organizacion", "tipo", "grupo", "titulo", "severidad", "estado", "fecha"])
    filas = list(alertas)
    for alerta in filas:
        escritor.writerow(
            [
                alerta.organizacion.nombre,
                alerta.tipo,
                alerta.agrupador,
                alerta.titulo,
                alerta.severidad,
                alerta.estado,
                alerta.created_at.isoformat(),
            ]
        )

    contenido = salida.getvalue()
    exportacion = ExportacionAuditoria.objects.create(
        actor=actor,
        tipo="ALERTAS",
        formato="CSV",
        filtros={"organizaciones": [str(item) for item in organizaciones_ids], "estado": estado},
        filas=len(filas),
        hash_sha256=hashlib.sha256(contenido.encode("utf-8")).hexdigest(),
    )
    emitir_evento(
        actor=actor,
        accion="EXPORTAR_ALERTAS",
        recurso=exportacion,
        motivo=f"Exportación CSV de {len(filas)} alertas.",
        metadata={"filas": len(filas), "formato": "CSV"},
    )
    return contenido, exportacion


def exportar_alertas_xlsx(*, organizaciones_ids, actor, estado=None):
    alertas = AlertaNotificacion.objects.filter(organizacion_id__in=organizaciones_ids)
    if estado:
        alertas = alertas.filter(estado=estado)
    filas = list(alertas.select_related("organizacion").order_by("organizacion_id", "-created_at"))

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Alertas"
    hoja.append(["Organización", "Tipo", "Grupo", "Título", "Severidad", "Estado", "Fecha"])
    for alerta in filas:
        hoja.append(
            [
                alerta.organizacion.nombre,
                alerta.tipo,
                alerta.agrupador,
                alerta.titulo,
                alerta.severidad,
                alerta.estado,
                alerta.created_at.isoformat(),
            ]
        )
    hoja.freeze_panes = "A2"
    for columna in hoja.columns:
        hoja.column_dimensions[columna[0].column_letter].width = min(
            max(len(str(celda.value or "")) for celda in columna) + 2, 45
        )

    archivo = io.BytesIO()
    libro.save(archivo)
    contenido = archivo.getvalue()
    exportacion = ExportacionAuditoria.objects.create(
        actor=actor,
        tipo="ALERTAS",
        formato="XLSX",
        filtros={"organizaciones": [str(item) for item in organizaciones_ids], "estado": estado},
        filas=len(filas),
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
    )
    emitir_evento(
        actor=actor,
        accion="EXPORTAR_ALERTAS",
        recurso=exportacion,
        motivo=f"Exportación XLSX de {len(filas)} alertas.",
        metadata={"filas": len(filas), "formato": "XLSX"},
    )
    return contenido, exportacion
