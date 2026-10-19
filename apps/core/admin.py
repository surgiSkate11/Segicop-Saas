import smtplib

from django import forms
from django.contrib import admin, messages

from apps.core.correo import enviar_correo
from apps.core.models import AlertaNotificacion, ConfiguracionCorreo, EventoAuditoria, ExportacionAuditoria


@admin.register(ExportacionAuditoria)
class ExportacionAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tipo", "formato", "filas", "actor", "hash_sha256")
    list_filter = ("tipo", "formato", "organizacion")
    search_fields = ("hash_sha256", "actor__email")
    readonly_fields = (
        "actor",
        "organizacion",
        "tipo",
        "formato",
        "filtros",
        "filas",
        "hash_sha256",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AlertaNotificacion)
class AlertaNotificacionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "tipo", "agrupador", "titulo", "severidad", "estado", "organizacion")
    list_filter = ("tipo", "severidad", "estado", "organizacion")
    search_fields = ("titulo", "mensaje", "clave_idempotencia")
    readonly_fields = (
        "organizacion",
        "clave_idempotencia",
        "tipo",
        "agrupador",
        "titulo",
        "mensaje",
        "severidad",
        "estado",
        "fecha_referencia",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EventoAuditoria)
class EventoAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("created_at", "accion", "recurso_tipo", "recurso_id", "actor", "organizacion", "resultado")
    list_filter = ("accion", "resultado", "organizacion")
    search_fields = ("recurso_tipo", "recurso_id", "motivo", "actor__email")
    readonly_fields = (
        "actor",
        "organizacion",
        "accion",
        "recurso_tipo",
        "recurso_id",
        "motivo",
        "metadata",
        "resultado",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ConfiguracionCorreoForm(forms.ModelForm):
    """La clave SMTP nunca se vuelve a mostrar; dejarla vacía conserva la anterior."""

    password = forms.CharField(
        label="Clave SMTP",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Déjala vacía para conservar la clave guardada.",
    )

    class Meta:
        model = ConfiguracionCorreo
        fields = "__all__"

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not password and self.instance.pk:
            return ConfiguracionCorreo.objects.get(pk=self.instance.pk).password
        return password


@admin.register(ConfiguracionCorreo)
class ConfiguracionCorreoAdmin(admin.ModelAdmin):
    form = ConfiguracionCorreoForm
    list_display = ("remitente_email", "host", "puerto", "activa", "updated_at")
    actions = ["enviar_correo_de_prueba"]
    fieldsets = (
        (None, {"fields": ("activa",)}),
        ("Servidor", {"fields": ("host", "puerto", "usuario", "password", "usar_tls", "usar_ssl", "timeout")}),
        ("Remitente", {"fields": ("remitente_nombre", "remitente_email")}),
    )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.action(description="Enviar correo de prueba a mi correo")
    def enviar_correo_de_prueba(self, request, queryset):
        for configuracion in queryset:
            try:
                enviar_correo(
                    asunto="SIGECOP · Correo de prueba",
                    destinatarios=[request.user.email],
                    texto=f"Este correo confirma que la configuración {configuracion} funciona.",
                    configuracion=configuracion,
                )
            except (smtplib.SMTPException, OSError) as error:
                self.message_user(request, f"Falló el envío con {configuracion}: {error}", messages.ERROR)
            else:
                self.message_user(request, f"Correo de prueba enviado a {request.user.email} con {configuracion}.")
