from django.contrib import admin

from apps.personas.models import Asignacion, Persona


class AsignacionInline(admin.TabularInline):
    model = Asignacion
    extra = 0
    fields = ("empresa", "contrato", "cargo_o_actividad", "fecha_inicio", "fecha_fin", "activa")


@admin.register(Persona)
class PersonaAdmin(admin.ModelAdmin):
    list_display = ("nombre_completo", "tipo_documento", "numero_documento", "organizacion", "estado")
    list_filter = ("organizacion", "estado", "tipo_documento")
    search_fields = ("nombres", "apellidos", "numero_documento")
    inlines = [AsignacionInline]


@admin.register(Asignacion)
class AsignacionAdmin(admin.ModelAdmin):
    list_display = ("persona", "empresa", "contrato", "cargo_o_actividad", "activa")
    list_filter = ("activa", "organizacion", "empresa")
    search_fields = ("persona__nombres", "persona__apellidos", "persona__numero_documento")