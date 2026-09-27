from django.contrib import admin

from apps.empresas.models import Contrato, EmpresaContratista


class ContratoInline(admin.TabularInline):
    model = Contrato
    extra = 0
    fields = ("numero", "fecha_inicio", "fecha_fin", "activo")


@admin.register(EmpresaContratista)
class EmpresaContratistaAdmin(admin.ModelAdmin):
    list_display = ("razon_social", "ruc", "organizacion", "estado")
    list_filter = ("organizacion", "estado")
    search_fields = ("razon_social", "ruc")
    inlines = [ContratoInline]


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ("numero", "empresa", "fecha_inicio", "fecha_fin", "activo")
    list_filter = ("activo", "organizacion")
    search_fields = ("numero", "empresa__razon_social")
    filter_horizontal = ("instalaciones",)