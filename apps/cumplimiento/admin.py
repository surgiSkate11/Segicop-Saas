from django.contrib import admin

from apps.cumplimiento.models import CatalogoRequisito, DocumentoVersion, RequisitoAplicado, Revision


@admin.register(CatalogoRequisito)
class CatalogoRequisitoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "aplica_a", "obligatorio", "vigencia_dias", "activo", "organizacion")
    list_filter = ("organizacion", "aplica_a", "obligatorio", "activo")
    search_fields = ("nombre", "codigo")


class DocumentoVersionInline(admin.TabularInline):
    model = DocumentoVersion
    extra = 0
    fields = ("version", "archivo", "vigente", "fecha_vencimiento", "subido_por")
    readonly_fields = ("version", "hash_sha256")


@admin.register(RequisitoAplicado)
class RequisitoAplicadoAdmin(admin.ModelAdmin):
    list_display = ("catalogo", "empresa", "persona", "estado", "fecha_limite")
    list_filter = ("organizacion", "estado", "catalogo")
    search_fields = ("empresa__razon_social", "persona__nombres", "persona__apellidos")
    inlines = [DocumentoVersionInline]


@admin.register(DocumentoVersion)
class DocumentoVersionAdmin(admin.ModelAdmin):
    list_display = ("requisito_aplicado", "version", "vigente", "fecha_vencimiento", "subido_por")
    list_filter = ("vigente", "organizacion")
    readonly_fields = ("hash_sha256", "version")


@admin.register(Revision)
class RevisionAdmin(admin.ModelAdmin):
    list_display = ("documento_version", "decision", "revisor", "created_at")
    list_filter = ("decision", "organizacion")
    readonly_fields = ("documento_version", "revisor", "decision", "comentario")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False