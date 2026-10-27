from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.cumplimiento import services
from apps.cumplimiento.forms import AsignarRequisitoForm, CatalogoRequisitoForm, RevisionForm, SubirDocumentoForm
from apps.cumplimiento.models import CatalogoRequisito, EstadoRequisito, RequisitoAplicado
from apps.core.modals import exito_htmx, render_modal
from apps.identidad.mixins import RequierePermisoMixin
from apps.identidad.permisos import exigir, organizacion_con_permiso, organizaciones_con_permiso


class BandejaRevisionView(RequierePermisoMixin, ListView):
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
            .order_by("created_at")
        )


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
