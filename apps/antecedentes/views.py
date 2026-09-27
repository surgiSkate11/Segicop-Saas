from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.antecedentes import services
from apps.antecedentes.forms import RegistrarResultadoManualForm, ValidarVerificacionForm
from apps.antecedentes.models import EstadoVerificacion, VerificacionAntecedentes
from apps.core.exceptions import DomainError
from apps.personas.models import Persona


def _organizaciones_del_usuario(usuario):
    return usuario.roles_asignados.filter(activo=True).values_list("organizacion_id", flat=True)


class BandejaAntecedentesView(LoginRequiredMixin, ListView):
    model = VerificacionAntecedentes
    template_name = "antecedentes/bandeja.html"
    context_object_name = "verificaciones"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return (
            VerificacionAntecedentes.objects.filter(
                organizacion_id__in=organizaciones_ids,
                estado__in=[EstadoVerificacion.CONTINGENCIA_MANUAL, EstadoVerificacion.EN_REVISION_MANUAL],
            ).select_related("persona").order_by("created_at")
        )


class IniciarVerificacionView(LoginRequiredMixin, View):
    def post(self, request, persona_pk):
        organizaciones_ids = _organizaciones_del_usuario(request.user)
        persona = get_object_or_404(Persona, pk=persona_pk, organizacion_id__in=organizaciones_ids)
        try:
            verificacion = services.iniciar_verificacion(persona=persona, actor=request.user)
        except DomainError as error:
            messages.error(request, str(error))
            return redirect(reverse("personas:lista"))
        messages.success(request, "Verificación de antecedentes iniciada.")
        return redirect(reverse("antecedentes:detalle", args=[verificacion.pk]))


class VerificacionDetalleView(LoginRequiredMixin, View):
    template_name = "antecedentes/detalle.html"

    def _get_verificacion(self, pk):
        organizaciones_ids = _organizaciones_del_usuario(self.request.user)
        return get_object_or_404(VerificacionAntecedentes.objects.select_related("persona"),
                                  pk=pk, organizacion_id__in=organizaciones_ids)

    def get(self, request, pk):
        verificacion = self._get_verificacion(pk)
        contexto = {
            "verificacion": verificacion,
            "ejecuciones": verificacion.ejecuciones.all(),
            "form_resultado": RegistrarResultadoManualForm(),
            "form_validar": ValidarVerificacionForm(),
        }
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        verificacion = self._get_verificacion(pk)
        accion = request.POST.get("accion")
        try:
            if accion == "cargar_resultado":
                self._cargar_resultado(request, verificacion)
            elif accion == "validar":
                self._validar(request, verificacion)
            else:
                messages.error(request, "Acción no reconocida.")
        except DomainError as error:
            messages.error(request, str(error))
        return redirect(reverse("antecedentes:detalle", args=[verificacion.pk]))

    def _cargar_resultado(self, request, verificacion):
        form = RegistrarResultadoManualForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return
        services.registrar_resultado_manual(
            verificacion=verificacion, actor=request.user,
            resultado=form.cleaned_data["resultado"], numero_certificado=form.cleaned_data["numero_certificado"],
            proveedor=form.cleaned_data["proveedor"], comentario=form.cleaned_data["comentario"],
        )
        messages.success(request, "Resultado cargado. Queda pendiente de validación.")

    def _validar(self, request, verificacion):
        form = ValidarVerificacionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return
        services.validar_verificacion(
            verificacion=verificacion, revisor=request.user,
            decision=form.cleaned_data["decision"], comentario=form.cleaned_data["comentario"],
            vigente_hasta=form.cleaned_data["vigente_hasta"],
        )
        messages.success(request, "Decisión registrada.")