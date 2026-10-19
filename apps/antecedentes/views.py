import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView

from apps.antecedentes import maquina_estados, permisos, services
from apps.antecedentes.forms import (
    AutorizacionDatosForm,
    RegistrarResultadoManualForm,
    RevocarAutorizacionForm,
    ValidarVerificacionForm,
)
from apps.antecedentes.models import (
    DocumentoAntecedentes,
    EstadoVerificacion,
    FinalidadConsulta,
    ModoConector,
    VerificacionAntecedentes,
)
from apps.antecedentes.pdf import DocumentoInvalido
from apps.core.exceptions import DomainError
from apps.core.modals import exito_htmx, render_modal
from apps.personas.models import Persona

MENSAJE_CUARENTENA = "Se conserva el original en cuarentena; carga otro archivo o solicita revisión."


class BandejaAntecedentesView(permisos.OperadorRequeridoMixin, ListView):
    model = VerificacionAntecedentes
    template_name = "antecedentes/bandeja.html"
    context_object_name = "verificaciones"
    paginate_by = 25

    def _bandeja(self):
        return permisos.verificaciones_visibles(self.request.user).filter(estado__in=maquina_estados.ESTADOS_BANDEJA)

    def _estado_filtrado(self):
        """Un estado fuera de la bandeja (o desconocido) no filtra: se muestran todos los casos."""
        estado = self.request.GET.get("estado", "")
        return estado if estado in maquina_estados.ESTADOS_BANDEJA else ""

    def get_queryset(self):
        # La bandeja nunca muestra resultados, ni siquiera al revisor.
        casos = self._bandeja()
        if self._estado_filtrado():
            casos = casos.filter(estado=self._estado_filtrado())
        return casos.select_related("persona").order_by("created_at")

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        conteos = {fila["estado"]: fila["total"] for fila in self._bandeja().values("estado").annotate(total=Count("pk")).order_by()}
        activo = self._estado_filtrado()
        contexto["pestanas"] = [
            {"estado": "", "etiqueta": "Todas", "conteo": sum(conteos.values()), "activa": not activo},
            *(
                {"estado": estado, "etiqueta": EstadoVerificacion(estado).label, "conteo": conteos.get(estado, 0), "activa": estado == activo}
                for estado in maquina_estados.ESTADOS_BANDEJA
            ),
        ]
        contexto["estado_filtrado"] = activo
        # Cada fallida indica desde cuándo se puede reintentar (None: ya se puede).
        contexto["verificaciones"] = casos = list(contexto["verificaciones"])
        for caso in casos:
            if caso.estado == EstadoVerificacion.FALLIDA:
                caso.reintentar_desde = services.reintentar_desde(caso)
        return contexto


class IniciarVerificacionView(LoginRequiredMixin, View):
    def post(self, request, persona_pk):
        # Sin rol operativo en la organización de la persona, esta no existe (404).
        persona = get_object_or_404(
            Persona, pk=persona_pk, organizacion_id__in=permisos.organizaciones_operables(request.user)
        )
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

    def _verificacion_para_accion(self, pk):
        """Las acciones exigen rol operativo en la organización de la verificación; sin él, no existe (404)."""
        return get_object_or_404(
            permisos.verificaciones_visibles(self.request.user).select_related("persona", "autorizacion"), pk=pk
        )

    def get(self, request, pk):
        # Sin rol en la organización el caso no existe (404): no se revela su existencia.
        verificacion = get_object_or_404(
            permisos.verificaciones_visibles(request.user).select_related("persona", "autorizacion"), pk=pk
        )
        es_revisor = permisos.puede_revisar(request.user, verificacion.organizacion_id)
        services.registrar_lectura(usuario=request.user, verificacion=verificacion, resultado_visible=es_revisor)
        espera_decision = verificacion.estado in maquina_estados.ESTADOS_DECISION
        cargado_por_mi = services.aporto_evidencia(verificacion, request.user)
        contexto = {
            "verificacion": verificacion,
            "ejecuciones": verificacion.ejecuciones.all(),
            "autorizacion": verificacion.autorizacion,
            "espera_autorizacion": verificacion.estado == EstadoVerificacion.AUTORIZACION_PENDIENTE,
            "puede_cargar": verificacion.estado in maquina_estados.ESTADOS_CARGA_MANUAL,
            "consulta_asistida": verificacion.estado in (EstadoVerificacion.CAPTCHA_REQUERIDO, EstadoVerificacion.EN_CONSULTA)
            and verificacion.modo_conector == ModoConector.ASISTIDO,
            "reintentar_desde": services.reintentar_desde(verificacion),
            "puede_revocar": verificacion.autorizacion is not None and not verificacion.autorizacion.revocada_en,
            # Lo extraído del certificado solo llega a la plantilla del revisor (S8).
            "documento": self._ultimo_documento(verificacion) if es_revisor else None,
            "ver_resultado": es_revisor,
            "puede_decidir": espera_decision and es_revisor and not cargado_por_mi,
            "bloqueado_cuatro_ojos": espera_decision and es_revisor and cargado_por_mi,
            "espera_revision": espera_decision and not es_revisor,
            "form_resultado": RegistrarResultadoManualForm(),
            # S9: se propone la vigencia leída del certificado; el revisor solo puede acortarla.
            "form_validar": ValidarVerificacionForm(
                initial={"vigente_hasta": services.vigencia_del_certificado(verificacion) if es_revisor else None}
            ),
        }
        return render(request, self.template_name, contexto)

    def post(self, request, pk):
        verificacion = self._verificacion_para_accion(pk)
        accion = request.POST.get("accion")
        try:
            if accion == "continuar":
                # El formulario se envía con target="_blank": el portal se abre en otra pestaña.
                return redirect(services.continuar_consulta(verificacion=verificacion, actor=request.user))
            if accion == "reintentar":
                services.reintentar_consulta(verificacion=verificacion, actor=request.user)
                messages.success(request, "Se volvió a consultar el portal oficial.")
            elif accion == "cargar_certificado":
                self._cargar_certificado(request, verificacion)
            elif accion == "cargar_resultado":
                self._cargar_resultado(request, verificacion)
            elif accion == "validar":
                self._validar(request, verificacion)
            else:
                messages.error(request, "Acción no reconocida.")
        except DomainError as error:
            messages.error(request, str(error))
        return redirect(reverse("antecedentes:detalle", args=[verificacion.pk]))

    @staticmethod
    def _ultimo_documento(verificacion):
        return (
            DocumentoAntecedentes.objects.filter(consulta__verificacion=verificacion).order_by("-created_at").first()
        )

    def _cargar_certificado(self, request, verificacion):
        archivo = request.FILES.get("archivo")
        if not archivo:
            messages.error(request, "Selecciona el certificado en PDF.")
            return
        try:
            documento, creado = services.cargar_certificado(verificacion=verificacion, archivo=archivo, actor=request.user)
        except DocumentoInvalido as error:
            # Solo se habla de cuarentena si el original llegó a guardarse.
            sufijo = f" {MENSAJE_CUARENTENA}" if error.documento else ""
            messages.error(request, f"{error}{sufijo}")
            return
        # Nunca se muestra lo extraído al operador: solo avisos de qué pasó con el caso.
        if not creado:
            messages.info(request, "Ese certificado ya estaba cargado.")
        elif documento.codigo_error == services.ANT_IDENTITY_MISMATCH:
            messages.warning(
                request,
                "El certificado no coincide con la identidad de la persona. "
                "Se conservó el original y el caso pasó a revisión.",
            )
        elif documento.codigo_error == services.ANT_RESULT_AMBIGUOUS:
            messages.warning(
                request,
                "No se pudo reconocer el resultado del certificado. "
                "Se conservó el original y el caso pasó a revisión.",
            )
        else:
            messages.success(request, "Certificado recibido. Queda pendiente de revisión.")

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
        permisos.exigir_revisar(request.user, verificacion.organizacion_id)
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


class ModalVerificacionView(LoginRequiredMixin, View):
    """Base de los modales HTMX de una verificación: sin HTMX se vuelve al detalle; opera quien tenga rol."""

    def dispatch(self, request, *args, **kwargs):
        if not request.htmx:
            return redirect(reverse("antecedentes:detalle", args=[kwargs["pk"]]))
        return super().dispatch(request, *args, **kwargs)

    def _get_verificacion(self, request, pk):
        return get_object_or_404(
            permisos.verificaciones_visibles(request.user).select_related("persona", "autorizacion"), pk=pk
        )


class AutorizarVerificacionModalView(ModalVerificacionView):
    """Modal HTMX para registrar la autorización de la persona de una verificación pendiente."""

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


class RevocarAutorizacionModalView(ModalVerificacionView):
    """Modal HTMX para revocar la autorización de una verificación; el motivo es obligatorio."""

    def _modal(self, request, form, verificacion):
        return render_modal(
            request,
            form=form,
            titulo=f"Revocar autorización · {verificacion.persona.nombre_completo}",
            accion_url=reverse("antecedentes:revocar-autorizacion", args=[verificacion.pk]),
            boton="Revocar autorización",
        )

    def get(self, request, pk):
        verificacion = self._get_verificacion(request, pk)
        return self._modal(request, RevocarAutorizacionForm(), verificacion)

    def post(self, request, pk):
        verificacion = self._get_verificacion(request, pk)
        form = RevocarAutorizacionForm(request.POST)
        if not form.is_valid():
            return self._modal(request, form, verificacion)
        if verificacion.autorizacion is None:
            form.add_error(None, "Esta verificación no tiene una autorización registrada.")
            return self._modal(request, form, verificacion)
        try:
            services.revocar_autorizacion(
                autorizacion=verificacion.autorizacion, motivo=form.cleaned_data["motivo"], actor=request.user
            )
        except DomainError as error:
            form.add_error(None, str(error))
            return self._modal(request, form, verificacion)
        return exito_htmx(
            request,
            mensaje="Autorización revocada; la verificación quedó anulada.",
            url=reverse("antecedentes:detalle", args=[verificacion.pk]),
        )


class DescargarDocumentoView(LoginRequiredMixin, View):
    """ANT-011 / S7: el original solo lo lee, autenticado y auditado, un revisor de la organización."""

    def get(self, request, pk):
        # Sin rol en la organización el documento no existe (404); el operador recibe 403.
        documento = get_object_or_404(
            DocumentoAntecedentes.objects.select_related("consulta__verificacion"),
            pk=pk,
            consulta__verificacion__organizacion_id__in=permisos.organizaciones_operables(request.user),
        )
        permisos.exigir_revisar(request.user, documento.consulta.verificacion.organizacion_id)
        try:
            archivo = documento.archivo.open("rb")
        except (ValueError, OSError):
            raise Http404("El archivo no está disponible.")
        services.registrar_descarga(usuario=request.user, documento=documento)
        nombre = os.path.basename(documento.nombre_original) or f"certificado-{documento.pk}.pdf"
        respuesta = FileResponse(archivo, as_attachment=True, filename=nombre, content_type="application/pdf")
        respuesta["X-Content-Type-Options"] = "nosniff"
        respuesta["Cache-Control"] = "private, no-store"
        return respuesta
