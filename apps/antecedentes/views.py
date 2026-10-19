from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.antecedentes import maquina_estados, services
from apps.antecedentes.alcance import organizaciones_del_usuario
from apps.antecedentes.forms import AutorizacionDatosForm, RegistrarResultadoManualForm, ValidarVerificacionForm
from apps.antecedentes.models import EstadoVerificacion, FinalidadConsulta, VerificacionAntecedentes
from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx, render_modal
from apps.personas.models import Persona


class BandejaAntecedentesView(LoginRequiredMixin, ListView):
    model = VerificacionAntecedentes
    template_name = "antecedentes/bandeja.html"
    context_object_name = "verificaciones"
    paginate_by = 25

    def get_queryset(self):
        organizaciones_ids = organizaciones_del_usuario(self.request.user)
        return (
            VerificacionAntecedentes.objects.filter(
                organizacion_id__in=organizaciones_ids,
                estado__in=maquina_estados.ESTADOS_BANDEJA,
            )
            .select_related("persona")
            .order_by("created_at")
        )


class IniciarVerificacionView(LoginRequiredMixin, View):
    def post(self, request, persona_pk):
        organizaciones_ids = organizaciones_del_usuario(request.user)
        persona = get_object_or_404(Persona, pk=persona_pk, organizacion_id__in=organizaciones_ids)
        try:
            verificacion, creada = services.iniciar_verificacion(
                persona=persona, actor=request.user, finalidad=FinalidadConsulta.HABILITACION_INGRESO
            )
        except DomainError as error:
            messages.error(request, str(error))
            return redirect(reverse("personas:lista"))
        if creada:
            messages.success(request, "Verificación de antecedentes iniciada.")
        else:
            messages.success(request, "Ya existía una solicitud activa para esta persona; se muestra esa.")
        return redirect(reverse("antecedentes:detalle", args=[verificacion.pk]))


class VerificacionDetalleView(LoginRequiredMixin, View):
    template_name = "antecedentes/detalle.html"

    def _get_verificacion(self, pk):
        organizaciones_ids = organizaciones_del_usuario(self.request.user)
        return get_object_or_404(
            VerificacionAntecedentes.objects.select_related("persona", "autorizacion"),
            pk=pk,
            organizacion_id__in=organizaciones_ids,
        
        )

    def get(self, request, pk):
        verificacion = self._get_verificacion(pk)
        contexto = {
            "verificacion": verificacion,
            "ejecuciones": verificacion.ejecuciones.all(),
            "autorizacion": verificacion.autorizacion,
            "espera_autorizacion": verificacion.estado == EstadoVerificacion.AUTORIZACION_PENDIENTE,
            "puede_cargar": verificacion.estado in maquina_estados.ESTADOS_CARGA_MANUAL,
            "puede_decidir": verificacion.estado in maquina_estados.ESTADOS_DECISION,
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
            verificacion=verificacion,
            actor=request.user,
            resultado=form.cleaned_data["resultado"],
            numero_certificado=form.cleaned_data["numero_certificado"],
            proveedor=form.cleaned_data["proveedor"],
            comentario=form.cleaned_data["comentario"],
        )
        messages.success(request, "Resultado cargado. Queda pendiente de revisión.")

    def _validar(self, request, verificacion):
        form = ValidarVerificacionForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Revisa el formulario: " + form.errors.as_text())
            return
        services.validar_verificacion(
            verificacion=verificacion,
            revisor=request.user,
            decision=form.cleaned_data["decision"],
            comentario=form.cleaned_data["comentario"],
            vigente_hasta=form.cleaned_data["vigente_hasta"],
        )
        messages.success(request, "Decisión registrada.")


class AutorizarVerificacionModalView(LoginRequiredMixin, View):
    """Modal HTMX para registrar la autorización de la persona de una verificación pendiente."""

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect(reverse("antecedentes:detalle", args=[kwargs["pk"]]))
        return super().dispatch(request, *args, **kwargs)

    def _get_verificacion(self, request, pk):
        return get_object_or_404(
            VerificacionAntecedentes.objects.select_related("persona"),
            pk=pk,
            organizacion_id__in=organizaciones_del_usuario(request.user),
        )

    def _modal(self, request, form, verificacion):
        return render_modal(
            request,
            form=form,
            titulo=f"Autorización · {verificacion.persona.nombre_completo}",
            accion_url=reverse("antecedentes:autorizar", args=[verificacion.pk]),
            boton="Registrar autorización",
        )

    def get(self, request, pk):
        verificacion = self._get_verificacion(request, pk)
        form = AutorizacionDatosForm(finalidad_label=verificacion.get_finalidad_display())
        return self._modal(request, form, verificacion)

    def post(self, request, pk):
        verificacion = self._get_verificacion(request, pk)
        form = AutorizacionDatosForm(request.POST, finalidad_label=verificacion.get_finalidad_display())
        if not form.is_valid():
            return self._modal(request, form, verificacion)
        if verificacion.estado != EstadoVerificacion.AUTORIZACION_PENDIENTE:
            form.add_error(None, "Esta verificación ya no espera una autorización.")
            return self._modal(request, form, verificacion)
        try:
            services.registrar_autorizacion(
                persona=verificacion.persona,
                finalidad=verificacion.finalidad,
                actor=request.user,
                **form.cleaned_data,
            )
        except DomainError as error:
            form.add_error(None, str(error))
            return self._modal(request, form, verificacion)
        return exito_htmx(
            request, mensaje="Autorización registrada.", url=reverse("antecedentes:detalle", args=[verificacion.pk])
        )
