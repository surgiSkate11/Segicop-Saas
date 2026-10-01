from django.dispatch import Signal

from apps.core.models import EventoAuditoria

evento_dominio = Signal()


def _persistir_evento(sender, *, actor, accion, recurso, organizacion, motivo, metadata, resultado, **kwargs):
    EventoAuditoria.objects.create(
        actor=actor,
        organizacion=organizacion,
        accion=accion,
        recurso_tipo=sender._meta.label,
        recurso_id=str(getattr(recurso, "pk", "")),
        motivo=motivo,
        metadata=metadata,
        resultado=resultado,
    )


evento_dominio.connect(_persistir_evento, dispatch_uid="core.persistir_evento_auditoria")


def emitir_evento(*, actor, accion, recurso, organizacion=None, motivo="", metadata=None, resultado="OK"):
    evento_dominio.send(
        sender=recurso.__class__,
        actor=actor,
        accion=accion,
        recurso=recurso,
        organizacion=organizacion,
        motivo=motivo,
        metadata=metadata or {},
        resultado=resultado,
    )
