# Create your models here.
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import BaseModel
from apps.identidad.managers import UsuarioManager


class Organizacion(BaseModel):
    """
    Cliente o espacio lógico que aísla configuración y datos (tenant raíz).
    """

    nombre = models.CharField(max_length=255)
    identificador_legal = models.CharField(
        max_length=50,
        blank=True,
        help_text="RUC u otro identificador legal de la organización cliente.",
    )
    zona_horaria = models.CharField(max_length=50, default="America/Guayaquil")
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Organización"
        verbose_name_plural = "Organizaciones"

    def __str__(self):
        return self.nombre


class Instalacion(BaseModel):
    """
    Sitio donde se controla ingreso y trabajo (RF-040, RF-050, garita).
    """

    organizacion = models.ForeignKey(Organizacion, on_delete=models.PROTECT, related_name="instalaciones")
    nombre = models.CharField(max_length=255)
    direccion = models.CharField(max_length=255, blank=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Instalación"
        verbose_name_plural = "Instalaciones"
        constraints = [
            models.UniqueConstraint(
                fields=["organizacion", "nombre"],
                name="instalacion_unica_por_organizacion",
            )
        ]

    def __str__(self):
        return f"{self.nombre} ({self.organizacion.nombre})"


class Usuario(BaseModel, AbstractBaseUser, PermissionsMixin):
    """
    Identidad de acceso (RF-001 a RF-004). No tiene organización directa:
    un mismo usuario puede tener distintos roles en distintas
    organizaciones (ver RolAsignado).
    """

    email = models.EmailField(unique=True)
    nombre_completo = models.CharField(max_length=255)
    mfa_habilitado = models.BooleanField(
        default=False,
        help_text="RF-001: obligatorio para perfiles administrativos y revisores de información sensible.",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UsuarioManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nombre_completo"]

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return f"{self.nombre_completo} <{self.email}>"


class Rol(BaseModel):
    """
    Catálogo de roles funcionales. Es una TABLA, no un choices() fijo,
    porque el sistema debe soportar roles futuros. Los 7 roles iniciales
    se cargan por fixture (siguiente paso), no hardcodeados aquí.
    """

    codigo = models.SlugField(max_length=50, unique=True)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    es_rol_plataforma = models.BooleanField(
        default=False,
        help_text="True solo para 'Administrador de plataforma'.",
    )

    class Meta:
        verbose_name = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.nombre


class RolAsignado(BaseModel):
    """
    Asignación de un rol a un usuario dentro de una organización.
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="roles_asignados")
    organizacion = models.ForeignKey(
        Organizacion,
        on_delete=models.CASCADE,
        related_name="roles_asignados",
        null=True,
        blank=True,
        help_text="Nulo únicamente cuando rol.es_rol_plataforma=True.",
    )
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, related_name="asignaciones")
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Rol asignado"
        verbose_name_plural = "Roles asignados"
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "organizacion", "rol"],
                name="rol_unico_por_usuario_y_organizacion",
            )
        ]

    def __str__(self):
        org = self.organizacion.nombre if self.organizacion else "plataforma"
        return f"{self.usuario.email} · {self.rol.nombre} · {org}"


class AlcanceAsignado(BaseModel):
    """
    Restringe un RolAsignado a un subconjunto de recursos (p.ej. una
    instalación). Usa GenericForeignKey para que `identidad` no dependa
    de apps futuras (empresas, cumplimiento). Sin filas aquí => el rol
    aplica a toda la organización.
    """

    rol_asignado = models.ForeignKey(RolAsignado, on_delete=models.CASCADE, related_name="alcances")
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    recurso = GenericForeignKey("content_type", "object_id")
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = "Alcance asignado"
        verbose_name_plural = "Alcances asignados"

    def __str__(self):
        return f"Alcance de {self.rol_asignado} sobre {self.recurso}"
