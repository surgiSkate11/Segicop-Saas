import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta
from datetime import time as hora_del_dia

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.antecedentes import certificado, maquina_estados, pdf, permisos
from apps.antecedentes.conectores.asistido import ConectorAsistido
from apps.antecedentes.conectores.base import ClaseRespuesta
from apps.antecedentes.conectores.factory import obtener_conector
from apps.antecedentes.conectores.manual import ConectorManual
from apps.antecedentes.models import (
    ESTADOS_CERRADOS,
    AutorizacionDatos,
    ConsultaProveedor,
    DocumentoAntecedentes,
    EstadoConsulta,
    EstadoVerificacion,
    FinalidadConsulta,
    IntentoConsulta,
    MedioAutorizacion,
    ModoConector,
    ProveedorAntecedentes,
    ResultadoAntecedentes,
    SaludFuente,
    VerificacionAntecedentes,
)
from apps.antecedentes.pdf import DocumentoInvalido
from apps.core.events import emitir_evento
from apps.core.exceptions import DomainError
from apps.identidad.models import Rol, RolAsignado
from apps.personas.models import Persona

# Códigos de error del módulo (tabla "Errores mínimos" de requerimientos).
ANT_CAPTCHA_REQUIRED = "ANT_CAPTCHA_REQUIRED"
ANT_SOURCE_UNAVAILABLE = "ANT_SOURCE_UNAVAILABLE"
ANT_RATE_LIMITED = "ANT_RATE_LIMITED"
ANT_DOCUMENT_INVALID = pdf.ANT_DOCUMENT_INVALID
ANT_IDENTITY_MISMATCH = "ANT_IDENTITY_MISMATCH"
ANT_RESULT_AMBIGUOUS = "ANT_RESULT_AMBIGUOUS"

CUARENTENA = "CUARENTENA"
_SUFIJO_CUARENTENA = " Se conserva el original en cuarentena; carga otro archivo o solicita revisión."

_esperar = time.sleep  # punto de inyección para las pruebas


def _config(nombre: str, por_defecto):
    return getattr(settings, nombre, por_defecto)


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
    """
    Desde LISTA: el conector avanza el caso y, si es asistido, se comprueba el portal.
    Síncrono por defecto (respuesta inmediata, sin depender de un worker); con
    ANTECEDENTES_CONSULTA_ASINCRONA la consulta se encola en Celery al confirmar.
    """
    obtener_conector().iniciar(verificacion, actor=actor)
    if verificacion.modo_conector != ModoConector.ASISTIDO:
        return
    consulta = verificacion.consultas_proveedor.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)
    if _config("ANTECEDENTES_CONSULTA_ASINCRONA", False):
        transaction.on_commit(lambda: _encolar_consulta(consulta.pk))
        return
    ejecutar_consulta_asistida(consulta=consulta, actor=actor)
    verificacion.refresh_from_db()


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
    from apps.antecedentes.tasks import ejecutar_consulta_asistida as tarea

    tarea.delay(str(consulta_id))


# --- Consulta asistida: reintentos limitados y disyuntor por fuente (S2-S4) ---


def _hora(momento) -> str:
    return timezone.localtime(momento).strftime("%H:%M")


def mensaje_reintento(momento) -> str:
    return f"Vuelve a intentarlo después de las {_hora(momento)} o carga el certificado manualmente."


def _salud(proveedor: str) -> SaludFuente:
    return SaludFuente.objects.get_or_create(proveedor=proveedor)[0]


def fuente_en_pausa_hasta(proveedor: str):
    """Hora hasta la que el disyuntor impide consultar la fuente, o None si está cerrado."""
    abierto_hasta = SaludFuente.objects.filter(proveedor=proveedor).values_list("abierto_hasta", flat=True).first()
    return abierto_hasta if abierto_hasta and abierto_hasta > timezone.now() else None


def _pausa() -> timedelta:
    return timedelta(seconds=_config("ANTECEDENTES_CIRCUITO_PAUSA", 600))


def _fuente_respondio(salud: SaludFuente) -> None:
    salud.fallos_consecutivos = 0
    salud.abierto_hasta = None
    salud.ultimo_codigo = ""
    salud.save()


def _fuente_fallo(salud: SaludFuente, sondeo) -> None:
    salud.fallos_consecutivos += 1
    salud.ultimo_codigo = ANT_SOURCE_UNAVAILABLE
    salud.ultima_evidencia = sondeo.evidencia[:255]
    if salud.fallos_consecutivos >= _config("ANTECEDENTES_CIRCUITO_UMBRAL", 3):
        salud.abierto_hasta = timezone.now() + _pausa()
    salud.save()


def _tomar_consulta(consulta: ConsultaProveedor) -> bool:
    """Reclama la consulta de forma atómica: dos ejecuciones simultáneas nunca consultan dos veces."""
    return bool(
        ConsultaProveedor.objects.filter(
            pk=consulta.pk,
            estado__in=(EstadoConsulta.PENDIENTE, EstadoConsulta.REINTENTO_PROGRAMADO),
            verificacion__estado=EstadoVerificacion.EN_CONSULTA,
        ).update(estado=EstadoConsulta.EN_CURSO, updated_at=timezone.now())
    )


def _pedir_intervencion(consulta, verificacion, actor, sondeo, nota: str) -> None:
    cambiar_estado_consulta(
        consulta=consulta,
        nuevo_estado=EstadoConsulta.CAPTCHA_REQUERIDO,
        registrar_intento=True,
        codigo_error=ANT_CAPTCHA_REQUIRED,
        detalle_error=sondeo.evidencia,
        latencia_ms=sondeo.latencia_ms,
    )
    maquina_estados.transicionar(
        verificacion=verificacion, nuevo_estado=EstadoVerificacion.CAPTCHA_REQUERIDO, actor=actor, nota_bitacora=nota
    )


def _fallar_consulta(consulta, verificacion, actor, *, codigo: str, motivo: str, hasta, sondeo=None) -> None:
    cambiar_estado_consulta(
        consulta=consulta,
        nuevo_estado=EstadoConsulta.FALLIDA,
        registrar_intento=True,
        codigo_error=codigo,
        detalle_error=sondeo.evidencia if sondeo else motivo,
        latencia_ms=sondeo.latencia_ms if sondeo else None,
        proximo_intento_at=hasta,
    )
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.FALLIDA,
        actor=actor,
        nota_bitacora=(
            f"{motivo} {mensaje_reintento(hasta)} "
            "No obtener un resultado no equivale a ausencia de antecedentes."
        ),
    )


def ejecutar_consulta_asistida(*, consulta: ConsultaProveedor, actor=None) -> bool:
    """
    Comprueba el portal oficial y deja el caso en CAPTCHA_REQUERIDO para la persona operadora.
    Devuelve False si no había nada que ejecutar (idempotente).

    Tiempo agotado o 5xx: máximo dos reintentos con espera incremental y luego FALLIDA.
    4xx: no se reintenta. 429 o disyuntor abierto: FALLIDA sin consultar, con la hora para reintentar.
    La carga manual sigue disponible en todos los casos.
    """
    if not _tomar_consulta(consulta):
        return False
    consulta.refresh_from_db()
    verificacion = consulta.verificacion
    conector = ConectorAsistido()
    salud = _salud(consulta.proveedor)
    esperas = tuple(_config("ANTECEDENTES_ESPERAS_REINTENTO", (2, 5)))
    reintentos = 0

    while True:
        ahora = timezone.now()
        if salud.abierto_hasta and salud.abierto_hasta > ahora:
            _fallar_consulta(
                consulta, verificacion, actor,
                codigo=salud.ultimo_codigo or ANT_SOURCE_UNAVAILABLE,
                motivo="El portal oficial está en pausa por fallos recientes; no se consultó.",
                hasta=salud.abierto_hasta,
            )
            return True

        sondeo = conector.sondear()

        if sondeo.clase == ClaseRespuesta.DISPONIBLE:
            _fuente_respondio(salud)
            _pedir_intervencion(
                consulta, verificacion, actor, sondeo,
                "El portal oficial responde y exige resolver un CAPTCHA: ábrelo, descarga el certificado y cárgalo.",
            )
            return True

        if sondeo.clase == ClaseRespuesta.DENEGADA:
            _pedir_intervencion(
                consulta, verificacion, actor, sondeo,
                f"El portal rechazó la comprobación automática ({sondeo.evidencia}); no se reintenta. "
                "Ábrelo en tu navegador o carga el certificado manualmente.",
            )
            return True

        if sondeo.clase == ClaseRespuesta.LIMITADA:
            hasta = ahora + max(_pausa(), timedelta(seconds=sondeo.reintentar_tras or 0))
            salud.abierto_hasta = hasta
            salud.ultimo_codigo = ANT_RATE_LIMITED
            salud.ultima_evidencia = sondeo.evidencia[:255]
            salud.save()
            _fallar_consulta(
                consulta, verificacion, actor, codigo=ANT_RATE_LIMITED,
                motivo="El portal oficial limitó las consultas.", hasta=hasta, sondeo=sondeo,
            )
            return True

        # Transitoria: tiempo agotado, error de red o 5xx.
        _fuente_fallo(salud, sondeo)
        if reintentos >= len(esperas) or salud.abierto_hasta:
            _fallar_consulta(
                consulta, verificacion, actor, codigo=ANT_SOURCE_UNAVAILABLE,
                motivo=f"El portal oficial no respondió tras {reintentos + 1} intentos.",
                hasta=salud.abierto_hasta or ahora + _pausa(), sondeo=sondeo,
            )
            return True

        espera = esperas[reintentos]
        reintentos += 1
        cambiar_estado_consulta(
            consulta=consulta,
            nuevo_estado=EstadoConsulta.REINTENTO_PROGRAMADO,
            registrar_intento=True,
            codigo_error=ANT_SOURCE_UNAVAILABLE,
            detalle_error=sondeo.evidencia,
            latencia_ms=sondeo.latencia_ms,
            proximo_intento_at=ahora + timedelta(seconds=espera),
        )
        _esperar(espera)
        # Durante la espera el caso pudo cargarse a mano o anularse: entonces no se insiste.
        verificacion.refresh_from_db()
        if verificacion.estado != EstadoVerificacion.EN_CONSULTA or not _tomar_consulta(consulta):
            return True
        consulta.refresh_from_db()


def reintentar_desde(verificacion: VerificacionAntecedentes):
    """Para una fallida: None si ya se puede reintentar; si no, el momento a partir del cual se podrá."""
    if verificacion.estado != EstadoVerificacion.FALLIDA:
        return None
    consulta = verificacion.consultas_proveedor.filter(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR).first()
    if consulta is None:
        return None
    ahora = timezone.now()
    candados = [
        momento
        for momento in (consulta.proximo_intento_at, fuente_en_pausa_hasta(consulta.proveedor))
        if momento and momento > ahora
    ]
    return max(candados) if candados else None


def reintentar_consulta(*, verificacion: VerificacionAntecedentes, actor) -> VerificacionAntecedentes:
    """La persona operadora pide un nuevo intento de una verificación fallida, respetando la hora indicada."""
    if verificacion.estado != EstadoVerificacion.FALLIDA:
        raise DomainError("Solo se puede reintentar una verificación fallida.")
    consulta = verificacion.consultas_proveedor.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)
    espera = reintentar_desde(verificacion)
    if espera:
        raise DomainError(f"Podrás reintentar después de las {_hora(espera)}; mientras tanto puedes cargar el certificado manualmente.")

    try:
        with transaction.atomic():
            maquina_estados.transicionar(
                verificacion=verificacion,
                nuevo_estado=EstadoVerificacion.LISTA,
                actor=actor,
                nota_bitacora="Nuevo intento solicitado.",
                modo_conector=_modo_del_conector(obtener_conector()),
            )
    except IntegrityError:
        verificacion.refresh_from_db()
        raise DomainError("La persona ya tiene otra solicitud en curso para esta finalidad; continúa con esa.")
    cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.PENDIENTE)
    _arrancar_conector(verificacion, actor)
    return verificacion


def continuar_consulta(*, verificacion: VerificacionAntecedentes, actor) -> str:
    """S4: la persona operadora abre el portal oficial en su navegador para resolver el CAPTCHA."""
    if verificacion.estado == EstadoVerificacion.CAPTCHA_REQUERIDO:
        consulta = verificacion.consultas_proveedor.get(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR)
        with transaction.atomic():
            maquina_estados.transicionar(
                verificacion=verificacion,
                nuevo_estado=EstadoVerificacion.EN_CONSULTA,
                actor=actor,
                nota_bitacora="Se abrió el portal oficial para resolver la intervención humana.",
            )
            cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.EN_CURSO)
    elif verificacion.estado != EstadoVerificacion.EN_CONSULTA:
        raise DomainError("La consulta asistida no está disponible en este estado.")
    return ConectorAsistido.url_portal


@transaction.atomic
def cambiar_estado_consulta(
    *,
    consulta: ConsultaProveedor,
    nuevo_estado: str,
    registrar_intento: bool = False,
    detalle_error: str = "",
    codigo_error: str = "",
    proximo_intento_at=None,
    latencia_ms=None,
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
            latencia_ms=latencia_ms,
            detalle=detalle_error,
        )
    return consulta


# --- Certificado PDF: validación, custodia, lectura e identidad (S7, ANT-006..010) ---


def _motivo_de_cuarentena(documento: DocumentoAntecedentes) -> str:
    return documento.detalle_validacion.removesuffix(_SUFIJO_CUARENTENA)


def _guardar_documento(consulta, archivo, contenido: bytes, sha256: str) -> DocumentoAntecedentes:
    """Conserva los bytes originales en el almacenamiento privado, con un nombre propio (nunca el del usuario)."""
    documento = DocumentoAntecedentes(
        consulta=consulta,
        nombre_original=os.path.basename(str(archivo.name))[:255],
        mime_type="application/pdf",
        tamano_bytes=len(contenido),
        sha256=sha256,
        estado_validacion="PENDIENTE",
    )
    documento.archivo.save(f"antecedente-{uuid.uuid4().hex}.pdf", ContentFile(contenido), save=False)
    documento.save()
    return documento


def _fecha_resultado(fecha_emision):
    if not fecha_emision:
        return timezone.now()
    return timezone.make_aware(datetime.combine(fecha_emision, hora_del_dia.min))


def _codigo_de_identidad(datos: certificado.CertificadoExtraido, persona) -> str:
    """ANT-008: cédula idéntica y nombres equivalentes; cualquier diferencia exige revisión."""
    if datos.identificacion and datos.identificacion != persona.numero_documento.strip():
        return ANT_IDENTITY_MISMATCH
    if datos.nombres and not certificado.nombres_equivalentes(datos.nombres, f"{persona.apellidos} {persona.nombres}"):
        return ANT_IDENTITY_MISMATCH
    if not (datos.identificacion and datos.nombres and datos.resultado):
        return ANT_RESULT_AMBIGUOUS
    return ""


def _analizar_certificado(*, verificacion, consulta, documento, texto: str, contenido: bytes, actor) -> None:
    """PDF_RECIBIDO -> (DATOS_COINCIDENTES -> AUTENTICIDAD_PENDIENTE) o REQUIERE_REVISION."""
    maquina_estados.transicionar(
        verificacion=verificacion,
        nuevo_estado=EstadoVerificacion.PDF_RECIBIDO,
        actor=actor,
        nota_bitacora="Certificado PDF recibido.",
        cargado_por=actor,
        proveedor=ProveedorAntecedentes(consulta.proveedor).label,
    )
    cambiar_estado_consulta(consulta=consulta, nuevo_estado=EstadoConsulta.COMPLETADA)

    datos = certificado.analizar_texto(texto)
    codigo = _codigo_de_identidad(datos, verificacion.persona)
    firma = pdf.tiene_firma(contenido)
    documento.identificacion_extraida = datos.identificacion[:50]
    documento.nombre_extraido = datos.nombres[:255]
    documento.numero_certificado = datos.numero_certificado[:100]
    documento.resultado_extraido = datos.resultado or ""
    documento.fecha_emision = datos.fecha_emision
    documento.vigente_hasta_extraida = datos.vigente_hasta
    documento.firma_detectada = firma
    documento.codigo_error = codigo
    firma_texto = (
        f"Marca «Documento firmado electrónicamente» en el texto: {'sí' if datos.marca_firma else 'no'}. "
        f"Diccionario de firma en el PDF: {'sí' if firma else 'no'}. "
        "La validación criptográfica de la firma está pendiente."
    )

    if not codigo:
        documento.estado_validacion = "DATOS_COINCIDENTES"
        documento.detalle_validacion = f"La identidad del certificado coincide con la persona consultada. {firma_texto}"
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.DATOS_COINCIDENTES,
            actor=actor,
            nota_bitacora="La identidad del certificado coincide con la persona consultada.",
            resultado=datos.resultado,
            numero_certificado=datos.numero_certificado[:100],
            fecha_resultado=_fecha_resultado(datos.fecha_emision),
        )
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.AUTENTICIDAD_PENDIENTE,
            actor=actor,
            nota_bitacora="Pendiente de verificar la autenticidad del certificado.",
        )
        return

    documento.estado_validacion = "REQUIERE_REVISION"
    if codigo == ANT_IDENTITY_MISMATCH:
        documento.detalle_validacion = (
            "La identidad del certificado no coincide con la de la persona consultada; aceptación bloqueada. "
            f"Se conserva el original y el caso se escala a revisión. {firma_texto}"
        )
        nota = f"El certificado no coincide con la identidad esperada ({codigo}); requiere revisión."
    else:
        campos = (("identificación", datos.identificacion), ("nombres", datos.nombres), ("resultado", datos.resultado))
        faltantes = ", ".join(nombre for nombre, valor in campos if not valor)
        documento.detalle_validacion = (
            f"No se pudo reconocer con certeza en el certificado: {faltantes}. "
            f"Requiere revisión humana. {firma_texto}"
        )
        nota = f"No se reconoció el resultado o la identidad del certificado ({codigo}); requiere revisión."
    maquina_estados.transicionar(
        verificacion=verificacion, nuevo_estado=EstadoVerificacion.REQUIERE_REVISION, actor=actor, nota_bitacora=nota
    )


def cargar_certificado(*, verificacion, archivo, actor) -> tuple[DocumentoAntecedentes, bool]:
    """
    Única entrada de un certificado PDF (S7). Devuelve (documento, creado).

    Siempre conserva el original con su SHA-256 y audita la recepción. El mismo archivo (mismo SHA-256)
    para la consulta devuelve el documento existente sin duplicar nada (doble clic o reintento). Un PDF
    ilegible, cifrado, dañado o con contenido activo queda en cuarentena, el caso no cambia de estado y se
    lanza DocumentoInvalido. Uno válido pasa a PDF_RECIBIDO y se lee: si la identidad coincide y se
    reconoce el resultado queda en AUTENTICIDAD_PENDIENTE; si no, en REQUIERE_REVISION.
    """
    error = pdf.validar_entrada(archivo)
    if error:
        raise DocumentoInvalido(error)
    archivo.seek(0)
    contenido = archivo.read()
    archivo.seek(0)
    sha256 = hashlib.sha256(contenido).hexdigest()
    consulta = verificacion.consultas_proveedor.filter(proveedor=ProveedorAntecedentes.MINISTERIO_INTERIOR).first()
    if consulta is None:
        raise DomainError("La verificación no tiene una consulta al Ministerio del Interior.")

    motivo = None
    with transaction.atomic():
        bloqueada = (
            VerificacionAntecedentes.objects.select_for_update().select_related("persona").get(pk=verificacion.pk)
        )
        existente = DocumentoAntecedentes.objects.filter(consulta=consulta, sha256=sha256).order_by("created_at").first()
        if existente:
            if existente.estado_validacion == CUARENTENA:
                raise DocumentoInvalido(_motivo_de_cuarentena(existente), existente)
            return existente, False
        if bloqueada.estado not in maquina_estados.ESTADOS_CARGA_MANUAL:
            raise DomainError(f"No se puede cargar un certificado con la verificación en estado '{bloqueada.estado}'.")

        motivo = pdf.validar_estructura(contenido)
        texto = ""
        if not motivo:
            texto, motivo = pdf.extraer_texto(contenido)
        documento = _guardar_documento(consulta, archivo, contenido, sha256)
        if motivo:
            documento.estado_validacion = CUARENTENA
            documento.codigo_error = ANT_DOCUMENT_INVALID
            documento.detalle_validacion = motivo + _SUFIJO_CUARENTENA
        else:
            _analizar_certificado(
                verificacion=bloqueada, consulta=consulta, documento=documento, texto=texto, contenido=contenido,
                actor=actor,
            )
        documento.save()
        emitir_evento(
            actor=actor,
            accion="DOCUMENTO_ANTECEDENTES_RECIBIDO",
            recurso=documento,
            organizacion=bloqueada.organizacion,
            motivo="Certificado recibido y conservado con su huella SHA-256.",
            metadata={
                "sha256": sha256,
                "tamano_bytes": len(contenido),
                "estado_validacion": documento.estado_validacion,
                "codigo_error": documento.codigo_error,
            },
        )
    # La cuarentena ya está confirmada cuando se avisa del rechazo.
    if motivo:
        raise DocumentoInvalido(motivo, documento)
    verificacion.refresh_from_db()
    return documento, True


def registrar_descarga(*, usuario, documento: DocumentoAntecedentes) -> None:
    """ANT-011: toda lectura del original queda en la auditoría."""
    emitir_evento(
        actor=usuario,
        accion="DESCARGAR_DOCUMENTO_ANTECEDENTES",
        recurso=documento,
        organizacion=documento.consulta.verificacion.organizacion,
        metadata={"sha256": documento.sha256},
    )


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


def _certificado_de_otra_identidad(verificacion) -> bool:
    """ANT-008: "Bloquear aceptación" cuando el último certificado cargado no es de la persona."""
    ultimo = DocumentoAntecedentes.objects.filter(consulta__verificacion=verificacion).order_by("-created_at").first()
    return bool(ultimo and ultimo.codigo_error == ANT_IDENTITY_MISMATCH)


def vigencia_del_certificado(verificacion):
    """'Válido hasta' extraído del último certificado cargado que no está en cuarentena, o None."""
    return (
        DocumentoAntecedentes.objects.filter(consulta__verificacion=verificacion, vigente_hasta_extraida__isnull=False)
        .exclude(estado_validacion="CUARENTENA")
        .order_by("-created_at")
        .values_list("vigente_hasta_extraida", flat=True)
        .first()
    )


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
    if decision == EstadoVerificacion.VALIDADA:
        # S9: la vigencia sale del certificado; el revisor puede acortarla, nunca extenderla.
        del_certificado = vigencia_del_certificado(verificacion)
        if vigente_hasta and del_certificado and vigente_hasta > del_certificado:
            raise DomainError(
                f"La vigencia ({vigente_hasta:%d/%m/%Y}) no puede superar la del certificado ({del_certificado:%d/%m/%Y})."
            )
        vigente_hasta = vigente_hasta or del_certificado
        if not vigente_hasta:
            raise DomainError("Toda validación debe indicar hasta cuándo es vigente el resultado.")
    if decision == EstadoVerificacion.VALIDADA and _certificado_de_otra_identidad(verificacion):
        raise DomainError(
            "El certificado no coincide con la identidad de la persona: no se puede validar. "
            "Devuelve el caso para una nueva carga o anúlalo."
        )
    if decision != EstadoVerificacion.VALIDADA and not comentario.strip():
        raise DomainError("Devolver o anular una verificación requiere un comentario con el motivo.")

    campos = {"validado_por": revisor}
    if comentario:
        campos["comentario"] = comentario
    if decision == EstadoVerificacion.VALIDADA:
        campos["vigente_hasta"] = vigente_hasta

    if verificacion.estado == EstadoVerificacion.AUTENTICIDAD_PENDIENTE and decision == EstadoVerificacion.LISTA:
        # El diagrama solo permite devolver desde REQUIERE_REVISION: se pasa por ahí y queda en la bitácora.
        maquina_estados.transicionar(
            verificacion=verificacion,
            nuevo_estado=EstadoVerificacion.REQUIERE_REVISION,
            actor=revisor,
            nota_bitacora="El revisor devuelve el caso para una nueva carga.",
        )

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


def programar_renovaciones(actor=None) -> int:
    """
    ANT-012: abre una nueva consulta (finalidad RENOVACION) para quien sigue con una asignación
    activa y cuya última verificación, validada o vencida, vence dentro del plazo configurado.
    Si la última verificación es otra (una renovación en curso o anulada) no se insiste.
    """
    limite = timezone.now().date() + timedelta(days=_config("ANTECEDENTES_DIAS_RENOVACION", 15))
    ultima = VerificacionAntecedentes.objects.filter(persona=OuterRef("pk")).order_by("-created_at")
    candidatas = (
        Persona.objects.filter(asignaciones__activa=True)
        .distinct()
        .annotate(
            ultimo_estado=Subquery(ultima.values("estado")[:1]),
            ultima_vigencia=Subquery(ultima.values("vigente_hasta")[:1]),
        )
        .filter(
            ultimo_estado__in=(EstadoVerificacion.VALIDADA, EstadoVerificacion.VENCIDA),
            ultima_vigencia__lte=limite,
        )
    )
    total = 0
    for persona in candidatas:
        _, creada = iniciar_verificacion(persona=persona, actor=actor, finalidad=FinalidadConsulta.RENOVACION)
        total += int(creada)
    return total
