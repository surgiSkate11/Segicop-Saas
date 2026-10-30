# Constitución del proyecto SIGECOP

Este documento es la fuente única de verdad sobre cómo está construido SIGECOP y por qué. Cualquier decisión nueva debe ser compatible con esto, o debe modificar este documento explícitamente (nunca contradecirlo en silencio).

---

## 1. Qué es SIGECOP

Sistema de gestión de contratistas y permisos: controla el acceso físico y documental de empresas contratistas y su personal a instalaciones de un cliente. El núcleo del problema **no es guardar archivos** — es calcular en todo momento si una persona/empresa está habilitada para ingresar o trabajar, a partir de tres decisiones que se toman por separado y nunca se fusionan.

---

## 2. Principios no negociables (RN)

Estos principios están implementados en código, no son solo intención. Romperlos es un bug de diseño, no un detalle de implementación.

1. **Tres decisiones separadas, nunca fusionadas** (RN-007 / ANT-014): el resultado de antecedentes penales, la autenticidad/cumplimiento documental, y la habilitación de ingreso/trabajo son tres cosas que se calculan y guardan por separado. `apps.accesos.services.calcular_habilitacion()` es la ÚNICA función de todo el sistema autorizada a leer las tres a la vez — y es de **solo lectura**, nunca escribe en `Persona`, `EmpresaContratista` ni `VerificacionAntecedentes`.
2. **Un fallo técnico nunca es un resultado favorable**: si un conector de antecedentes falla, el caso queda en `FALLIDA` (con la hora a partir de la cual reintentar y la carga manual siempre disponible), jamás se interpreta como "sin antecedentes". Un CAPTCHA o un rechazo del portal dejan el caso en `CAPTCHA_REQUERIDO` para una persona: nunca se evaden controles antibots. Ver `apps/antecedentes/services.py::ejecutar_consulta_asistida` y `conectores/ministerio.py`.
3. **Nada se sobrescribe** (RN-001): documentos (`DocumentoVersion`), verificaciones de antecedentes, y decisiones de revisión son inmutables. Renovar/reintentar = crear una fila nueva, nunca editar una existente.
4. **Los estados cambian solo a través de servicios de dominio**, nunca por edición directa de un modelo desde una vista o el admin. Cada app tiene un `services.py` que es el único punto de mutación. Donde hay una máquina de estados real (antecedentes, trabajos), hay además una función `transicionar()`/tabla de transiciones que es la única autorizada a cambiar el campo `estado`.
5. **Auditoría de solo-anexado**: toda mutación relevante llama a `apps.core.events.emitir_evento(...)`. Los modelos de bitácora (`EjecucionConector`, `Revision`) tienen `has_change_permission` y `has_delete_permission` en `False` en el admin. Las exportaciones Excel registran un `ExportacionAuditoria` (filtros, filas, SHA-256) y el evento `EXPORTAR_<REPORTE>` (§11.5). Los resúmenes de alertas por correo se auditan con `PROGRAMAR_RESUMEN_ALERTAS` y `ENVIAR_RESUMEN_ALERTAS` (§11.2).
6. **Multi-tenant por organización**: casi todo modelo de negocio hereda de `OrganizacionScopedModel` (tiene FK a `Organizacion`). El filtrado por organización se hace **explícito** en cada vista, nunca de forma mágica/implícita — así un bug de seguridad se detecta en pruebas, no falla en silencio. Los helpers son `identidad.permisos.organizaciones_con_permiso(usuario, accion)` y `RequierePermisoMixin.organizaciones_permitidas()`.
   *Cambio respecto a la versión anterior de este documento:* `_organizaciones_del_usuario` ya no existe. El filtro depende ahora de la **acción** pedida (no basta con tener cualquier rol activo), porque la matriz de §3 decide qué puede hacer cada rol. El principio no cambia; cambia el nombre del helper y su alcance.
7. **Mínimo privilegio**: por ejemplo, garita nunca ve antecedentes ni expedientes, solo el resultado de escanear un código (`apps/accesos/views.py::GaritaView`). En antecedentes solo el rol `revisor_antecedentes` ve resultados y certificados (descarga auditada, almacenamiento privado fuera de `/media/`), quien cargó un documento no puede validarlo (cuatro ojos), toda lectura queda auditada, y el motivo de habilitación es uno solo para no revelar el resultado (`apps/antecedentes/permisos.py`, `apps/accesos/services.py::MOTIVO_ANTECEDENTES`). Las vistas internas se rigen por la matriz de §3 (denegar por defecto, HTTP 403). El expediente y los reportes nunca muestran el resultado de antecedentes (§11.5 y §11.6).
8. **El conector de antecedentes es intercambiable** (ANT-003): `apps/antecedentes/conectores/` es un patrón puerto/adaptador. El dominio nunca habla directo con un proveedor externo.

---

## 3. Arquitectura: monolito modular

Un solo proyecto Django (`sigecop`), con apps de dominio independientes bajo `apps/`. Cada app puede depender de apps "anteriores" en esta lista, nunca de las "posteriores" (evita dependencias circulares):

```
core          → transversal, no depende de nada (BaseModel, OrganizacionScopedModel, 
                DomainError, TransicionEstadoInvalida, emitir_evento, EstiloFormMixin, 
                render_modal/exito_htmx)
identidad     → Usuario, Organizacion, Instalacion, Rol, RolAsignado, AlcanceAsignado
empresas      → EmpresaContratista, Contrato
personas      → Persona, Asignacion (persona↔empresa)
cumplimiento  → CatalogoRequisito, RequisitoAplicado, DocumentoVersion, Revision
                + el motor que recalcula Persona.estado / EmpresaContratista.estado
antecedentes  → VerificacionAntecedentes (14 estados), EjecucionConector, conectores/
accesos       → PermisoIngreso, EventoAcceso — lee cumplimiento + antecedentes
trabajos      → PermisoTrabajo — reutiliza accesos.calcular_habilitacion()
reportes      → sin modelos propios de negocio (la exportación audita en core); lee las apps anteriores.
                Es la última de la cadena: ninguna app la importa
```

Cada app sigue la misma estructura interna:
```
apps/<app>/
  models.py       # solo estructura de datos + constraints
  services.py     # ÚNICO lugar que muta estado; valida reglas de negocio
  forms.py        # formularios Django (EstiloFormMixin aplica clase sc-input)
  views.py        # ListView normal + vistas modal (ver sección 6)
  urls.py         # app_name = "<app>" obligatorio
  admin.py        # registro en el admin de Django
  migrations/
apps/<app>/maquina_estados.py   # solo si la app tiene una máquina de estados formal
apps/<app>/conectores/          # solo si la app usa el patrón puerto/adaptador
templates/<app>/
```

---

### Matriz de permisos (`apps/identidad/permisos.py`)

Una sola tabla en código (`PERMISOS`: acción → roles). Es la matriz de `odd/preguntas-director.md` (P5), que el director aún debe confirmar. Cambiar una fila es cambiar una línea de `PERMISOS` y esta tabla.

| Acción | Roles que la tienen |
|---|---|
| `ver_registros` | admin_cliente, gestor_contratista, revisor_documental, responsable_area, operador_antecedentes, revisor_antecedentes |
| `editar_registros` | admin_cliente, gestor_contratista |
| `enviar_credenciales` | admin_cliente, gestor_contratista |
| `configurar_catalogo` | admin_cliente |
| `asignar_requisitos` | admin_cliente, gestor_contratista |
| `subir_documentos` | admin_cliente, gestor_contratista |
| `revisar_documentos` | admin_cliente, revisor_documental |
| `solicitar_ingreso` | admin_cliente, gestor_contratista, responsable_area |
| `aprobar_ingreso` | admin_cliente, responsable_area *(sin uso, ver nota)* |
| `crear_trabajo` | admin_cliente, gestor_contratista, responsable_area |
| `gestionar_trabajo` | admin_cliente, responsable_area |
| `garita` | seguridad_garita |
| `ver_alertas` | admin_cliente, gestor_contratista, revisor_documental, responsable_area, operador_antecedentes, revisor_antecedentes |
| `exportar_reportes` | admin_cliente |
| `gestionar_usuarios` | admin_cliente |
| `recibir_resumen_alertas` | admin_cliente *(pendiente P21)* |

Reglas:
- **Denegar por defecto.** Un rol o una acción fuera de la matriz no da acceso. `ROLES_INTERNOS` (los roles que se pueden asignar a usuarios internos) es la unión de la matriz. Pedir una acción inexistente es un error de programación (`ValueError`), nunca un permiso.
- **403 real.** `RequierePermisoMixin` responde HTTP 403 antes de leer o escribir nada si el usuario no tiene la acción en ninguna de sus organizaciones. Dentro de la vista, cada consulta filtra por las organizaciones donde sí la tiene.
- **Menú cosmético.** `acciones_permitidas` (context processor) solo oculta entradas del menú. El control real es el 403.
- **Roles de portal y de plataforma** (`contratista`, `admin_plataforma`) no reciben ninguna acción interna.
- **Antecedentes y portal no cambian.** `apps/antecedentes/permisos.py` y `AccesoPortalMiddleware` conservan sus reglas.
- **Brecha conocida.** `gestor_contratista` actúa sobre todas las empresas de su organización, no solo sobre las suyas. Hasta resolver T3 (pregunta P2) **AC-002 no se cumple**.
- **Nota sobre `aprobar_ingreso`.** Ninguna vista la exige: `apps/accesos/services.py` aprueba solo si la persona está habilitada (P9 pendiente).

---

## 4. Estados de las entidades principales

| Entidad | Estados | Quién los cambia |
|---|---|---|
| `EmpresaContratista.estado`, `Persona.estado` | `EstadoCumplimiento` (core.choices): BORRADOR, INCOMPLETO, EN_REVISION, OBSERVADO, HABILITADO, HABILITADO_CON_CONDICIONES, SUSPENDIDO, VENCIDO, NO_HABILITADO | El motor de `apps.cumplimiento.services._recalcular()`, automático, nunca a mano |
| `RequisitoAplicado.estado` | PENDIENTE, EN_REVISION, OBSERVADO, APROBADO, RECHAZADO, VENCIDO | `apps.cumplimiento.services` (subir/revisar documento) |
| `VerificacionAntecedentes.estado` | 14 estados, ver `apps/antecedentes/maquina_estados.py::TRANSICIONES` | Solo `maquina_estados.transicionar()` |
| `PermisoIngreso.estado` | SOLICITADO, APROBADO, RECHAZADO, SUSPENDIDO, VENCIDO | `apps.accesos.services` |
| `PermisoTrabajo.estado` | SOLICITADO, RECHAZADO, ACTIVO, SUSPENDIDO, CERRADO | `apps.trabajos.services::TRANSICIONES` |

---

## 5. Stack técnico y decisiones tomadas (con su porqué)

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| Django Templates + HTMX, NO una SPA separada (React/Vue) | SPA con DRF | El cliente pidió mantener la familiaridad del sistema anterior (COSED); HTMX da interactividad sin la complejidad de una API+frontend separados |
| Tailwind v4 vía npm, compilado a `static/css/tailwind.css` | Tailwind CDN / CSS a mano | El usuario pidió Tailwind explícitamente. CDN no sirve para producción (sin purga, sin theme custom) |
| Clases de componente `sc-*` definidas con `@apply` en `static_src/input.css` | Reescribir cada template con utilidades sueltas | Permite tener Tailwind real por dentro sin reescribir las 8+ plantillas ya construidas |
| `django-fsm` descartado, máquinas de estado a mano (`TRANSICIONES` dict + función `transicionar()`) | Instalar django-fsm | Misma garantía de integridad, sin agregar una dependencia nueva sin revisar |
| UUID como PK en todo modelo de negocio, nunca autoincremental | IDs enteros | Requisito explícito: "las claves internas son UUID u otro identificador no predecible" |
| `settings/` como paquete (`base.py`/`dev.py`/`prod.py`) | `settings.py` único | Separar config de desarrollo/producción desde el principio |
| Modales vía HTMX (`hx-get`/`hx-post` a un `<div id="sc-modal">` en `base.html`) | Redirigir a páginas completas, o al admin de Django | Pedido explícito del cliente: el sistema de referencia usa popups de "Crear Item", no redirecciones |
| El admin de Django se mantiene para configuración de bajo nivel (catálogo de roles, etc.) | Construir pantallas propias para todo | Es una herramienta interna; no vale la pena una UI custom para tablas de configuración poco usadas |
| Matriz de permisos en un solo módulo (`PERMISOS`), denegar por defecto | Permisos sueltos en cada vista | Una decisión del director cambia una línea, y el 403 queda igual en todas las pantallas. Una vista nueva no puede olvidar el control |
| Job diario con `crontab` a hora fija (`SIGECOP_JOB_DIARIO_HORA`) | Intervalo de 86 400 s | Con intervalo, la hora dependía de cuándo arrancaba beat. El aviso debe salir siempre a la misma hora local |
| Resumen por correo idempotente por organización y día, auditado en dos pasos | Enviar y registrar después, sin marca previa | Evita duplicados si el job se repite, y un envío fallido nunca queda como enviado |
| Fecha de negocio con `timezone.localdate()` | `timezone.now().date()` (UTC) | Después de las 19:00 en Ecuador el sistema evaluaba el día siguiente (§9) |

### Versiones y entorno
- Python + Django **5.2** (LTS). Node.js **20+** requerido (Tailwind v4 lo exige; Node 18 falla).
- `requirements.txt` — dependencias Python. `package.json` — dependencias de build de CSS (Tailwind), nada de JS de aplicación (HTMX se carga por CDN vía `<script>`).
- Fuente tipográfica: **Nunito** (Google Fonts).
- Paleta de marca: naranja `--color-brand-500: #E8622C` y su escala 50–900, definida en `static_src/input.css` bajo `@theme`.

---

## 6. El patrón de modal HTMX (úsalo para cualquier "crear/editar" nuevo)

1. **`apps/core/forms.py`** → `EstiloFormMixin`: agrega `sc-input` a todos los widgets de texto/select/fecha automáticamente.
2. **`apps/core/modales.py`** *(en tu proyecto real puede llamarse `modals.py`, según cómo lo hayas creado — revisa cuál usaste y sé consistente)* → dos funciones:
   - `render_modal(request, *, form, titulo, accion_url, boton)`: renderiza `partials/_modal_formulario.html`.
   - `exito_htmx(request, *, mensaje, url)`: agrega el mensaje flash y responde con cabecera `HX-Redirect` (el navegador navega solo, sin JS custom).
3. **`templates/partials/_modal_formulario.html`**: la plantilla genérica del popup — cabecera naranja, campos del form, pie con Cancelar/Guardar.
4. **Vista**: una clase que hereda de `LoginRequiredMixin, View`, con:
   - `dispatch()` que **rechaza peticiones sin HTMX** (`if not request.htmx: return redirect(...)`) — un modal nunca debe ser accesible como página completa.
   - `get()` → `render_modal(...)` con el form vacío o precargado (`initial=...`).
   - `post()` → valida, llama al `services.py`, y si todo sale bien `return exito_htmx(...)`; si falla, `form.add_error(None, str(error))` y vuelve a renderizar el modal con el error.
5. **Disparador en el HTML**: nunca un `<a href="...">` a otra página — siempre:
   ```html
   <button type="button" class="sc-boton-secundario"
           hx-get="{% url 'app:vista' %}" hx-target="#sc-modal" hx-swap="innerHTML">
     Texto del botón
   </button>
   ```

**Excepción deliberada:** la Garita (`accesos:garita`) y la bandeja de revisión con sus formularios de "cargar resultado"/"revisar" siguen siendo páginas completas, no modales — son pantallas de trabajo continuo, no acciones puntuales de alta.

---

## 7. Convenciones de nombres

- Todo el dominio (modelos, campos, funciones, variables) está **en español**. El código de infraestructura Django (nombres de clases base, decoradores) queda en inglés porque es del framework.
- Prefijo `sc-` para toda clase CSS de componente (de "SIGECOP").
- `app_name = "<nombre>"` obligatorio en cada `urls.py` — Django lo exige para usar `namespace=` en el `include()`.
- Servicios siempre con argumentos *keyword-only* (`def crear_x(*, campo, actor, ...)`) — nunca posicionales, para que las llamadas sean auto-explicativas y no se puedan confundir el orden de los parámetros.
- Excepciones de dominio: `DomainError` (genérica) y `TransicionEstadoInvalida` (específica de máquinas de estado), ambas en `apps/core/exceptions.py`.

---

## 8. Roadmap — estado real

### ✅ Hecho y validado (Fases 1–8)
`core`, `identidad`, `empresas`, `personas`, `cumplimiento`, `antecedentes`, `accesos`, `trabajos` — dominio funcional completo, con máquina de estados, motor de recálculo, y combinador de habilitación, todo probado con peticiones HTTP reales (no solo revisado a ojo).

### ✅ Hecho en esta ronda: UI
Tema claro (naranja/blanco) inspirado en el sistema de referencia del cliente (COSED), Tailwind v4 vía npm, modales HTMX reemplazando redirecciones al admin para: Empresas (crear/editar), Personas (crear/editar/asignar a empresa), Cumplimiento (catálogo de requisitos: crear/asignar), Accesos (solicitar ingreso), Trabajos (crear permiso).

### ✅ Hecho: cuentas de acceso y portal (rama `feat/cuentas-acceso-portal`)
- Al crear una `EmpresaContratista` o `Persona` con correo de contacto se crea su cuenta de portal (`Usuario.tipo_cuenta` EMPRESA/PERSONA, `nombre_usuario` = RUC o número de documento) y se envía un **enlace de activación de un solo uso, válido 72 h** (`apps/identidad/tokens.py`). Nunca viaja una contraseña por correo. El mismo RUC/cédula en otra organización reutiliza la cuenta.
- Reutilización: `identidad.models.CuentaAccesoMixin` (titular de cuenta), `identidad.services.provisionar_cuenta/enviar_acceso/sincronizar_cuenta/activar_cuenta`, `identidad.mixins.EnviarAccesoModalView`, `identidad.forms.CorreoAccesoFormMixin`, `portal.mixins.PortalEmpresaMixin/PortalPersonaMixin`.
- Login: `identidad.backends.NombreUsuarioOCorreoBackend` (RUC/cédula o correo). `USERNAME_FIELD` sigue siendo `email`.
- **Aislamiento de portal (RN de seguridad):** `portal.middleware.AccesoPortalMiddleware` niega por defecto a las cuentas de portal todo lo que no sea `portal:*`, login/logout/activación y estáticos. Las vistas internas filtran solo por organización, así que este middleware NO se puede quitar.
- Correo: todo envío pasa por `core.correo.enviar_correo`; el servidor SMTP y el remitente se configuran en el admin (`core.ConfiguracionCorreo`, una sola activa, solo superusuarios).

### ✅ Hecho: cierre-sigecop (cambia la fase 9 anterior)
- **T1** matriz de permisos y denegación por defecto (§3), menú filtrado.
- **T2** usuarios internos: invitar, reenviar acceso, desactivar con motivo (§11.1).
- **T5, T5b, T5c** job diario a hora fija, avisos por umbral, resumen por correo auditado, fecha local (§11.2, §9). Antes, el job existía pero dependiente del arranque de beat, y evaluaba con UTC.
- **T6, T7** revisión en lote con Gestión y fecha de emisión; «Por hacer» en el panel (§11.4).
- **T8, T9** reportes: documentos, información de trabajadores, solicitudes de ingreso, histórico de accesos (§11.5).
- **T12** evidencia judicial asistida (§11.7). El informe de evidencia en reportes queda pendiente.
- **T13** campos de persona y expediente cronológico (§11.6, §11.8).

### 🔲 Pendiente — decisión del director (bloquea)
- **T3 (P2):** alcance del gestor por empresa con `AlcanceAsignado`. Mientras tanto AC-002 no se cumple.
- **T4 (P9):** aprobación humana de ingresos. Hoy `accesos` aprueba solo si la persona está habilitada.
- **T10 (P9):** solicitudes de cuadrilla. **T11 (P10):** visitas administrativas.
- **P21:** destinatarios de notificaciones y umbrales definitivos. Hoy solo `admin_cliente` recibe el resumen.
- **P15:** informe de antecedentes. Los reportes no muestran el resultado, y el informe sigue sin diseñar.

### 🔲 Pendiente — técnico
- Worker de Celery y beat en producción (Redis): **pendiente de confirmar** cómo se despliegan. El código está listo; no se ha probado en un entorno real.
- Cuatro ojos en documentos: no implementado (§11.4).
- `exportar_xlsx` deja `organizacion` vacía cuando el usuario tiene varias organizaciones (§11.5).
- `aprobar_ingreso` existe en la matriz sin uso (§3).
- `doc/` está en `.gitignore`: esta constitución y la guía de prueba no se versionan hasta decidirlo. **No se cambió `.gitignore`** en este cierre.
- El fixture `apps/identidad/fixtures/roles_iniciales.json` existe en la copia local pero está en `.gitignore`: un clon limpio no lo tiene. Trae 7 roles (los de antecedentes los crea la migración `antecedentes/0005_roles_antecedentes`). Decidir si se versiona (ver §10).

### 🔲 Pendiente — Endurecimiento
MFA real, permisos por objeto más finos (hoy el aislamiento es por organización, no aún por instalación/contrato específico), CI/CD, observabilidad, **cifrado de campos sensibles de antecedentes** (marcado explícitamente como pendiente en el modelo `VerificacionAntecedentes`), y el conector `ConectorAsistido` real con Playwright en un worker Celery aislado (hoy falla explícito a propósito, nunca finge funcionar).

### 🔲 Pendiente — API
Todo es HTMX/templates. Los endpoints DRF versionados (`/api/v1/...`) están pendientes si se necesita integración externa.

---

## 9. Reglas operativas (para no repetir errores ya cometidos)

- **Node.js debe ser ≥20.** Si `npm install` falla con `EBADENGINE` o `Cannot find native binding`, lo primero es revisar `node --version`, no reinstalar node_modules a ciegas.
- Después de tocar `static_src/input.css`, siempre `npm run build:css` antes de probar en el navegador — Django no compila Tailwind, solo sirve el archivo ya compilado.
- Después de tocar cualquier `settings/*.py`, **reiniciar el `runserver`** — Django lee settings una sola vez al arrancar.
- Antes de decir "listo", verificar que el archivo realmente existe en disco (`dir` / `type`) — no asumir que un paso anterior se aplicó solo porque se dio la instrucción.
- Cuando se da código para pegar, confirmar explícitamente en qué archivo va y con qué nombre exacto — los mensajes largos con muchos pasos son la causa más común de pasos salteados.
- **Fecha de negocio = `timezone.localdate()`** (zona `America/Guayaquil`). Nunca `timezone.now().date()`, `date.today()` ni `datetime.now()` en código de aplicación. En UTC, después de las 19:00 de Ecuador el día ya es el siguiente: vencimientos, permisos de ingreso, verificaciones de antecedentes y el job diario se evaluaban mal. Las pruebas de fechas también usan `localdate()`: una prueba que compara con `date.today()` falla de forma intermitente en un runner UTC.
- **Columna nueva no nula = `db_default`.** Un `default` solo de Python no basta: las pruebas de migración que insertan con el modelo histórico fallan con NOT NULL. Caso real: `Persona.nacionalidad`, `domicilio` y `telefono_emergencia` llevan `db_default=""`.
- **Excel y zonas horarias.** La librería de Excel no acepta fechas con zona horaria. Los reportes convierten a hora local sin `tzinfo` antes de escribir.
- Después de cambiar variables `SIGECOP_*` del entorno o de `settings/`, reiniciar `runserver` y el worker de Celery. Beat lee el horario al arrancar.

---

## 10. Cómo correr el proyecto desde cero

```bash
# Backend
pip install -r requirements.txt
python manage.py migrate
python manage.py loaddata roles_iniciales   # el fixture no está versionado: en un clon limpio hay que copiarlo aparte (ver §8)
python manage.py createsuperuser

# Frontend (Tailwind)
npm install
npm run build:css      # una vez, o:
npm run watch:css      # mientras desarrollas (recompila solo)

# Arrancar
python manage.py runserver
```

---

## 11. Lo que cambió con cierre-sigecop

Describe lo implementado en la rama `feat/cierre-sigecop`, según el código. Lo que depende de una decisión del director aparece como **Pendiente**. Para el estado de cada entrega, ver §8.

### 11.1 Usuarios internos (T2)
- Pantalla `identidad:usuarios` (`/usuarios/`): lista, invita, reenvía el acceso y desactiva roles. Solo la acción `gestionar_usuarios` (admin_cliente).
- Roles asignables: exactamente `ROLES_INTERNOS`. Nunca roles de portal ni de plataforma.
- Correo nuevo: cuenta `INTERNA` sin contraseña usable y enlace de activación de **72 h** (`PASSWORD_RESET_TIMEOUT`). Usuario interno existente: solo se suma el rol y se envía un aviso, sin enlace. Correo de una cuenta de portal: rechazado.
- Desactivar un rol exige **motivo** (RN-004). Nadie desactiva su propio rol de `admin_cliente`.
- Una persona con varias organizaciones invita a la primera donde tiene permiso (`organizacion_con_permiso`). Selector de organización: **Pendiente P7**. Alcance por instalación: no se ofrece hasta T3, para no prometer un control que no existe.

### 11.2 Job diario, avisos y resumen por correo (T5, T5c)
- Tarea `apps.core.tasks.procesar_vencimientos` (beat) → `apps.cumplimiento.jobs.procesar_vencimientos`. Corre a `SIGECOP_JOB_DIARIO_HORA` (por defecto 6), en hora de `America/Guayaquil`.
- **Avisos previos (RF-033):** un documento aprobado y vigente que vence dentro de un umbral de `SIGECOP_DIAS_AVISO_VENCIMIENTO` (por defecto `30,7`) genera **una** alerta por umbral: el menor umbral que cubre los días restantes. Clave de idempotencia `DOCUMENTO:<id>:POR_VENCER:<umbral>`. Repetir el job no duplica; una renovación vuelve a avisar.
- **Resumen por correo (RF-060, RF-061):** una vez por organización y día, a los usuarios internos activos con `recibir_resumen_alertas`. Sin alertas creadas ese día, o sin destinatarios, no hay correo.
- **Auditoría en dos pasos:** `PROGRAMAR_RESUMEN_ALERTAS` se registra dentro de la transacción, antes de enviar; es la marca que evita un segundo resumen el mismo día. `ENVIAR_RESUMEN_ALERTAS` con resultado `OK` solo se registra si el SMTP aceptó el mensaje. Si falla: evento `ERROR` y alerta `ALTA` `RESUMEN_NO_ENVIADO`.
- **Pendiente P21:** destinatarios (gestor, revisor, empresa contratista) y umbrales definitivos.

### 11.3 Fechas de negocio
Ver §9. Todo «hoy» es `timezone.localdate()`.

### 11.4 Revisión de documentos en lote (T6, T7)
- Bandeja `cumplimiento:bandeja`: columna **Gestión**, que vale «Nuevo» para la versión 1 y «Actualización» para las siguientes. Las casillas aparecen solo en las organizaciones donde el usuario tiene `revisar_documentos`.
- `revisar_documentos_en_lote`: **todo o nada**. Cada documento genera su propia `Revision` y su evento `REVISAR_DOCUMENTO_<DECISION>`. Solo acepta versiones vigentes cuyo requisito está en `EN_REVISION`; una versión vieja o ya resuelta invalida el lote completo.
- Observar y rechazar exigen un **comentario** compartido; aprobar no lo exige.
- **Fecha de emisión** (opcional): se guarda en la `Revision`, porque el documento es inmutable (RN-001). No puede ser futura ni posterior al vencimiento del documento. El formulario de revisión individual también la acepta.
- **«Por hacer»** en el panel: `cumplimiento:por_hacer`, documentos a aprobar agrupados por empresa. Lo sirve `cumplimiento` para que `identidad` no dependa hacia delante.
- **Brecha:** los **cuatro ojos no se aplican a documentos**. `revisar_documento` no impide que quien subió un documento lo apruebe, y `admin_cliente` tiene ambos permisos. **Pendiente de decisión.**

### 11.5 Reportes (T8, T9)
- App `apps/reportes`, la última de la cadena. Lee las apps de dominio y no tiene modelos propios.
- Cuatro reportes en `/reportes/`: **Documentos de trabajadores**, **Información de trabajadores**, **Solicitudes de ingreso** (`/reportes/solicitudes-ingreso/`) e **Histórico de accesos** (`/reportes/historico-accesos/`). Filtros por empresa, texto y fechas, y por planta, estado o tipo donde aplica. Paginado de 50 filas.
- **Ver** un reporte: `ver_registros`. **Descargar Excel** (`*.xlsx`): `exportar_reportes` (solo `admin_cliente`).
- Excel auditado: `ExportacionAuditoria` (actor, filtros, filas, SHA-256) y evento `EXPORTAR_<REPORTE>`. **Brecha:** si el usuario tiene varias organizaciones, `organizacion` de la exportación queda vacía (`exportar_xlsx`).
- **Información de trabajadores** muestra habilitación Sí/No, calculada con `calcular_habilitacion()` en modo lectura. **Nunca muestra el resultado de antecedentes**, a diferencia del sistema anterior (§2.7). El informe de antecedentes queda **Pendiente P15**.
- Fechas y horas en hora local de Ecuador.

### 11.6 Expediente de la persona (T13)
- Ruta `reportes/personas/<id>/expediente/`, con `ver_registros`. Garita recibe 403. Una persona de otra organización recibe 404.
- Línea de tiempo, de la más reciente a la más antigua: asignaciones, documentos cargados, revisiones, permisos de ingreso, verificaciones de antecedentes y eventos de acceso.
- **Antecedentes:** solo aparece «Solicitud de verificación», con su fecha y «En curso» o «Cerrada». Nunca el resultado, el estado detallado, el certificado ni la vigencia (§2.7).
- Solo lectura: no escribe nada.

### 11.7 Evidencia judicial asistida (T12)
- **Qué hace:** la persona operadora busca a la persona en el portal público de la Función Judicial (e-SATJE) con su navegador y carga como evidencia el PDF o la captura (PNG o JPEG). **SIGECOP no consulta ese portal** y no resuelve ningún control antibot.
- **Qué valida:** el formato (firma del archivo y comprobaciones del PDF). **No lee ni interpreta el contenido.** Un archivo ilegible va a cuarentena.
- **Un hallazgo siempre pasa a revisión:** con evidencia aceptada, un caso en `AUTENTICIDAD_PENDIENTE` pasa a `REQUIERE_REVISION`. Nunca se decide solo. Un fallo nunca es favorable.
- **Cuatro ojos:** decide un revisor de antecedentes. Quien cargó la evidencia no puede decidir.
- La evidencia judicial **no cuenta como certificado** y solo la ve el revisor.
- **Pendiente P17 y P18:** qué cuenta como hallazgo y quién lo revisa. El informe de evidencia en reportes queda pendiente.

### 11.8 Campos nuevos de persona (T13)
- `Persona.nacionalidad`, `domicilio` y `telefono_emergencia`: opcionales. Se editan en el formulario de persona y aparecen en el informe de información de trabajadores.
- La «posición» ya existe por empresa como `Asignacion.cargo_o_actividad`. No se añade otro campo.
- Regla: columna nueva no nula con `db_default` (§9).