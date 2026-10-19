from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.antecedentes import maquina_estados, permisos
from apps.antecedentes.conectores.factory import obtener_conector
from apps.antecedentes.conectores.manual import ConectorManual
from apps.antecedentes.models import (
    ESTADOS_CERRADOS,
    AutorizacionDatos,
    ConsultaProveedor,
    EstadoConsulta,
    EstadoVerificacion,
    FinalidadConsulta,
    IntentoConsulta,
    MedioAutorizacion,
    ModoConector,
    ProveedorAntecedentes,
    ResultadoAntecedentes,
    VerificacionAntecedentes,
)
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.identidad.models import Rol, RolAsignado
from apps.personas.models import Persona


@transaction.atomic
def asignar_rol_antecedentes(*, usuario, organizacion, codigo: str, actor) -> RolAsignado:
    """Asigna (o reactiva) un rol de antecedentes en la organización; el rol se crea bajo demanda."""
    if codigo not in permisos.ROLES:
        raise DomainError(f"Rol de antecedentes inválido: {codigo}")
    nombre, descripcion = permisos.ROLES[codigo]
    rol, _ = Rol.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre, "descripcion": descripcion})
    asignacion, creada = RolAsignado.objects.get_or_create(usuario=usuario, organizacion=organizacion, rol=rol)
    if not creada:
        if asignacion.activo:
            return asignacion
        asignacion.activo = True
        asignacion.save(update_fields=["activo", "updated_at"])
    emitir_evento(
        actor=actor,
        accion="ROL_ANTECEDENTES_ASIGNADO",
        recurso=asignacion,
        organizacion=organizacion,
        motivo=f"{rol.nombre} para {usuario.email}.",
    )
    return asignacion


def registrar_lectura(*, usuario, verificacion: VerificacionAntecedentes, resultado_visible: bool) -> None:
    """ANT-011: toda lectura del detalle de una verificación queda en la auditoría."""
    emitir_evento(
        actor=usuario,
        accion="LEER_ANTECEDENTES",
        recurso=verificacion,
        organizacion=verificacion.organizacion,
        metadata={"resultado_visible": resultado_visible},
    )


def _modo_del_conector(conector) -> str:
    return ModoConector.MANUAL if isinstance(conector, ConectorManual) else ModoConector.ASISTIDO


def _autorizacion_vigente(persona: Persona, finalidad: str):
    """Última autorización no revocada de la misma persona, organización y finalidad."""
    return (
        AutorizacionDatos.objects.filter(
            persona=persona, organizacion=persona.organizacion, finalidad=finalidad, revocada_en__isnull=True
        )
        .order_by("-created_at")
        .first()
    )


def _solicitud_existente(persona: Persona, finalidad: str, clave_idempotencia: str):
    """ANT-002: la operación ya registrada con esa clave o, sin clave, la solicitud activa de la persona."""
    if clave_idempotencia:
        existente = VerificacionAntecedentes.objects.filter(clave_idempotencia=clave_idempotencia).first()
        if existente:
            if (existente.persona_id, existente.finalidad) != (persona.pk, finalidad):
                raise DomainError("La clave de idempotencia ya se usó en otra solicitud.")
            return existente
    return (
        VerificacionAntecedentes.objects.filter(persona=persona, finalidad=finalidad)
        .exclude(estado__in=ESTADOS_CERRADOS)
        .order_by("-created_at")
        .first()
    )


def _arrancar_conector(verificacion: VerificacionAntecedentes, actor) -> None:
    """Desde LISTA: el conector avanza el caso y, si es asistido, se encola la consulta."""
    obtener_conector().iniciar(verificacion, actor=actor)
    if verificacion.modo_conector == ModoConector.ASISTIDO:
        consulta = verificacion.consultas_proveedor.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)
        transaction.on_commit(lambda: _encolar_consulta(consulta.pk))


def iniciar_verificacion(
    *, persona: Persona, actor, finalidad: str = FinalidadConsulta.HABILITACION_INGRESO, clave_idempotencia: str = ""
) -> tuple[VerificacionAntecedentes, bool]:
    """
    Devuelve (verificación, creada). Si la solicitud ya existe (misma clave, o una
    activa de la persona para la misma finalidad) se devuelve sin repetir la consulta.

    SIN @transaction.atomic a propósito: las transiciones del conector y su
    bitácora deben persistir aunque una excepción posterior se propague, o un
    fallo quedaría sin rastro.
    """
    if finalidad not in FinalidadConsulta.values:
        raise DomainError(f"Finalidad inválida: {finalidad}")
    clave_idempotencia = clave_idempotencia.strip()
    existente = _solicitud_existente(persona, finalidad, clave_idempotencia)
    if existente:
        return existente, False

    campos_clave = {"clave_idempotencia": clave_idempotencia} if clave_idempotencia else {}
    try:
        with transaction.atomic():
            verificacion = VerificacionAntecedentes.objects.create(
                organizacion=persona.organizacion,
                persona=persona,
                finalidad=finalidad,
                solicitante=actor,
                estado=EstadoVerificacion.CREADA,
                modo_conector=_modo_del_conector(obtener_conector()),
                **campos_clave,
            )
    except IntegrityError:
        # Carrera con una solicitud idéntica: gana la que ya existe.
        existente = _solicitud_existente(persona, finalidad, clave_idempotencia)
        if existente:
            return existente, False
        raise
    ConsultaProveedor.objects.create(verificacion=verificacion, proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)

    autorizacion = _autorizacion_vigente(persona, finalidad)
    if autorizacion is None:
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.AUTORIZACION_PENDIENTE,
            actor=actor,
            nota_bitacora="Falta la autorización de la persona para consultar sus antecedentes.",
        )
        return verificacion, True

    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.LISTA,
        actor=actor,
        nota_bitacora="Autorización vigente: lista para consultar.",
        autorizacion=autorizacion,
    )
    _arrancar_conector(verificacion, actor)
    return verificacion, True


def registrar_autorizacion(
    *, persona: Persona, finalidad: str, texto: str, version: str, medio: str, referencia_evidencia: str,
    otorgada_en, actor,
) -> AutorizacionDatos:
    """ANT-001: registra el consentimiento y libera las verificaciones que lo esperaban."""
    if finalidad not in FinalidadConsulta.values:
        raise DomainError(f"Finalidad inválida: {finalidad}")
    if medio not in MedioAutorizacion.values:
        raise DomainError(f"Medio inválido: {medio}")
    if not texto.strip() or not version.strip():
        raise DomainError("La autorización requiere el texto aceptado y su versión.")
    if otorgada_en is None:
        raise DomainError("La autorización requiere la fecha en que se otorgó.")
    if medio != MedioAutorizacion.PORTAL and not referencia_evidencia.strip():
        raise DomainError("Indica la referencia de la evidencia (documento firmado u otro medio).")

    with transaction.atomic():
        autorizacion = AutorizacionDatos.objects.create(
            organizacion=persona.organizacion,
            persona=persona,
            finalidad=finalidad,
            texto=texto.strip(),
            version=version.strip(),
            medio=medio,
            referencia_evidencia=referencia_evidencia.strip(),
            otorgada_en=otorgada_en,
            registrada_por=actor,
        )
        emitir_evento(
            actor=actor,
            accion="AUTORIZACION_REGISTRADA",
            recurso=autorizacion,
            organizacion=persona.organizacion,
            motivo=f"{autorizacion.get_finalidad_display()} · {autorizacion.get_medio_display()} · v{autorizacion.version}",
        )
        pendientes = list(
            VerificacionAntecedentes.objects.select_for_update().filter(
                persona=persona,
                organizacion=persona.organizacion,
                finalidad=finalidad,
                estado=EstadoVerificacion.AUTORIZACION_PENDIENTE,
            )
        )
        modo = _modo_del_conector(obtener_conector())
        for verificacion in pendientes:
            maquina_estados.transicionar(
                verificacion=verificacion,
                nuevo_estado=EstadoVerificacion.LISTA,
                actor=actor,
                nota_bitacora="Autorización registrada: lista para consultar.",
                autorizacion=autorizacion,
                modo_conector=modo,
            )
    for verificacion in pendientes:
        _arrancar_conector(verificacion, actor)
    return autorizacion


@transaction.atomic
def revocar_autorizacion(*, autorizacion: AutorizacionDatos, motivo: str, actor) -> AutorizacionDatos:
    """Revoca el consentimiento y anula las verificaciones no terminales que dependen de él."""
    autorizacion = AutorizacionDatos.objects.select_for_update().get(pk=autorizacion.pk)
    if autorizacion.revocada_en:
        raise DomainError("La autorización ya fue revocada.")
    if not motivo.strip():
        raise DomainError("Revocar la autorización requiere indicar el motivo.")

    autorizacion.revocada_en = timezone.now()
    autorizacion.motivo_revocacion = motivo.strip()
    autorizacion.save(update_fields=["revocada_en", "motivo_revocacion", "updated_at"])
    emitir_evento(
        actor=actor,
        accion="AUTORIZACION_REVOCADA",
        recurso=autorizacion,
        organizacion=autorizacion.organizacion,
        motivo=autorizacion.motivo_revocacion,
    )
    for verificacion in autorizacion.verificaciones.exclude(estado__in=maquina_estados.TERMINALES):
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.CANCELADA,
            actor=actor,
            motivo=f"Autorización revocada: {autorizacion.motivo_revocacion}",
        )
    return autorizacion


def _encolar_consulta(consulta_id):
    from apps.antecedentes.tasks import preparar_consulta_asistida

    preparar_consulta_asistida.delay(str(consulta_id))


@transaction.atomic
def cambiar_estado_consulta(
    *,
    consulta: ConsultaProveedor,
    nuevo_estado: str,
    registrar_intento: bool = False,
    detalle_error: str = "",
    codigo_error: str = "",
    proximo_intento_at=None,
) -> ConsultaProveedor:
    """ÚNICO escritor de `ConsultaProveedor.estado`; un intento de integración queda en `IntentoConsulta`."""
    if nuevo_estado not in EstadoConsulta.values:
        raise DomainError(f"Estado de consulta inválido: {nuevo_estado}")

    consulta.estado = nuevo_estado
    consulta.requiere_intervencion = nuevo_estado == EstadoConsulta.CAPTCHA_REQUERIDO
    consulta.detalle_error = detalle_error
    consulta.codigo_error = codigo_error
    consulta.proximo_intento_at = proximo_intento_at
    if registrar_intento:
        consulta.intentos += 1
        consulta.ultimo_intento_at = timezone.now()
    consulta.save()

    if registrar_intento:
        IntentoConsulta.objects.create(
            consulta=consulta,
            numero=consulta.intentos,
            estado_resultante=nuevo_estado,
            codigo_error=codigo_error,
            detalle=detalle_error,
        )
    return consulta


@transaction.atomic
def registrar_resultado_manual(*, verificacion, actor, resultado, numero_certificado="", proveedor="", comentario=""):
    if resultado not in ResultadoAntecedentes.values:
        raise DomainError(f"Resultado inválido: {resultado}")

    # Sin comparación de identidad todavía: todo resultado manual pasa a revisión humana.
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.PDF_RECIBIDO,
        actor=actor,
        nota_bitacora="Resultado cargado manualmente.",
        resultado=resultado,
        numero_certificado=numero_certificado,
        proveedor=proveedor,
        comentario=comentario,
        fecha_resultado=timezone.now(),
        cargado_por=actor,
    )
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.REQUIERE_REVISION,
        actor=actor,
        nota_bitacora="Resultado manual pendiente de validación humana.",
    )
    for consulta in verificacion.consultas_proveedor.all():
        cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.COMPLETADA)
    return verificacion


@transaction.atomic
def validar_verificacion(*, verificacion, revisor, decision: str, comentario="", vigente_hasta=None):
    # Cuatro ojos (ANT-013): decide un revisor y nunca quien cargó la evidencia. Siempre activo.
    if not permisos.puede_revisar(revisor, verificacion.organizacion_id):
        raise DomainError("Solo un revisor de antecedentes puede decidir sobre una verificación.")
    if verificacion.cargado_por_id == revisor.pk:
        raise DomainError("Quien cargó el documento no puede validarlo.")
    decisiones_validas = {EstadoVerificacion.VALIDADA, EstadoVerificacion.LISTA, EstadoVerificacion.CANCELADA}
    if decision not in decisiones_validas:
        raise DomainError(f"Decisión inválida: {decision}")
    if verificacion.estado not in maquina_estados.ESTADOS_DECISION:
        raise DomainError(f"La verificación en estado '{verificacion.estado}' no espera una decisión de revisión.")
    if decision == EstadoVerificacion.VALIDADA and not vigente_hasta:
        raise DomainError("Toda validación debe indicar hasta cuándo es vigente el resultado.")
    if decision != EstadoVerificacion.VALIDADA and not comentario.strip():
        raise DomainError("Devolver o anular una verificación requiere un comentario con el motivo.")

    campos = {"validado_por": revisor}
    if comentario:
        campos["comentario"] = comentario
    if decision == EstadoVerificacion.VALIDADA:
        campos["vigente_hasta"] = vigente_hasta

    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=decision,
        actor=revisor,
        nota_bitacora=f"Decisión de revisión: {decision}.",
        motivo=comentario,
        **campos,
    )
    return verificacion


def marcar_vencidas(actor=None) -> int:
    """Job en bloque para Celery-beat (Fase 9); por ahora, función normal."""
    hoy = timezone.now().date()
    total = 0
    for verificacion in VerificacionAntecedentes.objects.filter(
        estado=EstadoVerificacion.VALIDADA, vigente_hasta__lt=hoy
    ):
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.VENCIDA,
            actor=actor,
            nota_bitacora="Vencimiento detectado automáticamente.",
        )
        total += 1
    return total
