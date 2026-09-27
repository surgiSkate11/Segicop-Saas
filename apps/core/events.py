from django.dispatch import Signal

evento_dominio = Signal()

def emitir_evento(*, actor, accion, recurso, organizacion=None, motivo="", metadata=None, resultado="OK"):
    evento_dominio.send(
        sender=recurso.__class__, actor=actor, accion=accion, recurso=recurso,
        organizacion=organizacion, motivo=motivo, metadata=metadata or {}, resultado=resultado,
    )