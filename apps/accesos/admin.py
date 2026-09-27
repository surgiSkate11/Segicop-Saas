from django.contrib import admin

from apps.accesos.models import EventoAcceso, PermisoIngreso


class EventoAccesoInline(admin.TabularInline):
    model = EventoAcceso
    extra = 0
    fields = ("tipo", "motivo_denegacion", "registrado_por", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(PermisoIngreso)
class PermisoIngresoAdmin(admin.ModelAdmin):
    list_display = ("persona", "instalacion", "estado", "fecha_inicio_vigencia", "fecha_fin_vigencia")
    list_filter = ("organizacion", "estado", "instalacion")
    search_fields = ("persona__nombres", "persona__apellidos", "codigo")
    readonly_fields = ("codigo",)
    inlines = [EventoAccesoInline]


@admin.register(EventoAcceso)
class EventoAccesoAdmin(admin.ModelAdmin):
    list_display = ("persona", "instalacion", "tipo", "registrado_por", "created_at")
    list_filter = ("tipo", "instalacion")

    def has_change_permission(self, request, obj=None):
        return False