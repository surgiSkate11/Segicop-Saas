from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.core.exceptions import DomainError
from apps.cumplimiento import services
from apps.cumplimiento.forms import RevisionForm, SubirDocumentoForm
from apps.cumplimiento.models import DocumentoVersion, EstadoRequisito, RequisitoAplicado


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class BandejaRevisionView(LoginRequiredMixin, ListView):
    model = RequisitoAplicado
    template_name = "cumplimiento/bandeja.html"
    context_object_name = "requisitos"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return (
            RequisitoAplicado.objects.filter(organizacion_id__in=organizaciones_ids, estado=EstadoRequisito.EN_REVISION)
            .select_related("catalogo", "empresa", "persona").order_by("created_at")
        )


class RequisitoDetalleView(LoginRequiredMixin, View):
    template_name = "cumplimiento/detalle_requisito.html"

    def _get_requisito(self, pk):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return get_object_or_404(
            RequisitoAplicado.objects.select_related("catalogo", "empresa", "persona"),
            pk=pk, organizacion_id__in=organizaciones_ids,
        )

    def get(self, request, pk):
        requisito = self._get_requisito(pk)
        contexto = {
            "requisito": requisito,
            "documentos": requisito.documentos.select_related("subido_por").prefetch_related("revisiones"),
            "documento_vigente": requisito.documentos.filter(vigente=True).first(),
            "form_subida": SubirDocumentoForm(),
            "form_revision": RevisionForm(),
        }
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        requisito = self._get_requisito(pk)
        accion = request.POST.get("accion")
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
            requisito_aplicado=requisito, archivo=form.cleaned_data["archivo"], actor=request.user,
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
            documento_version=documento, revisor=request.user,
            decision=form.cleaned_data["decision"], comentario=form.cleaned_data["comentario"],
        )
        messages.success(request, "Decisión registrada.")