from django.contrib import admin

from apps.core.models import AlertaNotificacion, EventoAuditoria, ExportacionAuditoria


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
