# feat(antecedentes): dedicated roles, four-eyes review and read audit

Rama: `pr/b3-antecedentes-roles` → base `pr/b2-antecedentes-autorizacion`

## Chain Context

| Campo | Valor |
|-------|-------|
| Cadena | SIGECOP: cuentas de portal + antecedentes penales |
| Tracker PR | `feat/sigecop-contratos` → `main` (borrador, no fusionar) |
| Posición | 7 de 13 |
| Base | `pr/b2-antecedentes-autorizacion` |
| Depende de | pr/b2-antecedentes-autorizacion |
| Siguiente | Consulta asistida (B4). |

### Chain Overview

```text
main
 └── feat/sigecop-contratos  (tracker)
    1. pr/a1-cuentas-identidad
    2. pr/a2-cuentas-alta
    3. pr/a3-portal-lectura
    4. pr/a4-smtp-cuentas-compartidas
    5. pr/b1-antecedentes-estados
    6. pr/b2-antecedentes-autorizacion
📍  7. pr/b3-antecedentes-roles
    8. pr/b4-antecedentes-consulta-asistida
    9. pr/b5-antecedentes-pdf
   10. pr/b6-antecedentes-api-ui
   11. pr/b7-antecedentes-vigencia
   12. pr/b8-antecedentes-correcciones
   13. pr/b9-antecedentes-certificado-real
```

### Scope
- Incluye: Roles operador/revisor, cuatro ojos, auditoría de toda lectura (ANT-011/013).
- Excluye: lo de los demás tramos; Función Judicial, firma criptográfica y cifrado de campos (T9).

### Verificación
- `env/Scripts/python.exe manage.py test` y `manage.py makemigrations --check --dry-run` en esta rama.
- Detalle y evidencia: `odd/tasks/cuentas-acceso-portal.md` y `odd/tasks/antecedentes.md`.
