from django.db import migrations

ROLES = {
    "operador_antecedentes": (
        "Operador de antecedentes",
        "Inicia verificaciones, realiza la consulta asistida, carga documentos y registra autorizaciones; "
        "no ve el resultado ni el certificado.",
    ),
    "revisor_antecedentes": (
        "Revisor de antecedentes",
        "Ve resultados y certificados, y valida, devuelve o anula las verificaciones que no cargó.",
    ),
}


def crear_roles(apps, schema_editor):
    Rol = apps.get_model("identidad", "Rol")
    for codigo, (nombre, descripcion) in ROLES.items():
        Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre, "descripcion": descripcion})


class Migration(migrations.Migration):
    dependencies = [
        ("antecedentes", "0004_autorizacion_finalidad_idempotencia"),
        ("identidad", "0002_cuenta_portal"),
    ]

    operations = [migrations.RunPython(crear_roles, migrations.RunPython.noop)]
