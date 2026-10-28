from datetime import date

from django.contrib import messages
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.cumplimiento import services
from apps.cumplimiento.forms import AsignarRequisitoForm, CatalogoRequisitoForm, RevisionForm, SubirDocumentoForm
from apps.cumplimiento.models import CatalogoRequisito, DocumentoVersion, EstadoRequisito, RequisitoAplicado, Revision
from apps.core.modals import exito_htmx, render_modal
from apps.identidad.mixins import RequierePermisoMixin
from apps.identidad.permisos import exigir, organizacion_con_permiso, organizaciones_con_permiso, puede


class BandejaRevisionView(RequierePermisoMixin, ListView):
    """
    S9: como la bandeja del sistema anterior. Cada fila es la versión vigente de un requisito en revisión,
    con Gestión (versión 1 = Nuevo, siguientes = Actualización). La selección masiva solo aparece para
    quien tiene `revisar_documentos`; el POST la vuelve a exigir (RevisarLoteView).
    """

    accion_requerida = "ver_registros"
    model = RequisitoAplicado
    template_name = "cumplimiento/bandeja.html"
    context_object_name = "requisitos"
    paginate_by = 25

    def get_queryset(self):
        return (
            RequisitoAplicado.objects.filter(
                organizacion_id__in=self.organizaciones_permitidas(), estado=EstadoRequisito.EN_REVISION
            )
            .select_related("catalogo", "empresa", "persona")
            .prefetch_related(
                Prefetch("documentos", queryset=DocumentoVersion.objects.filter(vigente=True), to_attr="vigentes")
            )
            .order_by("created_at")
        )

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["puede_revisar"] = puede(self.request.user, "revisar_documentos")
        contexto["decisiones"] = Revision.Decision.choices
        return contexto


class RevisarLoteView(RequierePermisoMixin, View):
    """S9: aprobar, observar o rechazar varios documentos a la vez (todo o nada)."""

    accion_requerida = "revisar_documentos"
    http_method_names = ["post"]

    def post(self, request):
        ids = request.POST.getlist("documentos")
        documentos = list(
            DocumentoVersion.objects.select_related(
                "requisito_aplicado__catalogo", "requisito_aplicado__empresa", "requisito_aplicado__persona"
            ).filter(pk__in=ids, organizacion_id__in=self.organizaciones_permitidas())
        )
        try:
            if len(documentos) != len(set(ids)):
                raise DomainError("Algún documento seleccionado no existe o no puedes revisarlo.")
            fechas = {}
            for documento in documentos:
                valor = request.POST.get(f"fecha_emision_{documento.pk}", "").strip()
                if valor:
                    try:
                        fechas[documento.pk] = date.fromisoformat(valor)
                    except ValueError:
                        raise DomainError(f"Fecha de emisión inválida: {valor}.") from None
            revisiones = services.revisar_documentos_en_lote(
                documentos=documentos,
                revisor=request.user,
                decision=request.POST.get("decision", ""),
                comentario=request.POST.get("comentario", "").strip(),
                fechas_emision=fechas,
            )
        except DomainError as error:
            messages.error(request, f"No se aplicó ninguna decisión. {error}")
        else:
            messages.success(request, f"Decisión registrada en {len(revisiones)} documentos.")
        return redirect("cumplimiento:bandeja")


class RequisitoDetalleView(RequierePermisoMixin, View):
    """
    Ver exige `ver_registros`. El POST exige, además, la acción que pide el formulario:
    subir → `subir_documentos`, revisar → `revisar_documentos`.
    """

    accion_requerida = {"GET": "ver_registros", "POST": "ver_registros"}
    acciones_post = {"subir": "subir_documentos", "revisar": "revisar_documentos"}
    template_name = "cumplimiento/detalle_requisito.html"

    def _get_requisito(self, pk, accion):
        return get_object_or_404(
            RequisitoAplicado.objects.select_related("catalogo", "empresa", "persona"),
            pk=pk,
            organizacion_id__in=organizaciones_con_permiso(self.request.user, accion),
        )

    def get(self, request, pk):
        requisito = self._get_requisito(pk, "ver_registros")
        contexto = {
            "requisito": requisito,
            "documentos": requisito.documentos.select_related("subido_por").prefetch_related("revisiones"),
            "documento_vigente": requisito.documentos.filter(vigente=True).first(),
            "form_subida": SubirDocumentoForm(),
            "form_revision": RevisionForm(),
        }
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        accion = request.POST.get("accion")
        permiso = self.acciones_post.get(accion)
        if permiso is not None:
            exigir(request.user, permiso)
        requisito = self._get_requisito(pk, permiso or "ver_registros")
        try:
            if accion == "subir":
                self._subir(request, requisito)
            elif accion == "revisar":
                self._revisar(request, requisito)
            else:
                messages.error(request, "Acción no reconocida.")
        except DomainError as error:
            messages.error(request, str(error))
        return redirect(reverse("cumplimiento:detalle", args=[requisito.pk]))

    def _subir(self, request, requisito):
        form = SubirDocumentoForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Revisa el archivo: " + form.errors.as_text())
            return
        services.subir_documento(
            requisito_aplicado=requisito,
            archivo=form.cleaned_data["archivo"],
            actor=request.user,
            fecha_vencimiento=form.cleaned_data["fecha_vencimiento"],
        )
        messages.success(request, "Documento subido. Queda pendiente de revisión.")

    def _revisar(self, request, requisito):
        documento = requisito.documentos.filter(vigente=True).first()
        if not documento:
            messages.error(request, "No hay una versión vigente para revisar.")
            return
        form = RevisionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revisa la decisión: " + form.errors.as_text())
            return
        services.revisar_documento(
            documento_version=documento,
            revisor=request.user,
            decision=form.cleaned_data["decision"],
            comentario=form.cleaned_data["comentario"],
            fecha_emision=form.cleaned_data["fecha_emision"],
        )
        messages.success(request, "Decisión registrada.")


class CatalogoListView(RequierePermisoMixin, ListView):
    """Catálogo de requisitos configurables — antes solo existía en el admin."""

    accion_requerida = "ver_registros"
    template_name = "cumplimiento/catalogo.html"
    context_object_name = "catalogo"
    paginate_by = 25

    def get_queryset(self):
        return CatalogoRequisito.objects.filter(organizacion_id__in=self.organizaciones_permitidas()).order_by("nombre")


class _ModalCumplimientoView(RequierePermisoMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect("cumplimiento:catalogo")
        return super().dispatch(request, *args, **kwargs)


class CatalogoRequisitoModalView(_ModalCumplimientoView):
    accion_requerida = "configurar_catalogo"

    def get(self, request):
        return render_modal(
            request,
            form=CatalogoRequisitoForm(),
            titulo="Nuevo requisito del catálogo",
            accion_url=reverse("cumplimiento:catalogo_nuevo"),
            boton="Crear requisito",
        )

    def post(self, request):
        form = CatalogoRequisitoForm(request.POST)
        if form.is_valid():
            organizacion = organizacion_con_permiso(request.user, self.accion_actual)
            if organizacion is None:
                form.add_error(None, "Tu usuario no tiene una organización asignada.")
            else:
                datos = form.cleaned_data
                try:
                    services.crear_catalogo_requisito(organizacion=organizacion, actor=request.user, **datos)
                except DomainError as error:
                    form.add_error("codigo", str(error))
                else:
                    return exito_htmx(
                        request, mensaje="Requisito creado en el catálogo.", url=reverse("cumplimiento:catalogo")
                    )
        return render_modal(
            request,
            form=form,
            titulo="Nuevo requisito del catálogo",
            accion_url=reverse("cumplimiento:catalogo_nuevo"),
            boton="Crear requisito",
        )


class AsignarRequisitoModalView(_ModalCumplimientoView):
    accion_requerida = "asignar_requisitos"

    def _catalogo(self, request, pk):
        return get_object_or_404(CatalogoRequisito, pk=pk, organizacion_id__in=self.organizaciones_permitidas())

    def _modal(self, request, form, catalogo):
        return render_modal(
            request,
            form=form,
            titulo=f"Asignar · {catalogo.nombre}",
            accion_url=reverse("cumplimiento:catalogo_asignar", args=[catalogo.pk]),
            boton="Asignar",
        )

    def get(self, request, pk):
        catalogo = self._catalogo(request, pk)
        form = AsignarRequisitoForm(organizacion=catalogo.organizacion, catalogo=catalogo)
        return self._modal(request, form, catalogo)

    def post(self, request, pk):
        catalogo = self._catalogo(request, pk)
        form = AsignarRequisitoForm(request.POST, organizacion=catalogo.organizacion, catalogo=catalogo)
        if form.is_valid():
            try:
                services.asignar_requisito(
                    catalogo=catalogo,
                    actor=request.user,
                    empresa=form.cleaned_data.get("empresa"),
                    persona=form.cleaned_data.get("persona"),
                    fecha_limite=form.cleaned_data["fecha_limite"],
                )
            except DomainError as error:
                form.add_error(None, str(error))
            else:
                return exito_htmx(request, mensaje="Requisito asignado.", url=reverse("cumplimiento:catalogo"))
        return self._modal(request, form, catalogo)
