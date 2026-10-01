from django.contrib import admin

from apps.antecedentes.models import EjecucionConector, VerificacionAntecedentes


class EjecucionConectorInline(admin.TabularInline):
    model = EjecucionConector
    extra = 0
    fields = ("estado_resultante", "exito", "detalle", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(VerificacionAntecedentes)
class VerificacionAntecedentesAdmin(admin.ModelAdmin):
    list_display = ("persona", "estado", "modo_conector", "resultado", "vigente_hasta")
    list_filter = ("organizacion", "estado", "modo_conector")
    search_fields = ("persona__nombres", "persona__apellidos")
    inlines = [EjecucionConectorInline]

    def has_change_permission(self, request, obj=None):
        return False
