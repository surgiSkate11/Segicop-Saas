from django.contrib import admin

from apps.antecedentes.models import AutorizacionDatos, EjecucionConector, VerificacionAntecedentes


class EjecucionConectorInline(admin.TabularInline):
    model = EjecucionConector
    extra = 0
    fields = ("estado_resultante", "exito", "detalle", "created_at")
    readonly_fields = fields
    can_delete = False


@admin.register(VerificacionAntecedentes)
class VerificacionAntecedentesAdmin(admin.ModelAdmin):
    list_display = ("persona", "finalidad", "estado", "modo_conector", "resultado", "vigente_hasta")
    list_filter = ("organizacion", "estado", "finalidad", "modo_conector")
    search_fields = ("persona__nombres", "persona__apellidos")
    inlines = [EjecucionConectorInline]

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AutorizacionDatos)
class AutorizacionDatosAdmin(admin.ModelAdmin):
    list_display = ("persona", "finalidad", "medio", "version", "otorgada_en", "revocada_en")
    list_filter = ("organizacion", "finalidad", "medio")
    search_fields = ("persona__nombres", "persona__apellidos", "persona__numero_documento")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
