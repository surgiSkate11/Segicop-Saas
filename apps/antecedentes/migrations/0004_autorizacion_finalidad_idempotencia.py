import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.antecedentes.models

CERRADOS = ("VALIDADA", "VENCIDA", "CANCELADA", "FALLIDA")


def preparar_filas_existentes(apps, schema_editor):
    """Clave propia para cada fila y una sola solicitud activa por persona y finalidad (la más reciente)."""
    Verificacion = apps.get_model("antecedentes", "VerificacionAntecedentes")
    Ejecucion = apps.get_model("antecedentes", "EjecucionConector")
    for verificacion in Verificacion.objects.all():
        Verificacion.objects.filter(pk=verificacion.pk).update(clave_idempotencia=uuid.uuid4().hex)
    vistos = set()
    activas = Verificacion.objects.exclude(estado__in=CERRADOS).order_by("-created_at")
    for verificacion in activas:
        clave = (verificacion.persona_id, verificacion.finalidad)
        if clave not in vistos:
            vistos.add(clave)
            continue
        # La bitácora no se reescribe (RN-001): solo se anexa una fila.
        Verificacion.objects.filter(pk=verificacion.pk).update(estado="CANCELADA")
        Ejecucion.objects.create(
            verificacion=verificacion,
            estado_resultante="CANCELADA",
            exito=True,
            detalle="Migración de autorizaciones: anulada por solicitud activa duplicada.",
        )


class Migration(migrations.Migration):

    dependencies = [
        ('antecedentes', '0003_nueva_maquina_estados'),
        ('identidad', '0001_initial'),
        ('personas', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AutorizacionDatos',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('finalidad', models.CharField(choices=[('HABILITACION_INGRESO', 'Habilitación de ingreso a instalaciones'), ('RENOVACION', 'Renovación por vencimiento')], max_length=30)),
                ('texto', models.TextField(help_text='Texto del consentimiento tal como lo aceptó la persona.')),
                ('version', models.CharField(max_length=30)),
                ('medio', models.CharField(choices=[('DOCUMENTO_FIRMADO', 'Documento firmado'), ('PORTAL', 'Portal'), ('OTRO', 'Otro')], max_length=20)),
                ('referencia_evidencia', models.CharField(blank=True, max_length=255)),
                ('otorgada_en', models.DateField()),
                ('revocada_en', models.DateTimeField(blank=True, null=True)),
                ('motivo_revocacion', models.TextField(blank=True)),
                ('organizacion', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='%(class)ss', to='identidad.organizacion')),
                ('persona', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='autorizaciones_datos', to='personas.persona')),
                ('registrada_por', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Autorización de datos',
                'verbose_name_plural': 'Autorizaciones de datos',
                'indexes': [models.Index(fields=['persona', 'finalidad'], name='antecedente_persona_970b6e_idx')],
            },
        ),
        migrations.AddField(
            model_name='verificacionantecedentes',
            name='finalidad',
            field=models.CharField(choices=[('HABILITACION_INGRESO', 'Habilitación de ingreso a instalaciones'), ('RENOVACION', 'Renovación por vencimiento')], default='HABILITACION_INGRESO', max_length=30),
        ),
        migrations.AddField(
            model_name='verificacionantecedentes',
            name='autorizacion',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='verificaciones', to='antecedentes.autorizaciondatos'),
        ),
        migrations.AddField(
            model_name='verificacionantecedentes',
            name='solicitante',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='verificacionantecedentes',
            name='responsable',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        # Primero sin unicidad: un default callable se evaluaría una sola vez para todas las filas.
        migrations.AddField(
            model_name='verificacionantecedentes',
            name='clave_idempotencia',
            field=models.CharField(default='', max_length=64),
        ),
        migrations.RunPython(preparar_filas_existentes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='verificacionantecedentes',
            name='clave_idempotencia',
            field=models.CharField(default=apps.antecedentes.models.generar_clave_idempotencia, max_length=64, unique=True),
        ),
        migrations.AddConstraint(
            model_name='verificacionantecedentes',
            constraint=models.UniqueConstraint(condition=models.Q(('estado__in', ('VALIDADA', 'VENCIDA', 'CANCELADA', 'FALLIDA')), _negated=True), fields=('persona', 'finalidad'), name='una_solicitud_activa_por_persona_y_finalidad'),
        ),
    ]
