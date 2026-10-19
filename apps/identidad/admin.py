from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.identidad.models import (
    AlcanceAsignado,
    Instalacion,
    Organizacion,
    Rol,
    RolAsignado,
    Usuario,
)


class TOTPDeviceInline(admin.TabularInline):
    model = TOTPDevice
    extra = 0
    fields = ("name", "key", "step", "digits", "confirmed", "last_t")
    readonly_fields = ("last_t",)


@admin.register(Organizacion)
class OrganizacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "identificador_legal", "activa", "created_at")
    search_fields = ("nombre", "identificador_legal")
    list_filter = ("activa",)


@admin.register(Instalacion)
class InstalacionAdmin(admin.ModelAdmin):
    list_display = ("nombre", "organizacion", "activa")
    list_filter = ("organizacion", "activa")
    search_fields = ("nombre",)


@admin.register(Usuario)
class UsuarioAdmin(DjangoUserAdmin):
    """
    Adapta el UserAdmin estándar de Django al Usuario custom (email en
    vez de username).
    """

    ordering = ("email",)
    list_display = ("email", "nombre_usuario", "nombre_completo", "tipo_cuenta", "is_active", "is_staff", "mfa_habilitado")
    list_filter = ("tipo_cuenta", "is_active", "is_staff", "mfa_habilitado")
    search_fields = ("email", "nombre_usuario", "nombre_completo")
    readonly_fields = ("tipo_cuenta", "cuenta_activada_at")
    fieldsets = (
        (None, {"fields": ("email", "nombre_usuario", "password")}),
        ("Información personal", {"fields": ("nombre_completo",)}),
        ("Cuenta de portal", {"fields": ("tipo_cuenta", "cuenta_activada_at")}),
        (
            "Permisos",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "mfa_habilitado",
                    "groups",
                    "user_permissions",
                )
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "nombre_completo", "password1", "password2"),
            },
        ),
    )
    inlines = [TOTPDeviceInline]


@admin.register(Rol)
class RolAdmin(admin.ModelAdmin):
    list_display = ("nombre", "codigo", "es_rol_plataforma")
    search_fields = ("nombre", "codigo")


class AlcanceAsignadoInline(admin.TabularInline):
    model = AlcanceAsignado
    extra = 0


@admin.register(RolAsignado)
class RolAsignadoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "organizacion", "rol", "activo")
    list_filter = ("organizacion", "rol", "activo")
    search_fields = ("usuario__email", "usuario__nombre_completo")
    inlines = [AlcanceAsignadoInline]
