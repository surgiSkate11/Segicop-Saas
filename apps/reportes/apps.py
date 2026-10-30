from django.apps import AppConfig


class ReportesConfig(AppConfig):
    """Informes con filtros y exportación auditada. Última en la cadena: lee todas las apps de dominio."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reportes"
    verbose_name = "Reportes"
