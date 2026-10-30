# Guía de prueba manual de SIGECOP

Esta guía es para probar SIGECOP sin conocimientos técnicos. Recorre cada rol, indica por dónde entrar, qué debe pasar y qué debe quedar **denegado** (403).

**Convenciones**

- **Pendiente de confirmar**: no se pudo verificar en el código. No es un fallo del sistema; pregunta al desarrollador.
- **403**: la página muestra "acceso denegado" o un error 403, y no aparece ningún dato.
- **Ruta**: dirección a escribir en el navegador, después de `http://localhost:8000`.

---

## 1. Antes de empezar

### 1.1 Arrancar el sistema

Abre PowerShell en la carpeta del proyecto:

```powershell
cd C:\Users\daybu\Downloads\sigecop-saas
npm install
npm run build:css
env\Scripts\python.exe manage.py migrate
env\Scripts\python.exe manage.py runserver
```

- `npm install` solo la primera vez. Necesitas Node.js 20 o superior.
- Si la página sale sin estilos, vuelve a ejecutar `npm run build:css`.
- Deja la ventana abierta mientras pruebas. Para cerrar, pulsa `Ctrl + C`.
- Abre `http://localhost:8000/login/`.

### 1.2 Preparar las cuentas de prueba (una sola vez)

Lo hace quien administra el sistema, con un superusuario:

```powershell
env\Scripts\python.exe manage.py createsuperuser
```

Luego, en `http://localhost:8000/admin/`:

1. Crea una **Organización** llamada `Cliente Prueba`.
2. Crea una **Instalación** llamada `Planta Norte` dentro de esa organización.
3. Crea los **Roles** con estos códigos exactos (en minúsculas):

   `admin_cliente`, `gestor_contratista`, `revisor_documental`, `responsable_area`, `seguridad_garita`, `operador_antecedentes`, `revisor_antecedentes`

   **Pendiente de confirmar:** si el proyecto ya trae estos roles cargados. Si existen, no los dupliques.
4. Crea un **Usuario** por cada rol, con un correo de prueba y una contraseña que tú elijas.
5. Asigna cada rol a su usuario en **Asignaciones de rol**, con la organización `Cliente Prueba` y activo.

> El superusuario entra a `/admin/`, pero **no ve datos de la organización** si no tiene un rol asignado. Las pruebas se hacen con los usuarios de cada rol, no con el superusuario.

### 1.3 Correo

Las invitaciones y los accesos al portal se envían por correo. La configuración está en `/admin/`, sección de configuración de correo (solo superusuarios).

**Pendiente de confirmar:** cómo llega el correo en el entorno de desarrollo. Si no llega, pide al desarrollador el enlace de activación.

### 1.4 Datos de ejemplo (ficticios)

| Dato | Valor de ejemplo |
|---|---|
| Empresa contratista | `Constructora Ejemplo S.A.` |
| RUC de prueba | `1790000000001` (ficticio) |
| Trabajador | `Trabajador Prueba` |
| Cédula de prueba | `0900000001` (ficticia) |
| Requisito del catálogo | `RUC vigente` |

No uses datos reales de personas en las pruebas.

---

## 2. Mapa de menú y rutas

| Menú | Ruta | Permiso que lo exige |
|---|---|---|
| Panel | `/` | Cualquier rol activo. La cuenta de portal se redirige a `/portal/` |
| Empresas | `/empresas/` | Ver: `ver_registros`. Crear y editar: `editar_registros` |
| Personas | `/personas/` | Ver: `ver_registros`. Crear y editar: `editar_registros` |
| Documentos | `/cumplimiento/bandeja/` | Ver: `ver_registros`. Aprobar o rechazar: `revisar_documentos` |
| Catálogo documental | `/cumplimiento/catalogo/` | Ver: `ver_registros`. Crear requisito: `configurar_catalogo` |
| Antecedentes | `/antecedentes/bandeja/` | Solo roles de antecedentes (sus reglas propias) |
| Ingresos | `/accesos/permisos/` | `ver_registros` |
| Garita | `/accesos/garita/` | `garita` |
| Trabajos | `/trabajos/` | `ver_registros` |
| Reportes | `/reportes/` | `ver_registros` |
| Alertas | `/alertas/` | `ver_alertas` |
| Usuarios | `/usuarios/` | `gestionar_usuarios` |
| Descargas Excel de reportes | `/reportes/documentos-trabajadores.xlsx` (y los otros tres reportes con `.xlsx`) | `exportar_reportes` |
| Expediente de persona | `/reportes/personas/<id>/expediente/` (botón **Expediente** en Personas) | `ver_registros` |
| Alertas CSV o XLSX | `/reportes/alertas.csv?formato=csv` o `?formato=xlsx` | `exportar_reportes` |
| Portal de cuentas externas | `/portal/` | Solo cuentas de portal |

El menú solo **oculta** entradas. Lo que decide es el 403: si escribes la ruta directamente, debe denegarse igual.

---

## 3. Escenarios por rol

Para cada escenario: **precondición**, **pasos** y **resultado esperado**. Después de cada bloque aparece lo que debe quedar **denegado**.

### 3.1 Administrador del cliente (`admin_cliente`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| ADM-01 | Sesión como `admin_cliente` | Menú **Empresas** → botón de alta de empresa → llenar datos → guardar | La empresa aparece en la lista |
| ADM-02 | Un correo que no tenga cuenta | Menú **Usuarios** → invitar → correo, nombre y rol interno → enviar | El usuario aparece en la lista. Llega un correo con enlace de activación válido 72 h |
| ADM-03 | Un usuario interno activo | **Usuarios** → desactivar un rol → dejar el motivo vacío → intentar guardar | Lo bloquea y pide el motivo. Con motivo, el rol queda desactivado |
| ADM-04 | Un rol desactivado | **Usuarios** → reenviar acceso | Se envía un nuevo enlace (si el rol está activo) |
| ADM-05 | Sesión como `admin_cliente` | Menú **Catálogo documental** → nuevo requisito (ej. `RUC vigente`) | El requisito aparece en el catálogo |
| ADM-06 | Un reporte con datos | Menú **Reportes** → abrir un reporte → descarga Excel | Se descarga el `.xlsx`. Queda registrada la exportación |
| ADM-07 | Alertas existentes | Menú **Alertas** → exportar CSV o XLSX | Se descarga el archivo |
| ADM-08 | Alertas creadas hoy | Esperar al job diario o ejecutarlo (ver §5) | Llega un único resumen por correo a la organización. **Pendiente de confirmar** la llegada del correo según la configuración |

**Debe quedar DENEGADO (403):**

| Acción | Ruta |
|---|---|
| Entrar a la pantalla de garita | `/accesos/garita/` |
| Abrir la bandeja de antecedentes | `/antecedentes/bandeja/` |

### 3.2 Gestor de contratista (`gestor_contratista`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| GES-01 | Dos empresas en `Cliente Prueba` | Menú **Empresas** → revisar la lista | **Brecha conocida:** ve y edita todas las empresas de la organización, no solo las suyas (AC-002 pendiente, ver §5). Anota el resultado; no es una prueba que el sistema apruebe |
| GES-02 | Empresa con correo de contacto | **Empresas** → enviar acceso | Se envía el enlace de activación del portal de empresa |
| GES-03 | Requisito asignado a una empresa | **Documentos** → **Ver documento** → en el detalle, subir archivo | El documento queda en revisión, con nueva versión |
| GES-04 | Persona con habilitación | **Personas** → botón de solicitar ingreso (en la fila de la persona) → **Ingresos** | La solicitud aparece en `/accesos/permisos/` |
| GES-05 | Empresa existente | **Empresas** → botón de crear permiso de trabajo (en la fila de la empresa) | Se crea el permiso en **Trabajos** |
| GES-06 | Reportes disponibles | **Reportes** → abrir un reporte | Se ve la tabla y los filtros |

**Debe quedar DENEGADO (403):**

| Acción | Ruta o paso |
|---|---|
| Entrar a la pantalla de garita | `/accesos/garita/` |
| Abrir **Usuarios** | `/usuarios/` |
| Descargar un Excel de reportes | `/reportes/documentos-trabajadores.xlsx` |
| Ver la cola de revisión de documentos | `/cumplimiento/por-hacer/` |
| Aprobar, observar o rechazar un documento | Botones de **Documentos** (no deben aparecer); si se fuerza la petición, 403 |
| Abrir o crear requisitos del catálogo | `/cumplimiento/catalogo/` (crear: 403) |
| Abrir la bandeja de antecedentes | `/antecedentes/bandeja/` |

### 3.3 Revisor documental (`revisor_documental`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| REV-01 | Documentos en revisión | **Documentos** → revisar la lista | Aparecen casillas de selección y la columna **Gestión** (**Nuevo** o **Actualización**) |
| REV-02 | Dos documentos en revisión | Marcar dos casillas → poner fecha de emisión (no futura, no posterior al vencimiento) → botón **Aprobado seleccionados** | Los dos quedan aprobados. Cada uno tiene su revisión registrada |
| REV-03 | Documentos en revisión | Marcar uno → botón **Observado seleccionados** o **Rechazado seleccionados** sin comentario | El sistema pide un comentario y no guarda |
| REV-04 | Documento vencido o ya resuelto | Marcarlo junto a uno válido → aprobar | El lote entero se rechaza (todo o nada). Ninguno cambia |
| REV-05 | Panel con documentos pendientes | Abrir el **Panel** | Aparece la lista **Por hacer**, agrupada por empresa |
| REV-06 | Empresas y personas | Abrir **Empresas** y **Personas** | Se ve la lista, sin botones de crear ni editar |

**Debe quedar DENEGADO (403):**

| Acción | Ruta |
|---|---|
| Crear o editar empresas | `/empresas/nueva/` (modal; ver nota en §4) |
| Subir documentos | Ver el detalle de un requisito y subir (no debe permitirse) |
| Descargar un Excel de reportes | `/reportes/informacion-trabajadores.xlsx` |
| Abrir **Usuarios** | `/usuarios/` |
| Entrar a la garita | `/accesos/garita/` |

### 3.4 Responsable de área (`responsable_area`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| RA-01 | Permisos de ingreso existentes | Menú **Ingresos** → **Ver →** en un permiso | Se ve el detalle del permiso |
| RA-02 | Persona habilitada | **Personas** → solicitar ingreso | Se crea la solicitud en **Ingresos** |
| RA-03 | Permiso de trabajo creado | **Trabajos** → abrir el permiso → abrir, suspender, reanudar o cerrar | Cada acción cambia el estado del permiso |
| RA-04 | Empresas | **Empresas** → revisar la lista | Se ve la lista |

**Debe quedar DENEGADO (403):**

| Acción | Ruta |
|---|---|
| Configurar el catálogo | `/cumplimiento/catalogo/nuevo/` (modal; ver nota en §4) |
| Descargar un Excel de reportes | `/reportes/solicitudes-ingreso.xlsx` |
| Abrir **Usuarios** | `/usuarios/` |
| Entrar a la garita | `/accesos/garita/` |
| Abrir la bandeja de antecedentes | `/antecedentes/bandeja/` |
| Editar una empresa | Botón de editar en **Empresas** no debe permitir guardar |

### 3.5 Seguridad de garita (`seguridad_garita`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| GAR-01 | Una persona con permiso o habilitación | Menú **Garita** → buscar a la persona → validar | La pantalla dice si puede entrar (sí o no) y la razón operativa |
| GAR-02 | La misma persona | Registrar entrada y después salida | Se registran en el histórico de accesos (visible en un reporte solo para quien tenga `ver_registros`) |
| GAR-03 | Persona sin habilitación | Consultar en garita | Aparece "no habilitado" con la razón. **No** aparece el resultado de antecedentes ni el certificado |

**Debe quedar DENEGADO (403):**

| Acción | Ruta |
|---|---|
| Ver empresas | `/empresas/` |
| Ver personas | `/personas/` |
| Abrir el expediente de una persona | `/reportes/personas/<id>/expediente/` |
| Ver reportes | `/reportes/` |
| Abrir documentos | `/cumplimiento/bandeja/` |
| Abrir la bandeja de antecedentes | `/antecedentes/bandeja/` |
| Abrir usuarios | `/usuarios/` |

### 3.6 Operador de antecedentes (`operador_antecedentes`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| OPA-01 | Un caso existente | Menú **Antecedentes** → **Abrir caso** | Se ve el caso, sin resultado ni certificado |
| OPA-02 | Persona sin verificación | **Personas** → botón de iniciar antecedentes (formulario en la fila de la persona) | Se crea la verificación |
| OPA-03 | Caso con autorización | Registrar la autorización (botón de autorizar en el detalle) | La autorización queda registrada |
| OPA-04 | Caso en consulta asistida | Cargar evidencia de la Función Judicial (PDF, PNG o JPEG) | Si el caso está en «Autenticidad pendiente», la evidencia se acepta y el caso pasa a «Requiere revisión». Tú no decides el resultado |
| OPA-05 | Acceso a reportes | **Reportes** → **Expediente** de una persona | Ves la fecha de la verificación y "En curso" o "Cerrada". **Nunca** el resultado |

**Debe quedar DENEGADO (403):**

| Acción | Ruta o paso |
|---|---|
| Descargar un certificado | `/antecedentes/documentos/<id>/descargar/` |
| Validar un caso (decidir el resultado) | Botones de decisión del detalle no deben permitirse; el sistema lo rechaza |
| Descargar un Excel de reportes | `/reportes/historico-accesos.xlsx` |
| Entrar a la garita | `/accesos/garita/` |
| Abrir usuarios | `/usuarios/` |

### 3.7 Revisor de antecedentes (`revisor_antecedentes`)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| REVA-01 | Caso cargado por otra persona | **Antecedentes** → **Abrir caso** | Se ve el resultado y el estado |
| REVA-02 | Caso con certificado | Descargar el certificado desde el detalle | Se descarga el PDF. La descarga queda registrada |
| REVA-03 | Caso con evidencia judicial | Revisar la evidencia (botón **Descargar evidencia**) y decidir | Se puede decidir. Quien cargó la evidencia no puede decidir sobre ella |
| REVA-04 | Caso que tú mismo cargaste | Intentar validar | Se rechaza. Cuatro ojos: no puedes decidir sobre tu propia carga |

**Debe quedar DENEGADO (403):**

| Acción | Ruta |
|---|---|
| Entrar a la garita | `/accesos/garita/` |
| Abrir usuarios | `/usuarios/` |
| Descargar un Excel de reportes | `/reportes/documentos-trabajadores.xlsx` |
| Crear o editar empresas | Modal de **Empresas** (no debe guardar) |

### 3.8 Portal de empresa contratista (cuenta de portal)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| PEM-01 | Empresa con correo y enlace de activación | Abrir el enlace (válido 72 h) → crear contraseña | Se activa la cuenta y se entra |
| PEM-02 | Cuenta activada | Entrar en `/login/` con el RUC (o el correo) y la contraseña | Llega a `/portal/empresa/` |
| PEM-03 | Enlace vencido | Abrir el enlace después de 72 h | **Pendiente de confirmar** el mensaje exacto; no permite entrar. El gestor o el admin reenvía el acceso desde **Empresas** |

**Debe quedar DENEGADO (403):** cualquier ruta interna, por ejemplo `/empresas/`, `/personas/`, `/reportes/`, `/antecedentes/bandeja/`, `/usuarios/` o `/accesos/garita/`. Para una cuenta de portal, `/` redirige a `/portal/`.

### 3.9 Portal de persona (cuenta de portal)

| ID | Precondición | Pasos | Resultado esperado |
|---|---|---|---|
| PPE-01 | Persona con correo y acceso enviado | Abrir el enlace de activación → crear contraseña | Se activa la cuenta |
| PPE-02 | Cuenta activada | Entrar en `/login/` con la cédula (o el correo) y la contraseña | Llega a `/portal/persona/` |

**Debe quedar DENEGADO (403):** cualquier ruta interna, incluido el expediente `/reportes/personas/<id>/expediente/`.

---

## 4. Denegaciones y pantallas modales

Los formularios que se abren en ventana (modales) se piden por HTMX. **Pendiente de confirmar:** cuando se intenta abrir uno sin permiso, si la respuesta es 403 o una redirección. El criterio de aprobación es el mismo: el formulario no se abre y no se guarda nada.

---

## 5. Job diario de vencimientos (opcional)

El job corre todos los días a la hora que fija `SIGECOP_JOB_DIARIO_HORA` (por defecto 06:00, hora de Ecuador). Para probarlo sin esperar, ejecuta en la carpeta del proyecto:

```powershell
env\Scripts\python.exe manage.py shell -c "from apps.cumplimiento.jobs import procesar_vencimientos; print(procesar_vencimientos())"
```

- **Resultado esperado:** imprime un resumen con los contadores (documentos, avisos previos, resúmenes enviados).
- **Repetir el comando el mismo día:** no crea alertas nuevas ni envía un segundo correo. Los contadores de avisos y resúmenes deben ser 0.

---

## 6. Lista de comprobación (AC-001 a AC-010)

Texto literal de `doc/Requerimientos.docx`. La columna «Verificación automática» indica lo que ya comprobó la verificación independiente de T14 (pruebas del repositorio); la prueba manual confirma el comportamiento en pantalla.

| AC | Criterio | Escenarios | Verificación automática | Estado manual |
|---|---|---|---|---|
| AC-001 | Un requisito vencido recalcula el estado y bloquea permisos futuros según la regla. | — | Cumple (`cumplimiento/tests.py`, `accesos/tests.py`) | Por probar |
| AC-002 | Un gestor no puede ver ni modificar otra empresa contratista. | GES-01 | **No cumple** (brecha T3, pregunta P2) | **No cumple** |
| AC-003 | Garita ve habilitación y razón operativa sin abrir antecedentes. | GAR-01, GAR-03 | Cumple | Por probar |
| AC-004 | Una observación conserva archivo, versión, decisión, actor y motivo. | — | Cumple | Por probar |
| AC-005 | Dos solicitudes con la misma clave no generan doble consulta. | — | Cumple (solo automática) | No aplica |
| AC-006 | Un CAPTCHA deja el caso pendiente y reanudable, nunca favorable. | — | Cumple | Por probar |
| AC-007 | Una identidad diferente en el PDF fuerza revisión. | — | Cumple | Por probar |
| AC-008 | Un PDF vencido, cifrado, dañado o sin texto no se acepta automáticamente. | — | Cumple (el caso «vencido» por lógica de vigencia, sin prueba con ese nombre) | Por probar |
| AC-009 | La ausencia de API oficial no impide la carga manual validada. | — | Cumple | Por probar |
| AC-010 | Toda lectura o exportación sensible genera auditoría. | — | Cumple (falta registrar la finalidad de la exportación, RN-009, pregunta pendiente) | Por probar |

### Comprobaciones generales (marca cuando las pruebes)

- [ ] `admin_cliente`: los escenarios ADM-01 a ADM-08 pasan.
- [ ] `gestor_contratista`: GES-02 a GES-06 pasan y las denegaciones de §3.2 se cumplen.
- [ ] `revisor_documental`: REV-01 a REV-06 pasan y las denegaciones de §3.3 se cumplen.
- [ ] `responsable_area`: RA-01 a RA-04 pasan y las denegaciones de §3.4 se cumplen.
- [ ] `seguridad_garita`: GAR-01 a GAR-03 pasan y ninguna denegación de §3.5 se abre.
- [ ] `operador_antecedentes`: OPA-01 a OPA-05 pasan; el resultado nunca aparece.
- [ ] `revisor_antecedentes`: REVA-01 a REVA-04 pasan; la autovalidación se rechaza.
- [ ] Portal empresa y persona: entran solo a su portal; ninguna ruta interna se abre.
- [ ] El job diario, ejecutado dos veces el mismo día, no duplica nada.

---

## 7. Lo que no es un fallo (ya conocido)

| Comportamiento | Motivo |
|---|---|
| El gestor ve todas las empresas de su organización | Pendiente de decisión P2 (T3). Ver AC-002 |
| SIGECOP aprueba un ingreso sin una persona | Pendiente de decisión P9 (T4) |
| Quien sube un documento puede aprobarlo | Cuatro ojos no aplica a documentos (pendiente de decisión) |
| El resumen de alertas solo llega a `admin_cliente` | Pendiente de decisión P21 |
| No hay informe de antecedentes | Pendiente de decisión P15 |
| No hay cuadrillas ni visitas administrativas | Pendiente de decisiones P9 y P10 (T10 y T11) |
