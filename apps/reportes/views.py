"""
T8 / S10: viewing a report needs `ver_registros`; downloading it needs `exportar_reportes`.
Each view filters explicitly by the organizations where the user has that action (constitution §2.6).
"""

import uuid
from datetime import date

from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from apps.accesos.models import EstadoPermiso, TipoEvento
from apps.empresas.models import EmpresaContratista
from apps.identidad.models import Instalacion
from apps.identidad.mixins import RequierePermisoMixin
from apps.personas.models import Persona
from apps.reportes import services

REPORTES = {
    "documentos": {
        "codigo": services.DOCUMENTOS_TRABAJADORES,
        "titulo": "Documentos de trabajadores",
        "descripcion": "Cada documento exigido a los trabajadores, con su estado, emisión y caducidad.",
        "url": "reportes:documentos_trabajadores",
        "url_excel": "reportes:documentos_trabajadores_excel",
        "archivo": "documentos_trabajadores.xlsx",
        "con_fechas": True,
        "etiqueta_fechas": "Caduca",
        "placeholder": "Cédula, nombre o documento",
        "funcion": services.documentos_trabajadores,
    },
    "informacion": {
        "codigo": services.INFORMACION_TRABAJADORES,
        "titulo": "Información de trabajadores",
        "descripcion": "Datos de contacto, empresa, cargo, estado documental y habilitación de cada trabajador.",
        "url": "reportes:informacion_trabajadores",
        "url_excel": "reportes:informacion_trabajadores_excel",
        "archivo": "informacion_trabajadores.xlsx",
        "con_fechas": False,
        "funcion": services.informacion_trabajadores,
    },
    "solicitudes": {
        "codigo": services.SOLICITUDES_INGRESO,
        "titulo": "Solicitudes de ingreso",
        "descripcion": "Cada permiso de ingreso solicitado, con planta, estado, vigencia y aprobador.",
        "url": "reportes:solicitudes_ingreso",
        "url_excel": "reportes:solicitudes_ingreso_excel",
        "archivo": "solicitudes_ingreso.xlsx",
        "con_fechas": True,
        "etiqueta_fechas": "Solicitada",
        "con_planta": True,
        "estados": EstadoPermiso.choices,
        "etiqueta_estado": "Estado",
        "funcion": services.solicitudes_ingreso,
    },
    "accesos": {
        "codigo": services.HISTORICO_ACCESOS,
        "titulo": "Histórico de accesos",
        "descripcion": "Cada entrada, salida o ingreso denegado registrado en garita.",
        "url": "reportes:historico_accesos",
        "url_excel": "reportes:historico_accesos_excel",
        "archivo": "historico_accesos.xlsx",
        "con_fechas": True,
        "etiqueta_fechas": "Registrado",
        "con_planta": True,
        "estados": TipoEvento.choices,
        "etiqueta_estado": "Tipo",
        "funcion": services.historico_accesos,
    },
}


def _uuid(valor):
    try:
        return uuid.UUID(valor) if valor else None
    except ValueError:
        return None


def _fecha(valor):
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


class _ReporteMixin(RequierePermisoMixin):
    reporte = None

    def definicion(self):
        return REPORTES[self.reporte]

    def filtros(self, organizaciones_ids):
        """Validated filters; invalid values are ignored (they never widen what the user can see)."""
        definicion = self.definicion()
        empresa = _uuid(self.request.GET.get("empresa"))
        filtros = {"empresa": str(empresa) if empresa else None, "q": self.request.GET.get("q", "").strip()}
        if definicion["con_fechas"]:
            desde, hasta = _fecha(self.request.GET.get("desde")), _fecha(self.request.GET.get("hasta"))
            filtros.update(
                {"desde": desde.isoformat() if desde else None, "hasta": hasta.isoformat() if hasta else None}
            )
        if definicion.get("con_planta"):
            planta = _uuid(self.request.GET.get("planta"))
            valida = planta and Instalacion.objects.filter(pk=planta, organizacion_id__in=organizaciones_ids).exists()
            filtros["planta"] = str(planta) if valida else None
        if definicion.get("estados"):
            estado = self.request.GET.get("estado")
            filtros["estado"] = estado if estado in dict(definicion["estados"]) else None
        return filtros

    def filas(self, organizaciones_ids, filtros):
        argumentos = {"organizaciones_ids": organizaciones_ids, "empresa_id": filtros["empresa"], "q": filtros["q"]}
        if "desde" in filtros:
            argumentos.update(desde=_fecha(filtros["desde"]), hasta=_fecha(filtros["hasta"]))
        if "planta" in filtros:
            argumentos["planta_id"] = filtros["planta"]
        if "estado" in filtros:
            argumentos["estado"] = filtros["estado"]
        return self.definicion()["funcion"](**argumentos)


class IndiceReportesView(RequierePermisoMixin, View):
    accion_requerida = "ver_registros"

    def get(self, request):
        return render(request, "reportes/indice.html", {"reportes": REPORTES.values()})


class ReporteView(_ReporteMixin, View):
    accion_requerida = "ver_registros"

    def get(self, request):
        organizaciones_ids = list(self.organizaciones_permitidas())
        filtros = self.filtros(organizaciones_ids)
        filas = self.filas(organizaciones_ids, filtros)
        pagina = Paginator(filas, 50).get_page(request.GET.get("page"))
        contexto = {
            "reporte": self.definicion(),
            "columnas": services.COLUMNAS[self.definicion()["codigo"]],
            "filas": list(pagina.object_list),
            "pagina": pagina,
            "total": len(filas),
            "filtros": filtros,
            "empresas": EmpresaContratista.objects.filter(organizacion_id__in=organizaciones_ids).order_by(
                "razon_social"
            ),
            "plantas": Instalacion.objects.filter(organizacion_id__in=organizaciones_ids).order_by("nombre"),
            "consulta": request.GET.urlencode(),
        }
        return render(request, "reportes/reporte.html", contexto)


class ExpedientePersonaView(RequierePermisoMixin, View):
    """T13 / S11: chronological person file. Same audience as the person list; garita gets 403."""

    accion_requerida = "ver_registros"

    def get(self, request, pk):
        persona = get_object_or_404(Persona, pk=pk, organizacion_id__in=self.organizaciones_permitidas())
        return render(
            request,
            "reportes/expediente.html",
            {"persona": persona, "eventos": services.expediente_persona(persona)},
        )


class ExportarReporteView(_ReporteMixin, View):
    accion_requerida = "exportar_reportes"

    def get(self, request):
        organizaciones_ids = list(self.organizaciones_permitidas())
        filtros = self.filtros(organizaciones_ids)
        contenido, _ = services.exportar_xlsx(
            reporte=self.definicion()["codigo"],
            filas=self.filas(organizaciones_ids, filtros),
            actor=request.user,
            organizaciones_ids=organizaciones_ids,
            filtros=filtros,
        )
        respuesta = HttpResponse(
            contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        respuesta["Content-Disposition"] = f'attachment; filename="{self.definicion()["archivo"]}"'
        return respuesta
