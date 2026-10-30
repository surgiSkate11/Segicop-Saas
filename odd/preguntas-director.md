# Preguntas para el director — SIGECOP

Fecha: 27/10/2026. Cada pregunta indica qué parte del desarrollo bloquea y qué hace el sistema mientras no haya respuesta. Mientras tanto, todo lo no confirmado queda **denegado**: no se habilita ningún permiso por defecto.

## A. Registro de empresas y usuarios

**P1. ¿Quién registra una empresa contratista?**
Opciones: (a) solo el personal interno (administrador del cliente o gestor); (b) la empresa se registra sola desde un formulario público y luego se aprueba; (c) ambas.
Hoy: solo el personal interno, opción (a).

**P2. El "Gestor de contratista", ¿es personal interno del cliente o es personal de la empresa contratista?**
Los requerimientos dicen "Solo accede a empresas bajo su responsabilidad". ¿Quién le asigna esas empresas y puede tener más de una?
Bloquea: día 3 (alcance por empresa).

**P3. ¿Qué módulos ve una empresa contratista recién registrada cuando entra con sus credenciales?**
Hoy ve, en solo lectura, su estado, sus requisitos y sus trabajadores. El rol "Contratista" de los requerimientos dice: "Solo debe subir los requisitos necesarios para su validación de ingreso". Para confirmar:
- ¿Sube sus propios documentos desde el portal?
- ¿Registra a sus trabajadores?
- ¿Crea solicitudes de ingreso o de visita?
- ¿Ve el estado de antecedentes de sus trabajadores? Recomendación: **no**, solo "habilitado" o "no habilitado".

**P4. ¿Qué ve un trabajador (persona) que recibe sus credenciales?**
Hoy ve, en solo lectura, su estado de habilitación y un espacio reservado para el código QR. ¿Debe subir documentos, ver sus cursos o mostrar el QR en garita?

## B. Roles y permisos

**P5. Confirme o corrija la matriz propuesta.**
Se derivó solo de la tabla de roles de los requerimientos. ✅ = permitido, — = denegado.

| Acción | Admin cliente | Gestor contratista | Revisor documental | Responsable de área | Garita | Operador antecedentes | Revisor antecedentes |
|---|---|---|---|---|---|---|---|
| Ver empresas y personas | ✅ | ✅ (sus empresas) | ✅ | ✅ | — | ✅ | ✅ |
| Crear/editar empresas y personas | ✅ | ✅ | — | — | — | — | — |
| Enviar credenciales a empresa/persona | ✅ | ✅ | — | — | — | — | — |
| Configurar catálogo de requisitos | ✅ | — | — | — | — | — | — |
| Asignar requisitos | ✅ | ✅ | — | — | — | — | — |
| Subir documentos | ✅ | ✅ | — | — | — | — | — |
| Aprobar/observar/rechazar documentos | ✅ | — | ✅ | — | — | — | — |
| Solicitar ingreso | ✅ | ✅ | — | ✅ | — | — | — |
| Aprobar/rechazar ingreso | ✅ | — | — | ✅ | — | — | — |
| Crear permiso de trabajo | ✅ | ✅ | — | ✅ | — | — | — |
| Aprobar/abrir/suspender/cerrar trabajo | ✅ | — | — | ✅ | — | — | — |
| Garita: validar y registrar entrada/salida | — | — | — | — | ✅ | — | — |
| Operar antecedentes (sin ver el resultado) | — | — | — | — | — | ✅ | ✅ |
| Ver resultado y decidir antecedentes | — | — | — | — | — | — | ✅ |
| Ver alertas | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| Exportar reportes | ✅ | — | — | — | — | — | — |
| Gestionar usuarios internos y roles | ✅ | — | — | — | — | — | — |
| Recibir el resumen diario de alertas por correo | ✅ | — | — | — | — | — | — |

Para confirmar en particular: ¿el administrador del cliente aprueba documentos, o solo lo hace el revisor documental? ¿Quién exporta reportes?

**P6. ¿Quién asigna los roles?**
Propuesta: el administrador de plataforma crea la organización y su primer administrador del cliente; desde ahí, el administrador del cliente invita y asigna los roles de su organización. ¿Es correcto?

**P7. ¿Un usuario puede tener varios roles a la vez?**
Por ejemplo, revisor documental y responsable de área. El modelo lo permite; falta confirmarlo como regla.

## C. Primer inicio de sesión

**P8. ¿Qué pasa cuando un usuario entra por primera vez?**
Hoy recibe un enlace de un solo uso válido 72 horas, crea su contraseña y entra. Para confirmar:
- ¿Debe aceptar un aviso de privacidad o términos? Si es así, ¿con qué texto?
- ¿Debe completar un perfil?
- ¿Debe activar MFA? Los requerimientos (RF-001) lo exigen para administradores y revisores.
- ¿Qué hacer si el enlace vence? Propuesta: el administrador reenvía el acceso.

## D. Procesos del sistema anterior

**P9. Aprobación de ingresos.**
Hoy SIGECOP aprueba sola una solicitud si la persona está habilitada. El sistema anterior mostraba "Pendiente de aprobación". ¿Siempre debe aprobar una persona? Si es así, ¿quién: el anfitrión de la empresa cliente o el responsable de área?

**P10. Solicitud de ingreso con cuadrilla.**
El sistema anterior agrupaba varios trabajadores en una solicitud, con orden de trabajo, anfitrión, actividades, herramientas y equipos, tipo de trabajo, emergencia y "Sin documentos". Para confirmar:
- ¿Se conservan todos esos campos?
- ¿Qué significa "Sin documentos" y quién puede usarlo?
- ¿Qué cambia cuando es una emergencia?

**P11. Visitas administrativas.**
Los visitantes que no son trabajadores de una contratista, ¿necesitan antecedentes o documentos? ¿Solo se registran y se validan en garita?

**P12. Cursos y evaluaciones.**
¿Se mantienen el módulo de cursos (calificación, emisión, caducidad) y las preguntas y parámetros de evaluación? ¿Bloquean el ingreso si están vencidos o reprobados?

**P13. Alcocheck y Faltas.**
¿Qué registran? ¿Un resultado positivo o una falta bloquean el ingreso, y por cuánto tiempo?

**P14. Comunicados generales.**
¿Son avisos para empresas contratistas, para usuarios internos o para ambos? ¿Se envían también por correo?

**P15. Informes.**
¿Qué formato deben tener "Descargar informe" (¿PDF?) y "Descargar a Excel"? ¿Quién puede descargarlos?

## E. Antecedentes y legal

**P16.** Antes de producción, ¿confirma la base legal y la finalidad para pedir el certificado de antecedentes penales? El propio certificado cita el Decreto 1166, que prohíbe exigirlo como requisito.

**P17.** ¿Autoriza que tramitemos el acceso oficial (oficio a la DINARP o convenio con el Consejo de la Judicatura) o que evaluemos un proveedor privado? Sin una de las dos, la consulta sigue siendo asistida, con un paso humano por el CAPTCHA.

**P18.** Función Judicial: ¿qué cuenta como hallazgo (cualquier proceso, solo penales o solo sentencias)? ¿Quién lo revisa?

**P19.** Revocar el consentimiento, ¿debe anular una verificación ya `VALIDADA`?

**P21. Avisos y correos de vencimiento.**
Hoy, cada día a las 06:00, el sistema crea alertas a los 30 y a los 7 días antes de que venza un documento aprobado, y envía un resumen por correo solo al administrador del cliente. Para confirmar:
- ¿Con cuántos días de anticipación se avisa?
- ¿Quién más recibe el correo (gestor, revisor, la propia empresa contratista)?
- ¿Se avisa también a la contratista cuando un documento suyo es observado o rechazado?

## F. Operación

**P20.** ¿Cuál es el servidor de producción, cuántas empresas, personas e ingresos por día se esperan y quién provee el SMTP?
