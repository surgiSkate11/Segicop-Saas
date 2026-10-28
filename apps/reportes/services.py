"""
T8 / S10: worker reports of the previous system ("Documentos Trabajadores", "Información Trabajadores").

Read-only queries over the domain apps plus one audited mutation: the Excel export (RF-063, RN-009),
which records an ExportacionAuditoria (filters, rows, SHA-256) and an EXPORTAR_<REPORTE> event.
The worker information report shows enablement (Sí/No) and NEVER the antecedentes result
(constitution §2.7): `calcular_habilitacion` is the only place that combines the three decisions.
"""

import hashlib
import io

from django.db.models import Prefetch, Q
from openpyxl import Workbook

from apps.accesos.services import calcular_habilitacion
from apps.core.events import emitir_evento
from apps.core.models import ExportacionAuditoria
from apps.cumplimiento.models import DocumentoVersion, RequisitoAplicado
from apps.identidad.models import Organizacion
from apps.personas.models import Asignacion, Persona

DOCUMENTOS_TRABAJADORES = "DOCUMENTOS_TRABAJADORES"
INFORMACION_TRABAJADORES = "INFORMACION_TRABAJADORES"

COLUMNAS = {
    DOCUMENTOS_TRABAJADORES: [
        ("cedula", "Cédula"),
        ("trabajador", "Trabajador"),
        ("empresa", "Empresa"),
        ("documento", "Documento"),
        ("estado", "Estado"),
        ("fecha_emision", "Fecha de emisión"),
        ("fecha_caducidad", "Fecha de caducidad"),
    ],
    INFORMACION_TRABAJADORES: [
        ("cedula", "Cédula"),
        ("trabajador", "Trabajador"),
        ("empresa", "Empresa"),
        ("ruc", "RUC"),
        ("cargo", "Cargo"),
        ("estado_documental", "Estado documental"),
        ("habilitado", "Habilitado"),
        ("fecha_nacimiento", "Fecha de nacimiento"),
        ("nacionalidad", "Nacionalidad"),
        ("domicilio", "Domicilio"),
        ("correo", "Correo"),
        ("telefono", "Teléfono"),
        ("telefono_emergencia", "Teléfono de emergencia"),
    ],
}


def _asignaciones_activas():
    return Prefetch(
        "asignaciones",
        queryset=Asignacion.objects.filter(activa=True).select_related("empresa").order_by("fecha_inicio", "created_at"),
        to_attr="activas",
    )


def _buscar_persona(q, prefijo=""):
    q = (q or "").strip()
    if not q:
        return Q()
    return (
        Q(**{f"{prefijo}numero_documento__icontains": q})
        | Q(**{f"{prefijo}nombres__icontains": q})
        | Q(**{f"{prefijo}apellidos__icontains": q})
    )


def documentos_trabajadores(*, organizaciones_ids, empresa_id=None, desde=None, hasta=None, q=""):
    """One row per requirement of a worker, with its current document (emission and expiry dates)."""
    requisitos = RequisitoAplicado.objects.filter(organizacion_id__in=organizaciones_ids, persona__isnull=False)
    if empresa_id:
        requisitos = requisitos.filter(
            persona__asignaciones__empresa_id=empresa_id, persona__asignaciones__activa=True
        )
    if desde:
        requisitos = requisitos.filter(documentos__vigente=True, documentos__fecha_vencimiento__gte=desde)
    if hasta:
        requisitos = requisitos.filter(documentos__vigente=True, documentos__fecha_vencimiento__lte=hasta)
    if (q or "").strip():
        requisitos = requisitos.filter(_buscar_persona(q, "persona__") | Q(catalogo__nombre__icontains=q.strip()))
    requisitos = (
        requisitos.distinct()
        .select_related("catalogo", "persona")
        .prefetch_related(
            Prefetch(
                "documentos",
                queryset=DocumentoVersion.objects.filter(vigente=True).prefetch_related("revisiones"),
                to_attr="vigentes",
            ),
            Prefetch("persona__asignaciones", queryset=Asignacion.objects.filter(activa=True).select_related("empresa")),
        )
        .order_by("persona__apellidos", "persona__nombres", "catalogo__nombre")
    )
    filas = []
    for requisito in requisitos:
        persona = requisito.persona
        asignacion = next(iter(persona.asignaciones.all()), None)
        documento = requisito.vigentes[0] if requisito.vigentes else None
        emisiones = [r for r in documento.revisiones.all() if r.fecha_emision] if documento else []
        filas.append(
            {
                "cedula": persona.numero_documento,
                "trabajador": persona.nombre_completo,
                "empresa": asignacion.empresa.razon_social if asignacion else "",
                "documento": requisito.catalogo.nombre,
                "estado": requisito.get_estado_display(),
                "fecha_emision": max(emisiones, key=lambda r: r.created_at).fecha_emision if emisiones else None,
                "fecha_caducidad": documento.fecha_vencimiento if documento else None,
            }
        )
    return filas


def informacion_trabajadores(*, organizaciones_ids, empresa_id=None, q=""):
    """One row per worker with an active assignment. Enablement only as Sí/No, never the reason."""
    personas = Persona.objects.filter(organizacion_id__in=organizaciones_ids, asignaciones__activa=True)
    if empresa_id:
        personas = personas.filter(asignaciones__empresa_id=empresa_id, asignaciones__activa=True)
    personas = (
        personas.filter(_buscar_persona(q))
        .distinct()
        .prefetch_related(_asignaciones_activas())
        .order_by("apellidos", "nombres")
    )
    filas = []
    for persona in personas:
        asignacion = persona.activas[0] if persona.activas else None
        filas.append(
            {
                "cedula": persona.numero_documento,
                "trabajador": persona.nombre_completo,
                "empresa": asignacion.empresa.razon_social if asignacion else "",
                "ruc": asignacion.empresa.ruc if asignacion else "",
                "cargo": asignacion.cargo_o_actividad if asignacion else "",
                "estado_documental": persona.get_estado_display(),
                "habilitado": "Sí" if calcular_habilitacion(persona).habilitado else "No",
                "fecha_nacimiento": persona.fecha_nacimiento,
                "nacionalidad": persona.nacionalidad,
                "domicilio": persona.domicilio,
                "correo": persona.contacto_email,
                "telefono": persona.contacto_telefono,
                "telefono_emergencia": persona.telefono_emergencia,
            }
        )
    return filas


def exportar_xlsx(*, reporte, filas, actor, organizaciones_ids, filtros):
    """Builds the workbook and audits the export (RN-009). Returns (bytes, ExportacionAuditoria)."""
    columnas = COLUMNAS[reporte]
    libro = Workbook()
    hoja = libro.active
    hoja.title = reporte.replace("_", " ").title()[:31]
    hoja.append([etiqueta for _, etiqueta in columnas])
    for fila in filas:
        hoja.append([fila[clave] for clave, _ in columnas])
    hoja.freeze_panes = "A2"
    for columna in hoja.columns:
        hoja.column_dimensions[columna[0].column_letter].width = min(
            max(len(str(celda.value or "")) for celda in columna) + 2, 45
        )
    archivo = io.BytesIO()
    libro.save(archivo)
    contenido = archivo.getvalue()

    organizaciones_ids = [str(item) for item in organizaciones_ids]
    organizacion = Organizacion.objects.get(pk=organizaciones_ids[0]) if len(organizaciones_ids) == 1 else None
    exportacion = ExportacionAuditoria.objects.create(
        actor=actor,
        organizacion=organizacion,
        tipo=reporte,
        formato="XLSX",
        filtros={"organizaciones": organizaciones_ids, **filtros},
        filas=len(filas),
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
    )
    emitir_evento(
        actor=actor,
        accion=f"EXPORTAR_{reporte}",
        recurso=exportacion,
        organizacion=organizacion,
        motivo=f"Exportación XLSX de {len(filas)} filas.",
        metadata={"filas": len(filas), "formato": "XLSX", "filtros": filtros},
    )
    return contenido, exportacion


_VERIFICACION_CERRADA = {"VALIDADA", "VENCIDA", "CANCELADA", "FALLIDA"}


def expediente_persona(persona):
    """
    T13 / S11 (RF-023): chronological file of one person, newest first. Read-only.

    Antecedentes verifications appear only as "En curso"/"Cerrada" with their date: never the result,
    the detailed state, the certificate or the validity date (constitution §2.7).
    """
    eventos = []

    def agregar(fecha, tipo, descripcion, detalle=""):
        eventos.append({"fecha": fecha, "tipo": tipo, "descripcion": descripcion, "detalle": detalle})

    for asignacion in persona.asignaciones.select_related("empresa"):
        agregar(
            asignacion.created_at,
            "Asignación",
            f"{asignacion.empresa.razon_social} · {asignacion.cargo_o_actividad}",
            f"Desde {asignacion.fecha_inicio:%d/%m/%Y}" + ("" if asignacion.activa else " (inactiva)"),
        )
    documentos = DocumentoVersion.objects.filter(requisito_aplicado__persona=persona).select_related(
        "requisito_aplicado__catalogo"
    )
    for documento in documentos:
        nombre = documento.requisito_aplicado.catalogo.nombre
        agregar(documento.created_at, "Documento cargado", f"{nombre} (versión {documento.version})")
        for revision in documento.revisiones.all():
            agregar(revision.created_at, "Revisión de documento", nombre, revision.get_decision_display())
    for permiso in persona.permisos_ingreso.select_related("instalacion"):
        agregar(
            permiso.created_at,
            "Permiso de ingreso",
            permiso.instalacion.nombre,
            f"{permiso.get_estado_display()} · {permiso.fecha_inicio_vigencia:%d/%m/%Y}"
            f" al {permiso.fecha_fin_vigencia:%d/%m/%Y}",
        )
    for verificacion in persona.verificaciones_antecedentes.only("created_at", "estado"):
        agregar(
            verificacion.created_at,
            "Verificación de antecedentes",
            "Solicitud de verificación",
            "Cerrada" if verificacion.estado in _VERIFICACION_CERRADA else "En curso",
        )
    for evento in persona.eventos_acceso.select_related("instalacion"):
        agregar(evento.created_at, "Acceso", evento.instalacion.nombre, evento.get_tipo_display())
    # Ties (same timestamp) keep the domain order reversed: an access never appears before its permit.
    orden = sorted(enumerate(eventos), key=lambda par: (par[1]["fecha"], par[0]), reverse=True)
    return [evento for _, evento in orden]
